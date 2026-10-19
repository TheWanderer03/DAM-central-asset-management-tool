import secrets
from botocore.exceptions import ClientError
from flask import Blueprint, request, jsonify, g
from ..extensions import db
from ..models import Asset, AssetVersion, AssetUsage, Website, ApiKey, AuditLog
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

def asset_dict(a):
    return dict(id=a.id, publicId=a.public_id, name=a.name, type=a.type, tags=a.tags,
                currentVersionId=a.current_version_id)

@bp.get("/api/assets")
@require_role("viewer")
def list_assets():
    q, tag = request.args.get("q", ""), request.args.get("tag", "")
    query = Asset.query.filter_by(org_id=g.user.org_id, deleted=False)
    if q:
        query = query.filter(Asset.name.ilike(f"%{q}%"))
    if tag:
        query = query.filter(Asset.tags.any(tag))
    return jsonify([asset_dict(a) for a in query.order_by(Asset.id.desc())])

@bp.get("/api/assets/<int:asset_id>")
@require_role("viewer")
def get_asset(asset_id):
    asset = Asset.query.filter_by(id=asset_id, org_id=g.user.org_id, deleted=False).first()
    if not asset:
        return jsonify(error="not_found"), 404   # same answer whether missing or someone else's
    versions = AssetVersion.query.filter_by(asset_id=asset.id).order_by(AssetVersion.version_number.desc())
    usages = (db.session.query(Website.name, AssetUsage.page_url, AssetUsage.last_seen)
              .join(Website, Website.id == AssetUsage.website_id)
              .filter(AssetUsage.asset_id == asset.id))
    return jsonify(
        asset=asset_dict(asset),
        versions=[dict(id=v.id, version=v.version_number, storageKey=v.storage_key,
                       mimeType=v.mime_type, sizeBytes=v.size_bytes,
                       createdAt=v.created_at) for v in versions],
        usages=[dict(website=u[0], pageUrl=u[1], lastSeen=u[2]) for u in usages],
    )

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

@bp.post("/api/websites/<int:website_id>/keys")
@require_role("admin")
def create_key(website_id):
    site = Website.query.filter_by(id=website_id, org_id=g.user.org_id).first()
    if not site:
        return jsonify(error="not_found"), 404
    key = "dam_live_" + secrets.token_urlsafe(24)
    db.session.add(ApiKey(website_id=site.id, prefix=key[:12], key_hash=hash_key(key)))
    db.session.commit()
    audit(g.user.org_id, f"user:{g.user.id}", "apikey.created", site.id)
    return jsonify(apiKey=key, note="shown once, store it now")

@bp.get("/api/audit")
@require_role("admin")
def audit_list():
    rows = AuditLog.query.filter_by(org_id=g.user.org_id).order_by(AuditLog.id.desc()).limit(100)
    return jsonify([dict(id=r.id, actor=r.actor, action=r.action, entityId=r.entity_id,
                         details=r.details, createdAt=r.created_at) for r in rows])
@bp.get("/api/me")
@require_role("viewer")
def me():
    return jsonify(id=g.user.id, email=g.user.email,
                   role=g.user.role, orgId=g.user.org_id)

@bp.get("/api/websites")
@require_role("viewer")
def list_websites():
    rows = Website.query.filter_by(org_id=g.user.org_id).order_by(Website.id)
    return jsonify([dict(id=w.id, name=w.name, domain=w.domain) for w in rows])

@bp.post("/api/websites")
@require_role("admin")
def create_website():
    data = request.get_json() or {}
    if not data.get("name") or not data.get("domain"):
        return jsonify(error="name and domain required"), 400
    w = Website(org_id=g.user.org_id, name=data["name"], domain=data["domain"])
    db.session.add(w)
    db.session.commit()
    audit(g.user.org_id, f"user:{g.user.id}", "website.created", w.id)
    return jsonify(id=w.id, name=w.name, domain=w.domain), 201

