from flask import Blueprint, request, session, jsonify
from werkzeug.security import check_password_hash
from ..extensions import db
from ..models import User

bp = Blueprint("auth", __name__)

@bp.post("/api/login")
def login():
    data = request.get_json() or {}
    user = User.query.filter_by(email=data.get("email")).first()
    if not user or not check_password_hash(user.password_hash, data.get("password", "")):
        return jsonify(error="bad credentials"), 401
    session["user_id"] = user.id
    return jsonify(email=user.email, role=user.role, orgId=user.org_id)

@bp.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)