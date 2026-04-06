"""Web routes for the quoting app."""

from __future__ import annotations

from flask import Blueprint, render_template
from flask_login import login_required

main_bp = Blueprint("main", __name__)


@main_bp.get("/")
@login_required
def dashboard():
    return render_template("dashboard.html")


@main_bp.get("/healthz")
def healthz():
    return {"status": "ok"}
