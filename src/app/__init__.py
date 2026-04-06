"""Flask application factory."""

from __future__ import annotations

from datetime import timedelta

from flask import Flask
from flask_login import current_user

from .config import Config
from .extensions import db, login_manager
from .models import User
from .admin_routes import admin_bp
from .auth_routes import auth_bp
from .routes import main_bp
from . import models  # noqa: F401


def create_app() -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)
    app.config["REMEMBER_COOKIE_DURATION"] = timedelta(days=app.config["REMEMBER_COOKIE_DURATION_DAYS"])

    db.init_app(app)
    login_manager.init_app(app)
    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)

    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please sign in to continue."
    login_manager.login_message_category = "info"

    @login_manager.user_loader
    def load_user(user_id: str) -> User | None:
        return db.session.get(User, int(user_id))

    @app.context_processor
    def inject_auth_state() -> dict[str, bool]:
        return {"is_authenticated": current_user.is_authenticated}

    return app
