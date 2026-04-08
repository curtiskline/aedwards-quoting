"""Authentication routes for password and magic-link sign in."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import login_required, login_user, logout_user
from sqlalchemy.exc import SQLAlchemyError

from .email_service import EmailDeliveryError, send_magic_link_email
from .extensions import db
from .models import AuthToken, User


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
        ttl = current_app.config["MAGIC_LINK_TTL_SECONDS"]
        token_str = secrets.token_urlsafe(32)
        auth_token = AuthToken(
            user_id=user.id,
            token=token_str,
            expires_at=datetime.utcnow() + timedelta(seconds=ttl),
        )
        db.session.add(auth_token)
        db.session.commit()

        session["pending_magic_token"] = token_str
        if remember_me:
            session["pending_remember_me"] = True

        magic_link = _build_magic_link(token_str)
        try:
            send_magic_link_email(to_email=user.email, magic_link=magic_link)
        except EmailDeliveryError as exc:
            current_app.logger.error("Magic link delivery failed for %s: %s", user.email, exc)

        return redirect(url_for("auth.waiting"))

    # User not found — redirect to login with generic message (don't disclose).
    flash("If the email is registered, a sign-in link was sent.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.get("/waiting")
def waiting():
    if "pending_magic_token" not in session:
        return redirect(url_for("auth.login"))
    return render_template("auth/waiting.html")


@auth_bp.get("/check-magic-link")
def check_magic_link():
    token_str = session.get("pending_magic_token")
    if not token_str:
        return jsonify({"status": "error"})

    auth_token = AuthToken.query.filter_by(token=token_str).first()
    if not auth_token:
        return jsonify({"status": "error"})

    if auth_token.expires_at < datetime.utcnow():
        session.pop("pending_magic_token", None)
        return jsonify({"status": "expired"})

    if auth_token.used_at is not None:
        # Token was consumed on another device — log in here too.
        remember_me = session.pop("pending_remember_me", False)
        session.pop("pending_magic_token", None)
        remember_duration = timedelta(days=current_app.config["REMEMBER_COOKIE_DURATION_DAYS"])
        login_user(auth_token.user, remember=remember_me, duration=remember_duration)
        return jsonify({"status": "authenticated", "redirect": url_for("main.dashboard")})

    return jsonify({"status": "waiting"})


@auth_bp.get("/magic/<token>")
def consume_magic_link(token: str):
    auth_token = AuthToken.query.filter_by(token=token).first()
    if not auth_token or not auth_token.is_valid:
        flash("Invalid or expired magic link.", "error")
        return redirect(url_for("auth.login"))

    auth_token.mark_used()
    db.session.commit()

    remember_duration = timedelta(days=current_app.config["REMEMBER_COOKIE_DURATION_DAYS"])
    login_user(auth_token.user, remember=True, duration=remember_duration)
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
