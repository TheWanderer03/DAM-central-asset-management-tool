
from sqlalchemy.dialects.postgresql import ARRAY

from .extensions import db


def now_col():
    return db.Column(
        db.DateTime(timezone=True),
        server_default=db.func.now(),
    )


class Organization(db.Model):
    __tablename__ = "organizations"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False)
    created_at = now_col()


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    org_id = db.Column(
        db.ForeignKey("organizations.id"),
        nullable=False,
        index=True,
    )
    email = db.Column(db.String, unique=True, nullable=False)
    password_hash = db.Column(db.String, nullable=False)
    role = db.Column(
        db.String,
        nullable=False,
        default="viewer",
    )
    is_active = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
        server_default=db.true(),
    )
    created_at = now_col()


class Website(db.Model):
    __tablename__ = "websites"

    id = db.Column(db.Integer, primary_key=True)
    org_id = db.Column(
        db.ForeignKey("organizations.id"),
        nullable=False,
        index=True,
    )
    name = db.Column(db.String, nullable=False)
    domain = db.Column(db.String, nullable=False)
    webhook_url = db.Column(db.String)
    created_at = now_col()


class ApiKey(db.Model):
    __tablename__ = "api_keys"

    id = db.Column(db.Integer, primary_key=True)
    website_id = db.Column(
        db.ForeignKey("websites.id"),
        nullable=False,
    )
    prefix = db.Column(db.String, nullable=False)
    key_hash = db.Column(
        db.String,
        unique=True,
        nullable=False,
    )
    revoked = db.Column(
        db.Boolean,
        default=False,
        nullable=False,
    )
    last_used_at = db.Column(db.DateTime(timezone=True))
    created_at = now_col()


class Asset(db.Model):
    __tablename__ = "assets"

    id = db.Column(db.Integer, primary_key=True)
    org_id = db.Column(
        db.ForeignKey("organizations.id"),
        nullable=False,
        index=True,
    )
    public_id = db.Column(
        db.String,
        unique=True,
        nullable=False,
    )
    name = db.Column(db.String, nullable=False)
    type = db.Column(db.String, nullable=False)
    tags = db.Column(
        ARRAY(db.String),
        nullable=False,
        default=list,
    )
    current_version_id = db.Column(db.Integer)
    deleted = db.Column(
        db.Boolean,
        default=False,
        nullable=False,
    )
    created_at = now_col()


class AssetVersion(db.Model):
    __tablename__ = "asset_versions"

    id = db.Column(db.Integer, primary_key=True)
    asset_id = db.Column(
        db.ForeignKey("assets.id"),
        nullable=False,
        index=True,
    )
    version_number = db.Column(db.Integer, nullable=False)
    storage_key = db.Column(db.String, nullable=False)
    mime_type = db.Column(db.String)
    size_bytes = db.Column(db.BigInteger)
    created_at = now_col()

    __table_args__ = (
        db.UniqueConstraint(
            "asset_id",
            "version_number",
        ),
    )


class AssetUsage(db.Model):
    __tablename__ = "asset_usages"

    id = db.Column(db.Integer, primary_key=True)
    asset_id = db.Column(
        db.ForeignKey("assets.id"),
        nullable=False,
    )
    website_id = db.Column(
        db.ForeignKey("websites.id"),
        nullable=False,
    )
    page_url = db.Column(db.String, nullable=False)
    last_seen = db.Column(
        db.DateTime(timezone=True),
        server_default=db.func.now(),
    )

    __table_args__ = (
        db.UniqueConstraint(
            "asset_id",
            "website_id",
            "page_url",
        ),
    )


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    org_id = db.Column(db.Integer, index=True)
    actor = db.Column(db.String)
    action = db.Column(db.String, nullable=False)
    entity_id = db.Column(db.String)
    details = db.Column(db.JSON)
    created_at = now_col()
