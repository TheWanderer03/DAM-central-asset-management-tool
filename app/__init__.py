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
    app.config["PUBLIC_ASSET_BASE_URL"] = os.environ.get("PUBLIC_ASSET_BASE_URL", "")

    db.init_app(app)
    migrate.init_app(app, db)
    CORS(app, resources={r"/api/public/*": {"origins": "*"}})  # client sites call from browsers

    from . import models  # noqa: so migrations can see the tables
    from .routes import auth, dashboard, public
    app.register_blueprint(auth.bp)
    app.register_blueprint(dashboard.bp)
    app.register_blueprint(public.bp)
    return app