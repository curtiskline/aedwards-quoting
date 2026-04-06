from __future__ import annotations

from urllib.parse import urlparse

import pytest

from app import create_app
from app.config import Config
from app.extensions import db
from app.models import User


@pytest.fixture
def app(tmp_path, monkeypatch):
    db_path = tmp_path / "auth-system.db"
    monkeypatch.setattr(Config, "SQLALCHEMY_DATABASE_URI", f"sqlite:///{db_path}")
    monkeypatch.setattr(Config, "TESTING", True, raising=False)
    monkeypatch.setattr(Config, "WTF_CSRF_ENABLED", False, raising=False)

    app = create_app()

    with app.app_context():
        db.create_all()

    yield app

    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def _create_user(*, email: str = "owner@example.com", name: str = "Owner", password: str = "secret123") -> User:
    user = User(email=email, name=name, password_hash="")
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return user


def test_bootstrap_first_user(client, app) -> None:
    response = client.get("/auth/login", follow_redirects=True)
    assert response.status_code == 200
    assert b"Create First User" in response.data

    created = client.post(
        "/auth/bootstrap",
        data={"name": "Owner", "email": "owner@example.com", "password": "secret123"},
        follow_redirects=True,
    )
    assert created.status_code == 200
    assert b"Initial user created" in created.data

    signed_in = client.post(
        "/auth/password",
        data={"email": "owner@example.com", "password": "secret123"},
        follow_redirects=True,
    )
    assert signed_in.status_code == 200
    assert b"Dashboard" in signed_in.data


def test_password_login_required_dashboard(client, app) -> None:
    with app.app_context():
        _create_user()

    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]

    signed_in = client.post(
        "/auth/password",
        data={"email": "owner@example.com", "password": "secret123", "remember_me": "on"},
        follow_redirects=True,
    )
    assert signed_in.status_code == 200
    assert b"Signed in successfully" in signed_in.data
    assert b"Dashboard" in signed_in.data


def test_magic_link_login(client, app, monkeypatch) -> None:
    delivered: dict[str, str] = {}

    def fake_send_magic_link_email(*, to_email: str, magic_link: str) -> None:
        delivered["to"] = to_email
        delivered["link"] = magic_link

    monkeypatch.setattr("app.auth_routes.send_magic_link_email", fake_send_magic_link_email)

    with app.app_context():
        _create_user(email="teammate@example.com", name="Teammate")

    request_response = client.post(
        "/auth/magic-link",
        data={"email": "teammate@example.com"},
        follow_redirects=True,
    )
    assert request_response.status_code == 200
    assert b"sign-in link was sent" in request_response.data
    assert delivered["to"] == "teammate@example.com"

    path = urlparse(delivered["link"]).path
    consume_response = client.get(path, follow_redirects=True)
    assert consume_response.status_code == 200
    assert b"Dashboard" in consume_response.data

    with app.app_context():
        user = User.query.filter_by(email="teammate@example.com").first()
        assert user is not None
        assert user.magic_link_token is None


def test_user_admin_add_and_remove(client, app) -> None:
    with app.app_context():
        owner = _create_user()
        owner_id = owner.id

    client.post(
        "/auth/password",
        data={"email": "owner@example.com", "password": "secret123"},
        follow_redirects=True,
    )

    add_response = client.post(
        "/admin/users",
        data={"name": "Teammate", "email": "teammate@example.com", "password": "teammate123"},
        follow_redirects=True,
    )
    assert add_response.status_code == 200
    assert b"teammate@example.com" in add_response.data

    with app.app_context():
        teammate = User.query.filter_by(email="teammate@example.com").first()
        assert teammate is not None
        teammate_id = teammate.id

    cannot_delete_self = client.post(f"/admin/users/{owner_id}/delete", follow_redirects=True)
    assert cannot_delete_self.status_code == 400
    assert b"owner@example.com" in cannot_delete_self.data

    delete_response = client.post(f"/admin/users/{teammate_id}/delete", follow_redirects=True)
    assert delete_response.status_code == 200
    assert b"teammate@example.com" not in delete_response.data
