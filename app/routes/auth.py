
from flask import (
    Blueprint,
    request,
    session,
    jsonify,
    render_template,
    g,
)
from sqlalchemy import func
from werkzeug.security import (
    check_password_hash,
    generate_password_hash,
)

from ..extensions import db
from ..models import User
from ..security import require_role
from ..audit import audit

bp = Blueprint("auth", __name__)

VALID_ROLES = {"viewer", "editor", "admin"}


@bp.get("/login")
def login_page():
    return render_template("login.html")


@bp.post("/api/login")
def login():
    data = request.get_json(silent=True) or {}

    email = str(data.get("email", "")).strip().lower()
    password = data.get("password", "")

    if not isinstance(password, str):
        return jsonify(error="bad_credentials"), 401

    user = User.query.filter(
        func.lower(User.email) == email
    ).first()

    if (
        not user
        or not user.is_active
        or not check_password_hash(user.password_hash, password)
    ):
        return jsonify(error="bad_credentials"), 401

    # Clear any previous session before establishing a new login.
    session.clear()
    session["user_id"] = user.id

    return jsonify(
        email=user.email,
        role=user.role,
        orgId=user.org_id,
    )


@bp.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


# Return the currently authenticated user's information.
@bp.get("/api/login/status")
@require_role("viewer")
def login_status():
    return jsonify(
        userId=g.user.id,
        email=g.user.email,
        role=g.user.role,
        orgId=g.user.org_id,
    )


# Render the User Management page.
@bp.get("/users")
@require_role("admin")
def users_page():
    return render_template("users.html")


# List users belonging to the current organization.
@bp.get("/api/users")
@require_role("admin")
def list_users():
    users = (
        User.query
        .filter_by(org_id=g.user.org_id)
        .order_by(User.id.asc())
        .all()
    )

    return jsonify([
        {
            "id": user.id,
            "email": user.email,
            "role": user.role,
            "isActive": user.is_active,
            "createdAt": (
                user.created_at.isoformat()
                if user.created_at else None
            ),
        }
        for user in users
    ])


# Create a user in the current organization.
@bp.post("/api/users")
@require_role("admin")
def create_user():
    data = request.get_json(silent=True) or {}

    email = str(data.get("email", "")).strip().lower()
    password = data.get("password", "")
    role = str(data.get("role", "viewer")).strip().lower()

    if (
        not email
        or len(email) > 254
        or "@" not in email
        or any(char.isspace() for char in email)
    ):
        return jsonify(
            error="validation_error",
            message="Enter a valid email address.",
        ), 400

    if not isinstance(password, str) or len(password) < 12:
        return jsonify(
            error="validation_error",
            message="Password must contain at least 12 characters.",
        ), 400

    if role not in VALID_ROLES:
        return jsonify(
            error="validation_error",
            message="Choose viewer, editor, or admin.",
        ), 400

    existing_user = User.query.filter(
        func.lower(User.email) == email
    ).first()

    if existing_user:
        return jsonify(
            error="email_exists",
            message="An account with this email already exists.",
        ), 409

    user = User(
        org_id=g.user.org_id,
        email=email,
        password_hash=generate_password_hash(password),
        role=role,
        is_active=True,
    )

    try:
        db.session.add(user)
        db.session.flush()

        audit(
            g.user.org_id,
            f"user:{g.user.id}",
            "user.created",
            user.id,
        )

        db.session.commit()

    except Exception:
        db.session.rollback()
        raise

    return jsonify(
        message="User created successfully.",
        user={
            "id": user.id,
            "email": user.email,
            "role": user.role,
            "isActive": user.is_active,
        },
    ), 201


# Change a user's role.
@bp.patch("/api/users/<int:user_id>/role")
@require_role("admin")
def change_user_role(user_id):
    data = request.get_json(silent=True) or {}
    role = str(data.get("role", "")).strip().lower()

    if role not in VALID_ROLES:
        return jsonify(
            error="validation_error",
            message="Choose viewer, editor, or admin.",
        ), 400

    user = User.query.filter_by(
        id=user_id,
        org_id=g.user.org_id,
    ).first()

    if not user:
        return jsonify(error="not_found"), 404

    if not user.is_active:
        return jsonify(
            error="account_inactive",
            message="Reactivate the account before changing its role.",
        ), 400

    if user.id == g.user.id and role != "admin":
        return jsonify(
            error="cannot_demote_self",
            message="You cannot remove your own admin role.",
        ), 400

    if user.role == role:
        return jsonify(message="User role is unchanged.")

    previous_role = user.role
    user.role = role

    try:
        audit(
            g.user.org_id,
            f"user:{g.user.id}",
            "user.role_changed",
            user.id,
        )

        db.session.commit()

    except Exception:
        db.session.rollback()
        raise

    return jsonify(
        message=f"User role changed from {previous_role} to {role}.",
        user={
            "id": user.id,
            "email": user.email,
            "role": user.role,
            "isActive": user.is_active,
        },
    )


# Activate or deactivate a user account.
@bp.patch("/api/users/<int:user_id>/status")
@require_role("admin")
def change_user_status(user_id):
    data = request.get_json(silent=True) or {}
    is_active = data.get("isActive")

    if not isinstance(is_active, bool):
        return jsonify(
            error="validation_error",
            message="isActive must be true or false.",
        ), 400

    user = User.query.filter_by(
        id=user_id,
        org_id=g.user.org_id,
    ).first()

    if not user:
        return jsonify(error="not_found"), 404

    if user.id == g.user.id and not is_active:
        return jsonify(
            error="cannot_deactivate_self",
            message="You cannot deactivate your own account.",
        ), 400

    if user.is_active == is_active:
        return jsonify(message="Account status is unchanged.")

    # Prevent deactivating the last active admin in the organization.
    if not is_active and user.role == "admin":
        other_admins = User.query.filter(
            User.org_id == g.user.org_id,
            User.role == "admin",
            User.is_active.is_(True),
            User.id != user.id,
        ).count()

        if other_admins == 0:
            return jsonify(
                error="last_admin",
                message="At least one active admin must remain.",
            ), 400

    user.is_active = is_active

    try:
        audit(
            g.user.org_id,
            f"user:{g.user.id}",
            "user.activated" if is_active else "user.deactivated",
            user.id,
        )

        db.session.commit()

    except Exception:
        db.session.rollback()
        raise

    return jsonify(
        message=(
            "Account activated."
            if is_active
            else "Account deactivated."
        ),
        user={
            "id": user.id,
            "email": user.email,
            "role": user.role,
            "isActive": user.is_active,
        },
    )
