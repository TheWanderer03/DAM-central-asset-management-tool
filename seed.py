import secrets
from werkzeug.security import generate_password_hash
from app import create_app
from app.extensions import db
from app.models import *
from app.security import hash_key

app = create_app()
with app.app_context():
    pw = generate_password_hash("password123")
    acme, other = Organization(name="Acme Co"), Organization(name="Other Co")
    db.session.add_all([acme, other]); db.session.flush()

    db.session.add_all([
        User(org_id=acme.id, email="admin@acme.test", password_hash=pw, role="admin"),
        User(org_id=acme.id, email="editor@acme.test", password_hash=pw, role="editor"),
        User(org_id=acme.id, email="viewer@acme.test", password_hash=pw, role="viewer"),
        User(org_id=other.id, email="admin@other.test", password_hash=pw, role="admin"),
    ])
    site = Website(org_id=acme.id, name="Acme Demo", domain="localhost:4000")
    db.session.add(site); db.session.flush()

    key = "dam_live_" + secrets.token_urlsafe(24)
    db.session.add(ApiKey(website_id=site.id, prefix=key[:12], key_hash=hash_key(key)))
    print("Demo site API key (save it!):", key)

    for org, pid, name in [(acme, "ast_logo001", "Main logo"), (other, "ast_secret01", "Other org logo")]:
        a = Asset(org_id=org.id, public_id=pid, name=name, type="image", tags=["logo", "brand"])
        db.session.add(a); db.session.flush()
        v = AssetVersion(asset_id=a.id, version_number=1, storage_key=f"{pid}_v1.png",
                         mime_type="image/png", size_bytes=12345)
        db.session.add(v); db.session.flush()
        a.current_version_id = v.id
    db.session.commit()
    print("Seed done.")