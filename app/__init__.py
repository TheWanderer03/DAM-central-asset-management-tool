
import os
from pathlib import Path

from flask import Flask, g, session
from flask_cors import CORS
from dotenv import load_dotenv

from .extensions import db, migrate
from .models import User


def create_app():
    project_root = Path(__file__).resolve().parent.parent
    load_dotenv(project_root / ".env")

    app = Flask(__name__)

    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ["DATABASE_URL"]
    app.config["SECRET_KEY"] = os.environ["SECRET_KEY"]
    app.config["CLOUD_STORAGE_PROVIDER"] = (
        os.environ.get("CLOUD_STORAGE_PROVIDER", "local") or "local"
    ).lower()
    app.config["CLOUD_BUCKET_NAME"] = os.environ.get("CLOUD_BUCKET_NAME", "")
    app.config["S3_REGION"] = (
        os.environ.get("S3_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or os.environ.get("AWS_REGION", "")
    )
    app.config["S3_ENDPOINT_URL"] = (
        os.environ.get("S3_ENDPOINT_URL") or os.environ.get("AWS_S3_ENDPOINT_URL", "")
    )
    app.config["S3_ACCESS_KEY_ID"] = (
        os.environ.get("S3_ACCESS_KEY_ID") or os.environ.get("AWS_ACCESS_KEY_ID", "")
    )
    app.config["S3_SECRET_ACCESS_KEY"] = (
        os.environ.get("S3_SECRET_ACCESS_KEY") or os.environ.get("AWS_SECRET_ACCESS_KEY", "")
    )
    app.config["PUBLIC_ASSET_BASE_URL"] = (
        os.environ.get("PUBLIC_ASSET_BASE_URL", "") or ""
    ).rstrip("/")

    db.init_app(app)
    migrate.init_app(app, db)

    CORS(app, resources={r"/api/public/*": {"origins": "*"}})

    @app.before_request
    def load_logged_in_user():
        user_id = session.get("user_id")

        if not user_id:
            g.user = None
            return

        user = db.session.get(User, user_id)

        if user is None or not user.is_active:
            session.clear()
            g.user = None
            return

        g.user = user

    from .routes import auth, dashboard, public

    app.register_blueprint(auth.bp)
    app.register_blueprint(dashboard.bp)
    app.register_blueprint(public.bp)

    return app
