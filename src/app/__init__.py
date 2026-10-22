"""Flask application factory."""

from __future__ import annotations

from datetime import timedelta

from flask import Flask, Response, request, url_for
from flask_login import current_user

from . import models  # noqa: F401
from .admin_routes import admin_bp
from .auth_routes import auth_bp
from .calculator_routes import calculator_bp
from .config import Config
from .customers import customers_bp
from .extensions import db, login_manager
from .fulfillment import fulfillment_bp
from .inventory import stock_bp
from .models import FailedIntake, User
from .orders import orders_bp
from .quotes import quotes_bp
from .routes import main_bp
from .security import install_csrf


def create_app() -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)
    app.config["REMEMBER_COOKIE_DURATION"] = timedelta(days=app.config["REMEMBER_COOKIE_DURATION_DAYS"])

    db.init_app(app)
    login_manager.init_app(app)
    install_csrf(app)

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(customers_bp)
    app.register_blueprint(quotes_bp)
    app.register_blueprint(orders_bp)
    app.register_blueprint(fulfillment_bp)
    app.register_blueprint(stock_bp)
    app.register_blueprint(calculator_bp)

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

    @app.context_processor
    def inject_shell_state() -> dict[str, bool]:
        """Sidebar seat line (T514 slice 2): the live sending mode, so the
        shell never claims a policy the backend is not enforcing."""
        if not current_user.is_authenticated:
            return {"shell_auto_send_on": False}
        try:
            from .confidence import active_trust_tier

            return {"shell_auto_send_on": active_trust_tier() == 2}
        except Exception:
            # A missing table (fresh DB before migrations) must not break every page.
            return {"shell_auto_send_on": False}

    return app
