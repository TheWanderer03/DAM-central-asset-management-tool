import hashlib
from functools import wraps
from flask import session, jsonify, g, request
from .extensions import db
from .models import User, ApiKey, Website

RANK = {"viewer": 1, "editor": 2, "admin": 3}

def require_role(minimum):
    """Decorator. Sets g.user. Role is read fresh each request so changes apply instantly."""
    def deco(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            uid = session.get("user_id")
            if not uid:
                return jsonify(error="login required"), 401
            user = db.session.get(User, uid)
            if not user or RANK[user.role] < RANK[minimum]:
                return jsonify(error="forbidden"), 403
            g.user = user
            return f(*args, **kwargs)
        return wrapper
    return deco

def hash_key(key):
    return hashlib.sha256(key.encode()).hexdigest()

def verify_api_key():
    """Returns the Website the key belongs to, or None."""
    key = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not key:
        return None
    row = (db.session.query(ApiKey, Website)
           .join(Website, Website.id == ApiKey.website_id)
           .filter(ApiKey.key_hash == hash_key(key), ApiKey.revoked.is_(False))
           .first())
    if not row:
        return None
    row.ApiKey.last_used_at = db.func.now()
    db.session.commit()
    return row.Website