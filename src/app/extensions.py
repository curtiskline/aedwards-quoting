"""Flask extension objects."""

from __future__ import annotations

from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base class for SQLAlchemy models."""


# All models use one shared SQLAlchemy registry/metadata.
db = SQLAlchemy(model_class=Base)
login_manager = LoginManager()
