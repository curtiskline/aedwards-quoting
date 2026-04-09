"""Flask application factory."""

from __future__ import annotations

from datetime import timedelta

from flask import Flask, Response, request, url_for
from flask_login import current_user

from . import models  # noqa: F401
from .admin_routes import admin_bp
from .auth_routes import auth_bp
from .config import Config
from .customers import customers_bp
from .extensions import db, login_manager
from .models import User
from .quotes import quotes_bp
from .routes import main_bp


def create_app() -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)
    app.config["REMEMBER_COOKIE_DURATION"] = timedelta(days=app.config["REMEMBER_COOKIE_DURATION_DAYS"])

    db.init_app(app)
    login_manager.init_app(app)

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(customers_bp)
    app.register_blueprint(quotes_bp)

    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please sign in to continue."
    login_manager.login_message_category = "info"

    @login_manager.user_loader
    def load_user(user_id: str) -> User | None:
        return db.session.get(User, int(user_id))

    @app.before_request
    def require_auth_for_app_routes():
        endpoint = request.endpoint or ""
        if endpoint == "static" or endpoint == "main.healthz" or endpoint.startswith("auth."):
            return None
        if current_user.is_authenticated:
            return None
        if request.headers.get("HX-Request"):
            return Response("", 200, {"HX-Redirect": url_for("auth.login")})
        return login_manager.unauthorized()

    @app.context_processor
    def inject_auth_state() -> dict[str, bool]:
        return {"is_authenticated": current_user.is_authenticated}

    return app
