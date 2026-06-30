"""User admin routes."""

from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import desc

from .extensions import db
from .models import RejectedEmail, User


admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _normalized_email(value: str) -> str:
    return value.strip().lower()


def _users_partial_response(status: int = 200):
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template("partials/user_rows.html", users=users), status


@admin_bp.get("/users")
@login_required
def users_page() -> str:
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template("admin/users.html", users=users)


@admin_bp.post("/users")
@login_required
def create_user():
    email = _normalized_email(request.form.get("email", ""))
    name = request.form.get("name", "").strip()
    password = request.form.get("password", "")

    if not email or not name or not password:
        flash("Name, email, and password are required.", "error")
        return _users_partial_response(status=400)

    if User.query.filter_by(email=email).first():
        flash("User with this email already exists.", "error")
        return _users_partial_response(status=409)

    user = User(email=email, name=name, password_hash="")
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    flash("User added.", "success")
    return _users_partial_response()


@admin_bp.post("/users/<int:user_id>/delete")
@login_required
def delete_user(user_id: int):
    user = db.session.get(User, user_id)
    if not user:
        flash("User not found.", "error")
        return _users_partial_response(status=404)

    if user.id == current_user.id:
        flash("You cannot remove your own account.", "error")
        return _users_partial_response(status=400)

    if User.query.count() <= 1:
        flash("At least one user must remain.", "error")
        return _users_partial_response(status=400)

    db.session.delete(user)
    db.session.commit()

    flash("User removed.", "success")
    return _users_partial_response()


@admin_bp.get("/rejected-emails")
@login_required
def rejected_emails():
    page = request.args.get("page", 1, type=int)
    per_page = 50
    pagination = (
        RejectedEmail.query
        .order_by(desc(RejectedEmail.received_at))
        .paginate(page=page, per_page=per_page, error_out=False)
    )
    return render_template(
        "admin/rejected_emails.html",
        rejected_emails=pagination.items,
        pagination=pagination,
    )
