from flask import Blueprint, jsonify, request, current_app, redirect, send_from_directory
from sqlalchemy.dialects.postgresql import insert
from ..extensions import db
from ..models import Asset, AssetVersion, AssetUsage
from ..security import verify_api_key
from ..storage import asset_url_for, is_cloud_storage_enabled

bp = Blueprint("public", __name__)

@bp.get("/api/public/assets/<public_id>")
def public_asset(public_id):
    row = (db.session.query(Asset, AssetVersion)
           .join(AssetVersion, AssetVersion.id == Asset.current_version_id)
           .filter(Asset.public_id == public_id, Asset.deleted.is_(False))
           .first())
    if not row:
        return jsonify(error="not_found"), 404
    a, v = row
    asset_url = asset_url_for(v.storage_key)
    if not asset_url:
        asset_url = f"{current_app.config['PUBLIC_ASSET_BASE_URL'].rstrip('/')}/{v.storage_key.lstrip('/')}"
    resp = jsonify(publicId=a.public_id, name=a.name, type=a.type, version=v.version_number,
                   url=asset_url,
                   mimeType=v.mime_type, sizeBytes=v.size_bytes, updatedAt=v.created_at)
    resp.headers["Cache-Control"] = "public, max-age=60"
    return resp

@bp.get("/a/<public_id>")
def public_redirect(public_id):
    row = (db.session.query(Asset, AssetVersion)
           .join(AssetVersion, AssetVersion.id == Asset.current_version_id)
           .filter(Asset.public_id == public_id, Asset.deleted.is_(False))
           .first())
    if not row:
        return jsonify(error="not_found"), 404
    _, version = row
    target = asset_url_for(version.storage_key)
    if not target:
        return jsonify(error="public asset base URL not configured"), 500
    return redirect(target, code=302)

@bp.post("/api/public/usages")
def report_usage():
    site = verify_api_key()
    if not site:
        return jsonify(error="invalid api key"), 401
    data = request.get_json() or {}
    asset = Asset.query.filter_by(public_id=data.get("publicId"), org_id=site.org_id).first()
    if not asset:
        return jsonify(error="not_found"), 404   # also blocks using another org's asset
    stmt = (insert(AssetUsage)
            .values(asset_id=asset.id, website_id=site.id, page_url=data.get("pageUrl"))
            .on_conflict_do_update(constraint="asset_usages_asset_id_website_id_page_url_key",
                                   set_={"last_seen": db.func.now()}))
    db.session.execute(stmt)
    db.session.commit()
    return jsonify(ok=True)

@bp.get("/files/<path:name>")
def files(name):
    if is_cloud_storage_enabled():
        target = asset_url_for(name)
        if not target:
            return jsonify(error="public asset base URL not configured"), 500
        return redirect(target, code=302)
    return send_from_directory("../uploads", name)

@bp.get("/")
def home():
    return jsonify(service="DAM API", status="ok",
                   try_this="/api/public/assets/ast_logo001")