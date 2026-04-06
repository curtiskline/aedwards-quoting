"""Flask application factory."""

from __future__ import annotations

from flask import Flask

from .config import Config
from .extensions import db
from .customers import customers_bp
from .routes import main_bp
from . import models  # noqa: F401


def create_app() -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)

    db.init_app(app)
    app.register_blueprint(main_bp)
    app.register_blueprint(customers_bp)

    return app
