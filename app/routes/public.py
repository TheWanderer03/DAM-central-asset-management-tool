from flask import Blueprint, jsonify, request, current_app, send_from_directory
from sqlalchemy.dialects.postgresql import insert
from ..extensions import db
from ..models import Asset, AssetVersion, AssetUsage
from ..security import verify_api_key

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
    resp = jsonify(publicId=a.public_id, name=a.name, type=a.type, version=v.version_number,
                   url=current_app.config["PUBLIC_ASSET_BASE_URL"] + v.storage_key,
                   mimeType=v.mime_type, sizeBytes=v.size_bytes, updatedAt=v.created_at)
    resp.headers["Cache-Control"] = "public, max-age=60"
    return resp

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

# Local file serving. Person 2 replaces this with S3/R2.
@bp.get("/files/<path:name>")
def files(name):
    return send_from_directory("../uploads", name)

@bp.get("/")
def home():
    return jsonify(service="DAM API", status="ok",
                   try_this="/api/public/assets/ast_logo001")