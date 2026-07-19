"""Tests for soft-deleting quotes."""

from __future__ import annotations

import pytest

from app import create_app
from app.extensions import db as _db
from app.models import AuditLog, Customer, Quote, QuoteLineItem, QuoteStatus, User


@pytest.fixture()
def app(tmp_path, monkeypatch):
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
def seeded_quote(app):
    """A quote linked to a customer, with one line item."""
    with app.app_context():
        customer = Customer(company_name="Acme Corp", discount_pct=0)
        _db.session.add(customer)
        _db.session.flush()
        q = Quote(
            quote_number="QU-2026-5001",
            status=QuoteStatus.NEW,
            customer_id=customer.id,
            customer_name_raw="Acme Corp",
        )
        _db.session.add(q)
        _db.session.flush()
        _db.session.add(
            QuoteLineItem(
                quote_id=q.id,
                product_type="sleeve",
                description="Test sleeve",
                quantity=10,
                unit_price=25.00,
                line_total=250.00,
                sort_order=1,
            )
        )
        _db.session.commit()
        yield {"quote_id": q.id, "customer_id": customer.id}


def _delete(client, quote_id):
    return client.post(f"/quotes/{quote_id}/delete")


def test_delete_soft_deletes_and_redirects(client, app, seeded_quote):
    resp = _delete(client, seeded_quote["quote_id"])
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/quotes/")

    with app.app_context():
        quote = _db.session.get(Quote, seeded_quote["quote_id"])
        assert quote is not None  # row still exists (soft delete)
        assert quote.deleted_at is not None
        assert quote.reviewed_by is None
        # line items are retained
        assert len(quote.line_items) == 1


def test_delete_writes_audit_log(client, app, seeded_quote):
    _delete(client, seeded_quote["quote_id"])
    with app.app_context():
        audit = (
            _db.session.query(AuditLog)
            .filter_by(quote_id=seeded_quote["quote_id"], action="deleted")
            .one()
        )
        assert audit.details["quote_number"] == "QU-2026-5001"


def test_deleted_quote_hidden_from_queue_and_counts(client, seeded_quote):
    resp = client.get("/quotes/")
    assert b"QU-2026-5001" in resp.data

    _delete(client, seeded_quote["quote_id"])

    resp = client.get("/quotes/")
    assert b"QU-2026-5001" not in resp.data

    # Nav badge counts no NEW quotes anymore
    resp = client.get("/quotes/badge")
    assert b"badge" not in resp.data


def test_deleted_quote_hidden_from_search(client, seeded_quote):
    _delete(client, seeded_quote["quote_id"])
    resp = client.get("/quotes/?q=Acme")
    assert b"QU-2026-5001" not in resp.data


def test_deleted_quote_detail_404(client, seeded_quote):
    quote_id = seeded_quote["quote_id"]
    _delete(client, quote_id)
    assert client.get(f"/quotes/{quote_id}").status_code == 404


def test_deleted_quote_mutations_404(client, seeded_quote):
    quote_id = seeded_quote["quote_id"]
    _delete(client, quote_id)
    assert client.post(f"/quotes/{quote_id}/meta", data={"project_name": "X"}).status_code == 404
    assert client.post(f"/quotes/{quote_id}/status", data={"status": "ready"}).status_code == 404
    assert client.post(f"/quotes/{quote_id}/line-items/add", data={}).status_code == 404
    assert client.post(f"/quotes/{quote_id}/claim").status_code == 404
    assert client.post(f"/quotes/{quote_id}/delete").status_code == 404


def test_deleted_quote_hidden_from_customer_detail(client, seeded_quote):
    resp = client.get(f"/customers/{seeded_quote['customer_id']}")
    assert b"QU-2026-5001" in resp.data

    _delete(client, seeded_quote["quote_id"])

    resp = client.get(f"/customers/{seeded_quote['customer_id']}")
    assert b"QU-2026-5001" not in resp.data


def test_delete_requires_login(app, seeded_quote):
    anon = app.test_client()
    resp = anon.post(f"/quotes/{seeded_quote['quote_id']}/delete")
    # Redirected to login rather than performing the delete
    assert resp.status_code == 302
    assert "/quotes/" not in resp.headers["Location"]

    with app.app_context():
        quote = _db.session.get(Quote, seeded_quote["quote_id"])
        assert quote.deleted_at is None


def test_quote_numbers_not_reused_after_delete(client, app, seeded_quote):
    """Soft-deleted quotes still reserve their quote number."""
    _delete(client, seeded_quote["quote_id"])
    with app.app_context():
        assert (
            _db.session.query(Quote).filter_by(quote_number="QU-2026-5001").count() == 1
        )


def test_delete_button_rendered_on_detail(client, seeded_quote):
    resp = client.get(f"/quotes/{seeded_quote['quote_id']}")
    assert f"/quotes/{seeded_quote['quote_id']}/delete".encode() in resp.data
    assert b"confirm(" in resp.data
