
import os
import secrets
import uuid
from pathlib import Path

from botocore.exceptions import ClientError
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
from ..storage import (
    asset_url_for,
    build_storage_key,
    generate_presigned_upload_url,
    inspect_s3_object_info,
    is_cloud_storage_enabled,
)


bp = Blueprint("dashboard", __name__)


# ---------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------

IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
VIDEO_EXTENSIONS = {"mp4", "webm", "mov"}
DOCUMENT_EXTENSIONS = {"pdf", "doc", "docx", "ppt", "pptx", "txt"}
ALLOWED_UPLOAD_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | DOCUMENT_EXTENSIONS

MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50 MB


def classify_upload(filename):
    """Return the asset type for an allowed filename, or None if not allowed."""
    if not isinstance(filename, str):
        return None
    base = os.path.basename(filename.replace("\\", "/"))
    extension = os.path.splitext(base)[1].lstrip(".").lower()
    if extension not in ALLOWED_UPLOAD_EXTENSIONS:
        return None
    if extension in IMAGE_EXTENSIONS:
        return "image"
    if extension in VIDEO_EXTENSIONS:
        return "video"
    return "document"


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
# CLOUD UPLOAD (create asset -> presign -> confirm -> replace)
# ---------------------------------------------------------

@bp.post("/api/assets")
@require_role("editor")
def create_asset():
    data = request.get_json(silent=True) or {}

    name = str(data.get("name", "")).strip()
    if not name or len(name) > 200:
        return jsonify(
            error="validation_error",
            message="Asset name is required (200 characters or fewer).",
        ), 400

    asset_type = classify_upload(data.get("filename"))
    if asset_type is None:
        return jsonify(
            error="unsupported_file_type",
            message="This file type is not allowed.",
        ), 400

    raw_tags = data.get("tags", [])
    if isinstance(raw_tags, str):
        raw_tags = raw_tags.split(",")
    if not isinstance(raw_tags, list):
        return jsonify(
            error="validation_error",
            message="Tags must be a list or a comma-separated string.",
        ), 400
    tags = list(dict.fromkeys(
        str(tag).strip() for tag in raw_tags if str(tag).strip()
    ))

    asset = Asset(
        org_id=g.user.org_id,
        public_id=f"ast_{uuid.uuid4().hex}",
        name=name,
        type=asset_type,
        tags=tags,
        deleted=False,
    )
    db.session.add(asset)
    db.session.flush()

    # audit() commits the transaction
    audit(g.user.org_id, f"user:{g.user.id}", "asset.created", asset.id)

    return jsonify(
        message="Asset created. Now upload its file.",
        asset=asset_dict(asset),
    ), 201


@bp.post("/api/assets/presign-upload")
@require_role("editor")
def presign_upload():
    if not is_cloud_storage_enabled():
        return jsonify(error="cloud storage not configured"), 503

    data = request.get_json() or {}
    asset_id = data.get("assetId")
    if not isinstance(asset_id, int) or isinstance(asset_id, bool):
        return jsonify(error="assetId must be an integer"), 400
    asset = Asset.query.filter_by(
        id=asset_id,
        org_id=g.user.org_id,
        deleted=False,
    ).first()
    if not asset:
        return jsonify(error="not_found"), 404

    filename = data.get("filename") or "upload.bin"
    content_type = data.get("contentType") or "application/octet-stream"
    if not isinstance(filename, str) or not isinstance(content_type, str):
        return jsonify(error="filename and contentType must be strings"), 400
    if classify_upload(filename) is None:
        return jsonify(error="unsupported_file_type",
                       message="This file type is not allowed."), 400

    storage_key = build_storage_key(asset.id, filename)
    upload_url = generate_presigned_upload_url(storage_key, content_type)
    if not upload_url:
        return jsonify(error="cloud storage not configured"), 503
    return jsonify(
        uploadUrl=upload_url,
        storageKey=storage_key,
        contentType=content_type,
        assetUrl=asset_url_for(storage_key),
    )

@bp.post("/api/assets/<int:asset_id>/confirm-upload")
@require_role("editor")
def confirm_upload(asset_id):
    asset = Asset.query.filter_by(
        id=asset_id,
        org_id=g.user.org_id,
        deleted=False,
    ).first()
    if not asset:
        return jsonify(error="not_found"), 404

    data = request.get_json() or {}
    storage_key = data.get("storageKey")
    if not isinstance(storage_key, str) or not storage_key:
        return jsonify(error="storageKey required"), 400
    if not storage_key.startswith(f"assets/{asset.id}/"):
        return jsonify(error="storageKey does not belong to this asset"), 400

    try:
        info = inspect_s3_object_info(storage_key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            return jsonify(error="uploaded object not found"), 404
        raise
    if info is None:
        return jsonify(error="cloud storage not configured"), 503
    return jsonify(assetId=asset.id, storageKey=storage_key, info=info)

@bp.post("/api/assets/<int:asset_id>/replace-version")
@require_role("editor")
def replace_version(asset_id):
    asset = Asset.query.filter_by(
        id=asset_id,
        org_id=g.user.org_id,
        deleted=False,
    ).first()
    if not asset:
        return jsonify(error="not_found"), 404

    data = request.get_json() or {}
    storage_key = data.get("storageKey")
    if not isinstance(storage_key, str) or not storage_key:
        return jsonify(error="storageKey required"), 400
    if not storage_key.startswith(f"assets/{asset.id}/"):
        return jsonify(error="storageKey does not belong to this asset"), 400

    try:
        info = inspect_s3_object_info(storage_key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            return jsonify(error="uploaded object not found"), 404
        raise
    if info is None:
        return jsonify(error="cloud storage not configured"), 503

    if info["sizeBytes"] > MAX_UPLOAD_SIZE:
        return jsonify(error="file_too_large",
                       message="Maximum upload size is 50 MB."), 413

    latest_version = (
        AssetVersion.query.filter_by(asset_id=asset.id)
        .order_by(AssetVersion.version_number.desc())
        .first()
    )
    next_number = (latest_version.version_number if latest_version else 0) + 1
    version = AssetVersion(
        asset_id=asset.id,
        version_number=next_number,
        storage_key=storage_key,
        mime_type=info["mimeType"],
        size_bytes=info["sizeBytes"],
    )
    db.session.add(version)
    db.session.flush()
    asset.current_version_id = version.id
    db.session.add(
        AuditLog(
            org_id=g.user.org_id,
            actor=f"user:{g.user.id}",
            action="asset.replaced",
            entity_id=str(asset.id),
            details={"version": next_number, "storageKey": storage_key},
        )
    )
    db.session.commit()
    return jsonify(
        ok=True,
        assetId=asset.id,
        version=next_number,
        url=asset_url_for(storage_key),
        mimeType=info["mimeType"],
        sizeBytes=info["sizeBytes"],
    )


# ---------------------------------------------------------
# UPLOAD ASSET (local-disk fallback; not used when cloud storage is on)
# ---------------------------------------------------------

@bp.post("/api/assets/upload")
@require_role("editor")
def upload_asset():
    if is_cloud_storage_enabled():
        return jsonify(
            success=False,
            message="Cloud storage is enabled. Use the presigned upload flow.",
        ), 409

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
            type=classify_upload(safe_filename) or "document",
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

    # Hide half-finished uploads (assets that never got a live version).
    query = query.filter(Asset.current_version_id.isnot(None))

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


@bp.get("/api/me")
@require_role("viewer")
def me():
    return jsonify(
        id=g.user.id,
        email=g.user.email,
        role=g.user.role,
        orgId=g.user.org_id,
    )
