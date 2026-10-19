
import os
import secrets
import uuid
from pathlib import Path

from flask import (
    Blueprint,
    request,
    jsonify,
    render_template,
    g,
    current_app,
)
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
# CONFIGURATION
# ---------------------------------------------------------

ALLOWED_UPLOAD_EXTENSIONS = {
    "png", "jpg", "jpeg", "gif", "webp", "svg",
    "mp4", "webm", "mov",
    "pdf", "doc", "docx", "ppt", "pptx", "txt",
}

MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50 MB


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
# ASSET HELPERS AND APIS
# ---------------------------------------------------------

def asset_dict(asset):
    return {
        "id": asset.id,
        "publicId": asset.public_id,
        "name": asset.name,
        "type": asset.type,
        "tags": asset.tags,
        "currentVersionId": asset.current_version_id,
    }


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


@bp.get("/api/assets/<int:asset_id>")
@require_role("viewer")
def get_asset(asset_id):
    asset = Asset.query.filter_by(
        id=asset_id,
        org_id=g.user.org_id,
        deleted=False,
    ).first()

    if not asset:
        return jsonify(error="not_found"), 404

    versions = AssetVersion.query.filter_by(
        asset_id=asset.id
    ).order_by(
        AssetVersion.version_number.desc()
    ).all()

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
# ASSET UPLOAD
# ---------------------------------------------------------

@bp.post("/api/assets/upload")
@require_role("editor")
def upload_asset():
    uploaded_file = request.files.get("file")
    asset_name = request.form.get("name", "").strip()
    raw_tags = request.form.get("tags", "")

    if not uploaded_file or not uploaded_file.filename:
        return jsonify(
            error="validation_error",
            message="Please select a file.",
        ), 400

    if not asset_name:
        return jsonify(
            error="validation_error",
            message="Asset name is required.",
        ), 400

    if len(asset_name) > 200:
        return jsonify(
            error="validation_error",
            message="Asset name must be 200 characters or fewer.",
        ), 400

    original_filename = secure_filename(
        uploaded_file.filename
    )

    if not original_filename or "." not in original_filename:
        return jsonify(
            error="validation_error",
            message="Invalid filename.",
        ), 400

    extension = original_filename.rsplit(".", 1)[1].lower()

    if extension not in ALLOWED_UPLOAD_EXTENSIONS:
        return jsonify(
            error="unsupported_file_type",
            message="This file type is not allowed.",
        ), 400

    # Determine the asset category.
    if extension in {
        "png", "jpg", "jpeg", "gif", "webp", "svg"
    }:
        asset_type = "image"
    elif extension in {"mp4", "webm", "mov"}:
        asset_type = "video"
    else:
        asset_type = "document"

    # Read the actual uploaded size.
    uploaded_file.stream.seek(0, os.SEEK_END)
    file_size = uploaded_file.stream.tell()
    uploaded_file.stream.seek(0)

    if file_size == 0:
        return jsonify(
            error="validation_error",
            message="The selected file is empty.",
        ), 400

    if file_size > MAX_UPLOAD_SIZE:
        return jsonify(
            error="file_too_large",
            message="Maximum upload size is 50 MB.",
        ), 413

    # Normalize tags and remove duplicates.
    tags = list(dict.fromkeys(
        tag.strip()
        for tag in raw_tags.split(",")
        if tag.strip()
    ))

    # Generate an unpredictable filename.
    storage_filename = f"{uuid.uuid4().hex}.{extension}"

    # Keep the key relative so storage can later be moved
    # to S3/R2 without storing absolute local paths in the DB.
    storage_key = (
        f"{g.user.org_id}/{storage_filename}"
    )

    upload_root = (
        Path(current_app.root_path).parent / "uploads"
    ).resolve()

    destination = (
        upload_root / storage_key
    ).resolve()

    # Prevent paths from escaping the upload directory.
    if not destination.is_relative_to(upload_root):
        return jsonify(error="invalid_storage_path"), 400

    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    saved_file = False

    try:
        uploaded_file.save(destination)
        saved_file = True

        asset = Asset(
            org_id=g.user.org_id,
            public_id=f"ast_{uuid.uuid4().hex}",
            name=asset_name,
            type=asset_type,
            tags=tags,
            deleted=False,
        )

        db.session.add(asset)
        db.session.flush()

        version = AssetVersion(
            asset_id=asset.id,
            version_number=1,
            storage_key=storage_key,
            mime_type=uploaded_file.mimetype,
            size_bytes=file_size,
        )

        db.session.add(version)
        db.session.flush()

        asset.current_version_id = version.id

        audit(
            g.user.org_id,
            f"user:{g.user.id}",
            "asset.uploaded",
            asset.id,
        )

        db.session.commit()

        return jsonify(
            message="Asset uploaded successfully.",
            asset=asset_dict(asset),
        ), 201

    except Exception:
        db.session.rollback()

        if saved_file:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                current_app.logger.exception(
                    "Could not clean up failed upload: %s",
                    destination,
                )

        current_app.logger.exception(
            "Asset upload failed."
        )

        return jsonify(
            error="upload_failed",
            message="The upload could not be completed.",
        ), 500


# ---------------------------------------------------------
# WEBSITE MANAGEMENT
# ---------------------------------------------------------

def website_dict(site):
    keys = ApiKey.query.filter_by(
        website_id=site.id
    ).order_by(
        ApiKey.id.desc()
    ).all()

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


@bp.get("/api/websites")
@require_role("viewer")
def list_websites():
    sites = Website.query.filter_by(
        org_id=g.user.org_id
    ).order_by(
        Website.id.desc()
    ).all()

    return jsonify([
        website_dict(site)
        for site in sites
    ])


@bp.post("/api/websites")
@require_role("admin")
def create_website():
    data = request.get_json(silent=True) or {}

    name = str(data.get("name", "")).strip()
    domain = str(data.get("domain", "")).strip()
    webhook_url = (
        str(data.get("webhookUrl", "")).strip()
        or None
    )

    if not name or not domain:
        return jsonify(
            error="validation_error",
            message="Website name and domain are required.",
        ), 400

    if any(char.isspace() for char in domain) or "/" in domain:
        return jsonify(
            error="validation_error",
            message=(
                "Enter a domain such as example.com, "
                "without a path."
            ),
        ), 400

    site = Website(
        org_id=g.user.org_id,
        name=name,
        domain=domain,
        webhook_url=webhook_url,
    )

    db.session.add(site)
    db.session.flush()

    audit(
        g.user.org_id,
        f"user:{g.user.id}",
        "website.created",
        site.id,
    )

    db.session.commit()

    return jsonify(
        website=website_dict(site)
    ), 201


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

    db.session.add(api_key)
    db.session.flush()

    audit(
        g.user.org_id,
        f"user:{g.user.id}",
        "apikey.created",
        site.id,
    )

    db.session.commit()

    # The complete key is returned only once.
    return jsonify(
        apiKey=key,
        prefix=api_key.prefix,
        note=(
            "Copy and store this key now. "
            "It cannot be retrieved later."
        ),
    ), 201


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
        )

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
    )


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
