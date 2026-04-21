"""Application configuration for the Allan Edwards Flask app."""

from __future__ import annotations

import os
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
INSTANCE_DIR = REPO_ROOT / "instance"
DEFAULT_SQLITE_PATH = INSTANCE_DIR / "allenedwards.db"


def _default_database_url() -> str:
    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{DEFAULT_SQLITE_PATH}"


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key")
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", _default_database_url())
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    REMEMBER_COOKIE_DURATION_DAYS = int(os.getenv("REMEMBER_COOKIE_DURATION_DAYS", "30"))
    MAGIC_LINK_TTL_SECONDS = int(os.getenv("MAGIC_LINK_TTL_SECONDS", "1800"))
    APP_URL = os.getenv("APP_URL")
