
import secrets

from flask import Blueprint, request, jsonify, render_template, g

from ..extensions import db
from ..models import Asset, AssetVersion, AssetUsage, Website, ApiKey, AuditLog
from ..security import require_role, hash_key
from ..audit import audit


bp = Blueprint("dashboard", __name__)


# Dashboard page
@bp.get("/")
def dashboard_page():
    return render_template("dashboard.html")


# Assets listing page
@bp.get("/assets")
def assets_page():
    return render_template("assets.html")


# Upload assets page
@bp.get("/upload")
def upload_page():
    return render_template("upload.html")


# Individual asset details page
@bp.get("/assets/<int:asset_id>")
def asset_detail_page(asset_id):
    return render_template("asset_detail.html")


# Convert an asset model into JSON
def asset_dict(a):
    return dict(
        id=a.id,
        publicId=a.public_id,
        name=a.name,
        type=a.type,
        tags=a.tags,
        currentVersionId=a.current_version_id
    )


# List assets belonging to the logged-in user's organization
@bp.get("/api/assets")
@require_role("viewer")
def list_assets():
    q = request.args.get("q", "")
    tag = request.args.get("tag", "")

    query = Asset.query.filter_by(
        org_id=g.user.org_id,
        deleted=False
    )

    if q:
        query = query.filter(Asset.name.ilike(f"%{q}%"))

    if tag:
        query = query.filter(Asset.tags.any(tag))

    assets = query.order_by(Asset.id.desc())

    return jsonify([asset_dict(a) for a in assets])


# Get an individual asset with versions and website usage
@bp.get("/api/assets/<int:asset_id>")
@require_role("viewer")
def get_asset(asset_id):
    asset = Asset.query.filter_by(
        id=asset_id,
        org_id=g.user.org_id,
        deleted=False
    ).first()

    if not asset:
        return jsonify(error="not_found"), 404

    versions = AssetVersion.query.filter_by(
        asset_id=asset.id
    ).order_by(AssetVersion.version_number.desc())

    usages = (
        db.session.query(
            Website.name,
            AssetUsage.page_url,
            AssetUsage.last_seen
        )
        .join(Website, Website.id == AssetUsage.website_id)
        .filter(AssetUsage.asset_id == asset.id)
    )

    return jsonify(
        asset=asset_dict(asset),
        versions=[
            dict(
                id=v.id,
                version=v.version_number,
                storageKey=v.storage_key,
                mimeType=v.mime_type,
                sizeBytes=v.size_bytes,
                createdAt=v.created_at
            )
            for v in versions
        ],
        usages=[
            dict(
                website=u[0],
                pageUrl=u[1],
                lastSeen=u[2]
            )
            for u in usages
        ]
    )


# Create an API key for a website (admin only)
@bp.post("/api/websites/<int:website_id>/keys")
@require_role("admin")
def create_key(website_id):
    site = Website.query.filter_by(
        id=website_id,
        org_id=g.user.org_id
    ).first()

    if not site:
        return jsonify(error="not_found"), 404

    key = "dam_live_" + secrets.token_urlsafe(24)

    db.session.add(
        ApiKey(
            website_id=site.id,
            prefix=key[:12],
            key_hash=hash_key(key)
        )
    )

    db.session.commit()

    audit(
        g.user.org_id,
        f"user:{g.user.id}",
        "apikey.created",
        site.id
    )

    return jsonify(
        apiKey=key,
        note="shown once, store it now"
    )


# List recent audit events (admin only)
@bp.get("/api/audit")
@require_role("admin")
def audit_list():
    rows = (
        AuditLog.query
        .filter_by(org_id=g.user.org_id)
        .order_by(AuditLog.id.desc())
        .limit(100)
    )

    return jsonify([
        dict(
            id=r.id,
            actor=r.actor,
            action=r.action,
            entityId=r.entity_id,
            details=r.details,
            createdAt=r.created_at
        )
        for r in rows
    ])
