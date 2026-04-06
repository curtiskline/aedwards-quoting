"""Authentication routes for password and magic-link sign in."""

from __future__ import annotations

from datetime import timedelta

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import login_required, login_user, logout_user
from sqlalchemy.exc import SQLAlchemyError

from .email_service import EmailDeliveryError, send_magic_link_email
from .extensions import db
from .models import User


auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


def _normalize_email(raw: str) -> str:
    return raw.strip().lower()


def _remember_me_from_form() -> bool:
    return request.form.get("remember_me") in {"on", "1", "true", "yes"}


def _build_magic_link(token: str) -> str:
    magic_path = url_for("auth.consume_magic_link", token=token)
    app_url = current_app.config.get("APP_URL")
    if app_url:
        return f"{app_url.rstrip('/')}{magic_path}"
    return url_for("auth.consume_magic_link", token=token, _external=True)


def _has_users() -> bool:
    try:
        return db.session.query(User.id).limit(1).scalar() is not None
    except SQLAlchemyError:
        return False


@auth_bp.get("/login")
def login() -> str:
    if not _has_users():
        return redirect(url_for("auth.bootstrap_user"))
    return render_template("auth/login.html")


@auth_bp.route("/bootstrap", methods=["GET", "POST"])
def bootstrap_user() -> str:
    if _has_users():
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        email = _normalize_email(request.form.get("email", ""))
        name = request.form.get("name", "").strip()
        password = request.form.get("password", "")

        if not email or not name or not password:
            flash("Name, email, and password are required.", "error")
            return render_template("auth/bootstrap.html"), 400

        user = User(email=email, name=name, password_hash="")
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        flash("Initial user created. You can sign in now.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/bootstrap.html")


@auth_bp.post("/magic-link")
def request_magic_link() -> str:
    email = _normalize_email(request.form.get("email", ""))
    remember_me = _remember_me_from_form()

    user = User.query.filter_by(email=email).first()
    if user:
        token = user.issue_magic_link_token()
        db.session.commit()

        magic_link = _build_magic_link(token)
        try:
            send_magic_link_email(to_email=user.email, magic_link=magic_link)
        except EmailDeliveryError as exc:
            current_app.logger.error("Magic link delivery failed for %s: %s", user.email, exc)

    # Keep response generic so we never disclose which emails are users.
    if remember_me:
        flash(
            "If the email is registered, a sign-in link was sent. Select remember me when signing in.",
            "info",
        )
    else:
        flash("If the email is registered, a sign-in link was sent.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.get("/magic/<token>")
def consume_magic_link(token: str):
    user = User.query.filter_by(magic_link_token=token).first()
    if not user:
        flash("Invalid or expired magic link.", "error")
        return redirect(url_for("auth.login"))

    user.magic_link_token = None
    db.session.commit()

    remember_duration = timedelta(days=current_app.config["REMEMBER_COOKIE_DURATION_DAYS"])
    login_user(user, remember=True, duration=remember_duration)
    flash("Signed in successfully.", "success")
    return redirect(url_for("main.dashboard"))


@auth_bp.post("/password")
def password_login():
    email = _normalize_email(request.form.get("email", ""))
    password = request.form.get("password", "")
    remember_me = _remember_me_from_form()

    user = User.query.filter_by(email=email).first()
    if not user or not user.check_password(password):
        flash("Invalid email or password.", "error")
        return redirect(url_for("auth.login"))

    remember_duration = timedelta(days=current_app.config["REMEMBER_COOKIE_DURATION_DAYS"])
    login_user(user, remember=remember_me, duration=remember_duration)
    flash("Signed in successfully.", "success")
    return redirect(url_for("main.dashboard"))


@auth_bp.post("/logout")
@login_required
def logout():
    logout_user()
    flash("Signed out.", "info")
    return redirect(url_for("auth.login"))
