import secrets
from flask import Blueprint, request, jsonify, g
from ..extensions import db
from ..models import Asset, AssetVersion, AssetUsage, Website, ApiKey, AuditLog
from ..security import require_role, hash_key
from ..audit import audit

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

