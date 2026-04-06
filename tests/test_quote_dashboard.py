"""Tests for the quote queue dashboard."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app import create_app
from app.extensions import db as _db
from app.models import Quote, QuoteLineItem, QuoteStatus, User


@pytest.fixture()
def app(tmp_path, monkeypatch):
    """Create an app with a test SQLite DB."""
    from app.config import Config

    db_path = tmp_path / "test.db"
    monkeypatch.setattr(Config, "SQLALCHEMY_DATABASE_URI", f"sqlite:///{db_path}")
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


@pytest.fixture()
def seed_quotes(app):
    """Seed several quotes with different statuses."""
    with app.app_context():
        user = User(email="jamee@allanedwards.com", name="Jamee", password_hash="x")
        _db.session.add(user)
        _db.session.flush()

        quotes = []
        for i, (status, customer) in enumerate([
            (QuoteStatus.NEW, "Acme Corp"),
            (QuoteStatus.NEW, "Beta Industries"),
            (QuoteStatus.IN_REVIEW, "Gamma LLC"),
            (QuoteStatus.NEEDS_PRICING, "Delta Co"),
            (QuoteStatus.READY, "Epsilon Inc"),
            (QuoteStatus.SENT, "Zeta Partners"),
        ]):
            q = Quote(
                quote_number=f"QU-2026-{1000 + i}",
                status=status,
                customer_name_raw=customer,
                sender_name=f"Sender {i}",
                created_at=datetime.utcnow() - timedelta(hours=i),
            )
            if status == QuoteStatus.IN_REVIEW:
                q.reviewed_by = user.id
                q.review_started_at = datetime.utcnow()
            _db.session.add(q)
            _db.session.flush()

            # Add line items
            li = QuoteLineItem(
                quote_id=q.id,
                product_type="sleeve",
                description=f"Test item {i}",
                quantity=10,
                unit_price=100.00 if status != QuoteStatus.NEEDS_PRICING else 0,
                line_total=1000.00 if status != QuoteStatus.NEEDS_PRICING else 0,
            )
            _db.session.add(li)
            quotes.append(q)

        _db.session.commit()
        return quotes


def test_dashboard_redirects_to_quotes(client):
    resp = client.get("/")
    assert resp.status_code == 302
    assert "/quotes/" in resp.headers["Location"]


def test_queue_page_loads(client, seed_quotes):
    resp = client.get("/quotes/")
    assert resp.status_code == 200
    assert b"Quote Queue" in resp.data


def test_queue_shows_all_quotes(client, seed_quotes):
    resp = client.get("/quotes/")
    assert b"QU-2026-1000" in resp.data
    assert b"QU-2026-1005" in resp.data
    assert b"Acme Corp" in resp.data
    assert b"Zeta Partners" in resp.data


def test_queue_filter_by_status(client, seed_quotes):
    resp = client.get("/quotes/?status=new")
    assert b"Acme Corp" in resp.data
    assert b"Beta Industries" in resp.data
    assert b"Epsilon Inc" not in resp.data


def test_queue_search(client, seed_quotes):
    resp = client.get("/quotes/?q=Acme")
    assert b"Acme Corp" in resp.data
    assert b"Beta Industries" not in resp.data


def test_queue_search_by_quote_number(client, seed_quotes):
    resp = client.get("/quotes/?q=QU-2026-1002")
    assert b"Gamma LLC" in resp.data
    assert b"Acme Corp" not in resp.data


def test_queue_htmx_returns_partial(client, seed_quotes):
    resp = client.get("/quotes/?status=all", headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert b"queue-body" in resp.data
    # Should NOT contain the full page layout
    assert b"<!doctype html>" not in resp.data


def test_badge_count(client, seed_quotes):
    resp = client.get("/quotes/badge")
    assert resp.status_code == 200
    # Should show 2 (two NEW quotes)
    assert b"2" in resp.data


def test_badge_count_empty(client):
    resp = client.get("/quotes/badge")
    assert resp.status_code == 200
    assert resp.data.strip() == b""


def test_needs_pricing_display(client, seed_quotes):
    resp = client.get("/quotes/?status=needs_pricing")
    assert b"Needs Pricing" in resp.data


def test_status_badges_rendered(client, seed_quotes):
    resp = client.get("/quotes/")
    assert b"status-new" in resp.data
    assert b"status-in_review" in resp.data
    assert b"status-ready" in resp.data


def test_reviewer_shown(client, seed_quotes):
    resp = client.get("/quotes/")
    assert b"Jamee" in resp.data


def test_claim_quote(client, seed_quotes):
    with client.application.app_context():
        q = _db.session.query(Quote).filter_by(status=QuoteStatus.NEW).first()
        user = _db.session.query(User).first()

    resp = client.post(f"/quotes/{q.id}/claim", data={"user_id": user.id})
    assert resp.status_code == 200
    assert resp.json["ok"] is True

    with client.application.app_context():
        updated = _db.session.get(Quote, q.id)
        assert updated.status == QuoteStatus.IN_REVIEW
        assert updated.reviewed_by == user.id
        assert updated.review_started_at is not None


def test_release_quote(client, seed_quotes):
    with client.application.app_context():
        q = _db.session.query(Quote).filter_by(status=QuoteStatus.IN_REVIEW).first()

    resp = client.post(f"/quotes/{q.id}/release")
    assert resp.status_code == 200
    assert resp.json["ok"] is True

    with client.application.app_context():
        updated = _db.session.get(Quote, q.id)
        assert updated.reviewed_by is None
        assert updated.review_started_at is None


def test_status_tab_counts(client, seed_quotes):
    resp = client.get("/quotes/")
    # All tab should show total (6)
    assert b"(6)" in resp.data
    # New tab should show (2)
    assert b"New" in resp.data


def test_nav_badge_link(client):
    resp = client.get("/quotes/")
    assert b"nav-badge" in resp.data
    assert b"/quotes/badge" in resp.data
