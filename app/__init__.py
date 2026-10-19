import os
from flask import Flask
from flask_cors import CORS
from dotenv import load_dotenv
from .extensions import db, migrate

def create_app():
    load_dotenv()
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ["DATABASE_URL"]
    app.config["SECRET_KEY"] = os.environ["SECRET_KEY"]
    app.config["CLOUD_STORAGE_PROVIDER"] = (
        os.environ.get("CLOUD_STORAGE_PROVIDER", "local") or "local"
    ).lower()
    app.config["CLOUD_BUCKET_NAME"] = os.environ.get("CLOUD_BUCKET_NAME", "")
    app.config["AWS_REGION"] = (
        os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION", "")
    )
    app.config["AWS_S3_ENDPOINT_URL"] = os.environ.get("AWS_S3_ENDPOINT_URL", "")
    app.config["AWS_ACCESS_KEY_ID"] = os.environ.get("AWS_ACCESS_KEY_ID", "")
    app.config["AWS_SECRET_ACCESS_KEY"] = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
    app.config["PUBLIC_ASSET_BASE_URL"] = (
        os.environ.get("PUBLIC_ASSET_BASE_URL", "") or ""
    ).rstrip("/")

    db.init_app(app)
    migrate.init_app(app, db)
    CORS(app, resources={r"/api/public/*": {"origins": "*"}})  # client sites call from browsers

    from . import models  # noqa: so migrations can see the tables
    from .routes import auth, dashboard, public
    app.register_blueprint(auth.bp)
    app.register_blueprint(dashboard.bp)
    app.register_blueprint(public.bp)
    return app