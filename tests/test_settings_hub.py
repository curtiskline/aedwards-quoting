"""T514 slice 1: Settings hub, link tabs, consolidated nav, failed-intake badge."""

from __future__ import annotations

from datetime import datetime

import pytest

from app import create_app
from app.extensions import db as _db
from app.models import FailedIntake, User


@pytest.fixture()
def app(db_url, monkeypatch):
    from app.config import Config

    monkeypatch.setattr(Config, "SQLALCHEMY_DATABASE_URI", db_url)
    monkeypatch.setattr(Config, "TESTING", True, raising=False)
    application = create_app()
    with application.app_context():
        _db.create_all()
        owner = User(email="owner@example.com", name="Owner", password_hash="")
        owner.set_password("secret123")
        _db.session.add(owner)
        _db.session.commit()
        yield application
        _db.session.remove()


@pytest.fixture()
def client(app):
    c = app.test_client()
    c.post(
        "/auth/password",
        data={"email": "owner@example.com", "password": "secret123"},
        follow_redirects=True,
    )
    return c


def test_settings_hub_requires_login(app):
    anonymous = app.test_client()
    response = anonymous.get("/settings")
    assert response.status_code in (301, 302)
    assert "/auth" in response.headers["Location"]


def test_settings_hub_shows_sections_and_state(client):
    page = client.get("/settings").get_data(as_text=True)
    assert "Settings" in page
    # Sending policy state in plain words (default is Tier 1, assisted).
    assert "Auto-send is OFF" in page
    assert "No send holds are active." in page
    # Every configuration area is reachable from the hub.
    assert "/admin/pricing?tab=pricing" in page
    assert "/admin/pricing?tab=catalog" in page
    assert "/admin/pricing?tab=types" in page
    assert "/admin/pricing?tab=shipping" in page
    assert "/admin/pricing?tab=trust" in page
    assert "/admin/users" in page
    assert "/admin/failed-intakes" in page
    assert "/admin/rejected-emails" in page


def test_settings_sections_are_link_tabs(client):
    page = client.get("/admin/pricing?tab=catalog").get_data(as_text=True)
    # Tabs are real links with the current one marked, not JS-only buttons.
    assert 'aria-current="page">Product catalog</a>' in page
    assert "/admin/pricing?tab=trust" in page
    assert "Sending policy" in page
    # Only the active section's content renders.
    assert "Shipping defaults</h2>" not in page


def test_nav_consolidated_to_settings(client):
    page = client.get("/settings").get_data(as_text=True)
    nav = page[: page.index("<main>")]
    assert ">Settings</a>" in nav
    assert "Work queue" in nav
    # The old admin links are gone from the nav; failed intake now
    # surfaces as work-queue rows and on the Settings hub instead.
    assert ">Admin</a>" not in nav
    assert ">Users</a>" not in nav
    assert ">Rejected</a>" not in nav
    assert ">Set password</a>" not in nav


def test_failed_intake_surfaces_in_work_queue(client):
    _db.session.add(
        FailedIntake(
            received_at=datetime(2026, 10, 19, 12, 0),
            sender_email="buyer@example.com",
            subject="Tulsa RFQ",
            failure_stage="parse",
        )
    )
    _db.session.add(
        FailedIntake(
            received_at=datetime(2026, 10, 19, 12, 5),
            sender_email="handled@example.com",
            subject="RFQ 2",
            failure_stage="parse",
            resolved_at=datetime(2026, 10, 19, 13, 0),
        )
    )
    _db.session.commit()

    page = client.get("/").get_data(as_text=True)
    # Only the unresolved intake appears, as actionable work.
    assert "buyer@example.com" in page
    assert "Recover the request details" in page
    assert "handled@example.com" not in page
    # And the Settings hub still shows the outstanding count.
    hub = client.get("/settings").get_data(as_text=True)
    assert '<span class="settings-count">1</span>' in hub


def test_users_page_offers_password_change(client):
    page = client.get("/admin/users").get_data(as_text=True)
    assert "Users &amp; account" in page
    assert "Change your password" in page
    assert "There are no permission levels." in page
