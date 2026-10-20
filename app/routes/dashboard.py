
import secrets
from pathlib import Path

from flask import Blueprint, request, jsonify, render_template, g, current_app
from werkzeug.utils import secure_filename

from ..extensions import db
from ..models import (
    Asset,
    AssetVersion,
    AssetUsage,
    Website,
    ApiKey,
    AuditLog,
)
from ..security import require_role, hash_key
from ..audit import audit


bp = Blueprint("dashboard", __name__)


# ---------------------------------------------------------
# HTML PAGES
# ---------------------------------------------------------

@bp.get("/")
def dashboard_page():
    return render_template("dashboard.html")


@bp.get("/assets")
def assets_page():
    return render_template("assets.html")


@bp.get("/upload")
def upload_page():
    return render_template("upload.html")


@bp.get("/assets/<int:asset_id>")
def asset_detail_page(asset_id):
    return render_template("asset_detail.html")


@bp.get("/websites")
def websites_page():
    return render_template("websites.html")


@bp.get("/settings")
def settings_page():
    return render_template("settings.html")


# ---------------------------------------------------------
# ASSET HELPERS
# ---------------------------------------------------------

def asset_dict(asset):
    return {
        "id": asset.id,
        "publicId": asset.public_id,
        "name": asset.name,
        "type": asset.type,
        "tags": asset.tags or [],
        "currentVersionId": asset.current_version_id,
    }


def find_asset(asset_id):
    """Only return active assets belonging to the current user's organization."""
    return Asset.query.filter_by(
        id=asset_id,
        org_id=g.user.org_id,
        deleted=False,
    ).first()


# ---------------------------------------------------------
# UPLOAD ASSET
# ---------------------------------------------------------

@bp.post("/api/assets/upload")
@require_role("editor")
def upload_asset():
    file = request.files.get("file")
    name = request.form.get("name", "").strip()
    raw_tags = request.form.get("tags", "")

    if not file or not file.filename:
        return jsonify(
            success=False,
            message="Please select a file.",
        ), 400

    if not name:
        return jsonify(
            success=False,
            message="Asset name is required.",
        ), 400

    if len(name) > 200:
        return jsonify(
            success=False,
            message="Asset name cannot exceed 200 characters.",
        ), 400

    allowed_extensions = {
        "png", "jpg", "jpeg", "gif", "webp",
        "mp4", "webm", "mov",
        "pdf", "doc", "docx", "ppt", "pptx", "txt",
    }

    safe_filename = secure_filename(file.filename)

    if not safe_filename or "." not in safe_filename:
        return jsonify(
            success=False,
            message="Invalid filename.",
        ), 400

    extension = safe_filename.rsplit(".", 1)[1].lower()

    if extension not in allowed_extensions:
        return jsonify(
            success=False,
            message="This file type is not supported.",
        ), 400

    file.stream.seek(0, 2)
    size_bytes = file.stream.tell()
    file.stream.seek(0)

    max_file_size = 50 * 1024 * 1024

    if size_bytes > max_file_size:
        return jsonify(
            success=False,
            message="File exceeds the 50 MB limit.",
        ), 413

    tags = []
    for item in raw_tags.split(","):
        tag = item.strip()
        if tag and tag not in tags:
            tags.append(tag)

    org_id = g.user.org_id
    saved_path = None

    try:
        # public_id is required by the database.
        asset = Asset(
            org_id=org_id,
            public_id=secrets.token_urlsafe(16),
            name=name,
            type=extension,
            tags=tags,
            deleted=False,
        )

        db.session.add(asset)
        db.session.flush()

        uploads_root = (
            Path(current_app.root_path).resolve().parent / "uploads"
        )

        asset_directory = uploads_root / str(org_id) / str(asset.id)
        asset_directory.mkdir(parents=True, exist_ok=True)

        stored_filename = secrets.token_hex(16) + "." + extension
        saved_path = asset_directory / stored_filename

        file.save(str(saved_path))

        storage_key = saved_path.relative_to(
            uploads_root
        ).as_posix()

        version = AssetVersion(
            asset_id=asset.id,
            version_number=1,
            storage_key=storage_key,
            mime_type=file.mimetype or "application/octet-stream",
            size_bytes=size_bytes,
        )

        db.session.add(version)
        db.session.flush()

        asset.current_version_id = version.id

        audit(
            org_id,
            f"user:{g.user.id}",
            "asset.uploaded",
            asset.id,
        )

        db.session.commit()

        return jsonify(
            success=True,
            message="Asset uploaded successfully.",
            asset=asset_dict(asset),
        ), 201

    except Exception:
        db.session.rollback()

        if saved_path is not None:
            try:
                if saved_path.exists():
                    saved_path.unlink()
            except OSError:
                current_app.logger.exception(
                    "Could not remove incomplete upload"
                )

        current_app.logger.exception("Failed to upload asset")

        return jsonify(
            success=False,
            message="Upload failed. Check the Flask terminal for details.",
        ), 500


# ---------------------------------------------------------
# LIST ASSETS
# ---------------------------------------------------------

@bp.get("/api/assets")
@require_role("viewer")
def list_assets():
    query = Asset.query.filter_by(
        org_id=g.user.org_id,
        deleted=False,
    )

    search_query = request.args.get("q", "").strip()
    tag = request.args.get("tag", "").strip()

    if search_query:
        query = query.filter(
            Asset.name.ilike(f"%{search_query}%")
        )

    if tag:
        query = query.filter(Asset.tags.any(tag))

    assets = query.order_by(Asset.id.desc()).all()

    return jsonify([asset_dict(asset) for asset in assets])


# ---------------------------------------------------------
# GET ASSET DETAILS
# ---------------------------------------------------------

@bp.get("/api/assets/<int:asset_id>")
@require_role("viewer")
def get_asset(asset_id):
    asset = find_asset(asset_id)

    if not asset:
        return jsonify(
            error="not_found",
            message="Asset not found.",
        ), 404

    versions = (
        AssetVersion.query
        .filter_by(asset_id=asset.id)
        .order_by(AssetVersion.version_number.desc())
        .all()
    )

    usages = (
        db.session.query(
            Website.name,
            AssetUsage.page_url,
            AssetUsage.last_seen,
        )
        .join(
            Website,
            Website.id == AssetUsage.website_id,
        )
        .filter(
            AssetUsage.asset_id == asset.id,
            Website.org_id == g.user.org_id,
        )
        .all()
    )

    return jsonify(
        asset=asset_dict(asset),
        versions=[
            {
                "id": version.id,
                "version": version.version_number,
                "storageKey": version.storage_key,
                "mimeType": version.mime_type,
                "sizeBytes": version.size_bytes,
                "createdAt": (
                    version.created_at.isoformat()
                    if version.created_at
                    else None
                ),
            }
            for version in versions
        ],
        usages=[
            {
                "website": usage[0],
                "pageUrl": usage[1],
                "lastSeen": (
                    usage[2].isoformat()
                    if usage[2]
                    else None
                ),
            }
            for usage in usages
        ],
    )


# ---------------------------------------------------------
# EDIT ASSET NAME AND TAGS
# ---------------------------------------------------------

@bp.patch("/api/assets/<int:asset_id>")
@require_role("editor")
def update_asset(asset_id):
    asset = find_asset(asset_id)

    if not asset:
        return jsonify(
            error="not_found",
            message="Asset not found.",
        ), 404

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify(
            error="validation_error",
            message="Send a valid JSON object.",
        ), 400

    allowed_fields = {"name", "tags"}

    if not set(data.keys()).issubset(allowed_fields):
        return jsonify(
            error="validation_error",
            message="Only the asset name and tags can be edited.",
        ), 400

    if not data:
        return jsonify(
            error="validation_error",
            message="Provide a name or tags to update.",
        ), 400

    if "name" in data:
        name = data["name"]

        if not isinstance(name, str):
            return jsonify(
                error="validation_error",
                message="Asset name must be text.",
            ), 400

        name = name.strip()

        if not name:
            return jsonify(
                error="validation_error",
                message="Asset name cannot be empty.",
            ), 400

        if len(name) > 200:
            return jsonify(
                error="validation_error",
                message="Asset name cannot exceed 200 characters.",
            ), 400

        asset.name = name

    if "tags" in data:
        tags = data["tags"]

        if isinstance(tags, str):
            tags = tags.split(",")
        elif not isinstance(tags, list):
            return jsonify(
                error="validation_error",
                message="Tags must be a list or comma-separated text.",
            ), 400

        cleaned_tags = []

        for tag in tags:
            if not isinstance(tag, str):
                return jsonify(
                    error="validation_error",
                    message="Every tag must be text.",
                ), 400

            tag = tag.strip()

            if tag and tag not in cleaned_tags:
                cleaned_tags.append(tag)

        asset.tags = cleaned_tags

    try:
        db.session.flush()

        audit(
            g.user.org_id,
            f"user:{g.user.id}",
            "asset.updated",
            asset.id,
        )

        db.session.commit()

        return jsonify(
            success=True,
            message="Asset updated successfully.",
            asset=asset_dict(asset),
        ), 200

    except Exception:
        db.session.rollback()

        current_app.logger.exception(
            "Failed to update asset %s",
            asset_id,
        )

        return jsonify(
            error="update_failed",
            message="Unable to update this asset.",
        ), 500


# ---------------------------------------------------------
# REMOVE ASSET
# ---------------------------------------------------------

@bp.delete("/api/assets/<int:asset_id>")
@require_role("editor")
def remove_asset(asset_id):
    asset = find_asset(asset_id)

    if not asset:
        return jsonify(
            error="not_found",
            message="Asset not found.",
        ), 404

    try:
        asset.deleted = True
        db.session.flush()

        audit(
            g.user.org_id,
            f"user:{g.user.id}",
            "asset.removed",
            asset.id,
        )

        db.session.commit()

        return jsonify(
            success=True,
            message="Asset removed successfully.",
        ), 200

    except Exception:
        db.session.rollback()

        current_app.logger.exception(
            "Failed to remove asset %s",
            asset_id,
        )

        return jsonify(
            error="remove_failed",
            message="Unable to remove this asset.",
        ), 500


# ---------------------------------------------------------
# WEBSITE HELPERS
# ---------------------------------------------------------

def website_dict(site):
    keys = (
        ApiKey.query
        .filter_by(website_id=site.id)
        .order_by(ApiKey.id.desc())
        .all()
    )

    return {
        "id": site.id,
        "name": site.name,
        "domain": site.domain,
        "webhookUrl": site.webhook_url,
        "createdAt": (
            site.created_at.isoformat()
            if site.created_at
            else None
        ),
        "apiKeys": [
            {
                "id": key.id,
                "prefix": key.prefix,
                "revoked": key.revoked,
                "lastUsedAt": (
                    key.last_used_at.isoformat()
                    if key.last_used_at
                    else None
                ),
                "createdAt": (
                    key.created_at.isoformat()
                    if key.created_at
                    else None
                ),
            }
            for key in keys
        ],
    }


# ---------------------------------------------------------
# LIST WEBSITES
# ---------------------------------------------------------

@bp.get("/api/websites")
@require_role("viewer")
def list_websites():
    sites = (
        Website.query
        .filter_by(org_id=g.user.org_id)
        .order_by(Website.id.desc())
        .all()
    )

    return jsonify([website_dict(site) for site in sites])


# ---------------------------------------------------------
# CREATE WEBSITE
# ---------------------------------------------------------

@bp.post("/api/websites")
@require_role("admin")
def create_website():
    data = request.get_json(silent=True) or {}

    name = str(data.get("name", "")).strip()
    domain = str(data.get("domain", "")).strip()
    webhook_url = str(data.get("webhookUrl", "")).strip() or None

    if not name or not domain:
        return jsonify(
            error="validation_error",
            message="Website name and domain are required.",
        ), 400

    if any(char.isspace() for char in domain) or "/" in domain:
        return jsonify(
            error="validation_error",
            message="Enter a domain such as example.com, without a path.",
        ), 400

    site = Website(
        org_id=g.user.org_id,
        name=name,
        domain=domain,
        webhook_url=webhook_url,
    )

    try:
        db.session.add(site)
        db.session.flush()

        audit(
            g.user.org_id,
            f"user:{g.user.id}",
            "website.created",
            site.id,
        )

        db.session.commit()

        return jsonify(website=website_dict(site)), 201

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to create website")

        return jsonify(
            error="create_failed",
            message="Unable to create this website.",
        ), 500


# ---------------------------------------------------------
# CREATE API KEY
# ---------------------------------------------------------

@bp.post("/api/websites/<int:website_id>/keys")
@require_role("admin")
def create_key(website_id):
    site = Website.query.filter_by(
        id=website_id,
        org_id=g.user.org_id,
    ).first()

    if not site:
        return jsonify(error="not_found"), 404

    key = "dam_live_" + secrets.token_urlsafe(24)

    api_key = ApiKey(
        website_id=site.id,
        prefix=key[:12],
        key_hash=hash_key(key),
    )

    try:
        db.session.add(api_key)
        db.session.flush()

        audit(
            g.user.org_id,
            f"user:{g.user.id}",
            "apikey.created",
            site.id,
        )

        db.session.commit()

        return jsonify(
            apiKey=key,
            prefix=api_key.prefix,
            note="Copy and store this key now. It cannot be retrieved later.",
        ), 201

    except Exception:
        db.session.rollback()

        current_app.logger.exception(
            "Failed to create API key for website %s",
            website_id,
        )

        return jsonify(
            error="create_failed",
            message="Unable to create an API key.",
        ), 500


# ---------------------------------------------------------
# REVOKE API KEY
# ---------------------------------------------------------

@bp.post(
    "/api/websites/<int:website_id>/keys/<int:key_id>/revoke"
)
@require_role("admin")
def revoke_key(website_id, key_id):
    site = Website.query.filter_by(
        id=website_id,
        org_id=g.user.org_id,
    ).first()

    if not site:
        return jsonify(error="not_found"), 404

    api_key = ApiKey.query.filter_by(
        id=key_id,
        website_id=site.id,
    ).first()

    if not api_key:
        return jsonify(error="key_not_found"), 404

    if api_key.revoked:
        return jsonify(
            message="API key is already revoked."
        ), 200

    try:
        api_key.revoked = True

        audit(
            g.user.org_id,
            f"user:{g.user.id}",
            "apikey.revoked",
            site.id,
        )

        db.session.commit()

        return jsonify(
            message="API key revoked successfully."
        ), 200

    except Exception:
        db.session.rollback()

        current_app.logger.exception(
            "Failed to revoke API key %s",
            key_id,
        )

        return jsonify(
            error="revoke_failed",
            message="Unable to revoke this API key.",
        ), 500


# ---------------------------------------------------------
# AUDIT LOG
# ---------------------------------------------------------

@bp.get("/api/audit")
@require_role("admin")
def audit_list():
    rows = (
        AuditLog.query
        .filter_by(org_id=g.user.org_id)
        .order_by(AuditLog.id.desc())
        .limit(100)
        .all()
    )

    return jsonify([
        {
            "id": row.id,
            "actor": row.actor,
            "action": row.action,
            "entityId": row.entity_id,
            "details": row.details,
            "createdAt": (
                row.created_at.isoformat()
                if row.created_at
                else None
            ),
        }
        for row in rows
    ])
