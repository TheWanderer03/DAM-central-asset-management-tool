
import hashlib
from functools import wraps

from flask import session, jsonify, g, request

from .extensions import db
from .models import User, ApiKey, Website


RANK = {
    "viewer": 1,
    "editor": 2,
    "admin": 3,
}


def require_role(minimum):
    def deco(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            uid = session.get("user_id")

            if not uid:
                return jsonify(error="login required"), 401

            user = db.session.get(User, uid)

            if not user or not user.is_active:
                session.clear()
                return jsonify(error="login required"), 401

            user_role_rank = RANK.get(user.role, 0)
            required_rank = RANK.get(minimum, 999)

            if user_role_rank < required_rank:
                return jsonify(error="forbidden"), 403

            g.user = user
            return f(*args, **kwargs)

        return wrapper

    return deco


def hash_key(key):
    return hashlib.sha256(key.encode()).hexdigest()


def verify_api_key():
    key = request.headers.get(
        "Authorization", ""
    ).removeprefix("Bearer ").strip()

    if not key:
        return None

    row = (
        db.session.query(ApiKey, Website)
        .join(
            Website,
            Website.id == ApiKey.website_id,
        )
        .filter(
            ApiKey.key_hash == hash_key(key),
            ApiKey.revoked.is_(False),
        )
        .first()
    )

    if not row:
        return None

    row.ApiKey.last_used_at = db.func.now()
    db.session.commit()

    return row.Website
