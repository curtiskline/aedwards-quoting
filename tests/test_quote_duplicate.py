"""Tests for duplicating quotes to another customer."""

from __future__ import annotations

from datetime import datetime

import pytest

from app import create_app
from app.extensions import db as _db
from app.models import (
    AuditLog,
    Contact,
    Customer,
    Quote,
    QuoteAttachment,
    QuoteLineItem,
    QuoteStatus,
    ShipToAddress,
    User,
)


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
def seeded(app):
    """A sent quote with two line items and an attachment, plus a second customer."""
    with app.app_context():
        original_customer = Customer(company_name="Acme Corp", discount_pct=0)
        other_customer = Customer(company_name="Bravo Builders", discount_pct=0)
        _db.session.add_all([original_customer, other_customer])
        _db.session.flush()
        _db.session.add(
            Contact(
                customer_id=other_customer.id,
                name="Bonnie Bravo",
                email="bonnie@bravo.example.com",
                phone="555-0101",
            )
        )
        _db.session.add(
            ShipToAddress(
                customer_id=other_customer.id,
                address_line1="200 Bravo Way",
                city="Tulsa",
                state="OK",
                postal_code="74103",
                is_default=True,
            )
        )
        q = Quote(
            quote_number="126-042",
            status=QuoteStatus.SENT,
            customer_id=original_customer.id,
            customer_name_raw="Acme Corp",
            contact_name="Alice Acme",
            contact_email="alice@acme.example.com",
            project_name="River Crossing Phase 2",
            notes_customer="Lead time 3 weeks",
            notes_internal="Chip approved pricing",
            source_email_id="msg-123",
            sender_email="alice@acme.example.com",
            sender_name="Alice Acme",
            subject="RFQ River Crossing",
            tax_amount=12.34,
        )
        _db.session.add(q)
        _db.session.flush()
        _db.session.add_all(
            [
                QuoteLineItem(
                    quote_id=q.id,
                    product_type="sleeve",
                    sku="SLV-100",
                    description="12in sleeve",
                    quantity=10,
                    unit_price=25.00,
                    line_total=250.00,
                    specs_json={"diameter": 12},
                    part_number="PN-12",
                    sort_order=1,
                ),
                QuoteLineItem(
                    quote_id=q.id,
                    product_type="bag",
                    description="Denso bag",
                    quantity=4,
                    unit_price=15.00,
                    line_total=60.00,
                    sort_order=2,
                ),
            ]
        )
        _db.session.add(
            QuoteAttachment(
                quote_id=q.id,
                filename="rfq.pdf",
                content_type="application/pdf",
                size_bytes=3,
                content_bytes=b"pdf",
            )
        )
        _db.session.commit()
        yield {
            "quote_id": q.id,
            "original_customer_id": original_customer.id,
            "other_customer_id": other_customer.id,
        }


def _duplicate(client, quote_id, **data):
    return client.post(f"/quotes/{quote_id}/duplicate", data=data)


def _new_quote(app, seeded):
    with app.app_context():
        return (
            _db.session.query(Quote)
            .filter(Quote.id != seeded["quote_id"])
            .one()
        )


def test_duplicate_to_existing_customer_copies_line_items(client, app, seeded):
    resp = _duplicate(client, seeded["quote_id"], customer_id=seeded["other_customer_id"])
    assert resp.status_code == 302

    with app.app_context():
        new = _db.session.query(Quote).filter(Quote.id != seeded["quote_id"]).one()
        assert resp.headers["Location"].endswith(f"/quotes/{new.id}")
        assert new.quote_number != "126-042"
        assert new.status == QuoteStatus.NEW
        assert new.customer_id == seeded["other_customer_id"]
        assert new.customer_name_raw == "Bravo Builders"
        assert new.contact_name == "Bonnie Bravo"
        assert new.contact_email == "bonnie@bravo.example.com"
        assert new.ship_to_json["address_line1"] == "200 Bravo Way"
        assert new.project_name == "River Crossing Phase 2"
        assert new.notes_customer == "Lead time 3 weeks"
        assert new.notes_internal == "Chip approved pricing"
        assert float(new.tax_amount) == 12.34

        items = sorted(new.line_items, key=lambda li: li.sort_order)
        assert [(li.product_type, li.description, float(li.quantity), float(li.unit_price), float(li.line_total)) for li in items] == [
            ("sleeve", "12in sleeve", 10.0, 25.0, 250.0),
            ("bag", "Denso bag", 4.0, 15.0, 60.0),
        ]
        assert items[0].sku == "SLV-100"
        assert items[0].part_number == "PN-12"
        assert items[0].specs_json == {"diameter": 12}


def test_duplicate_to_new_customer_name(client, app, seeded):
    resp = _duplicate(client, seeded["quote_id"], new_customer_name="Charlie Coatings")
    assert resp.status_code == 302

    new = _new_quote(app, seeded)
    assert new.customer_id is None
    assert new.customer_name_raw == "Charlie Coatings"
    assert new.contact_name is None
    assert new.ship_to_json is None


def test_duplicate_does_not_copy_email_sent_state_or_attachments(client, app, seeded):
    _duplicate(client, seeded["quote_id"], customer_id=seeded["other_customer_id"])

    with app.app_context():
        new = _db.session.query(Quote).filter(Quote.id != seeded["quote_id"]).one()
        assert new.status == QuoteStatus.NEW
        assert new.source_email_id is None
        assert new.sender_email is None
        assert new.sender_name is None
        assert new.subject is None
        assert new.deleted_at is None
        assert new.reviewed_by is None
        assert new.attachments == []
        # original keeps its attachment
        original = _db.session.get(Quote, seeded["quote_id"])
        assert len(original.attachments) == 1


def test_duplicate_writes_audit_trail_both_directions(client, app, seeded):
    _duplicate(client, seeded["quote_id"], customer_id=seeded["other_customer_id"])

    with app.app_context():
        new = _db.session.query(Quote).filter(Quote.id != seeded["quote_id"]).one()
        from_log = (
            _db.session.query(AuditLog)
            .filter_by(quote_id=new.id, action="duplicated_from")
            .one()
        )
        assert from_log.details["source_quote_id"] == seeded["quote_id"]
        assert from_log.details["source_quote_number"] == "126-042"
        to_log = (
            _db.session.query(AuditLog)
            .filter_by(quote_id=seeded["quote_id"], action="duplicated_to")
            .one()
        )
        assert to_log.details["new_quote_id"] == new.id
        # Audit history is not copied onto the new quote beyond the duplication marker.
        assert (
            _db.session.query(AuditLog).filter_by(quote_id=new.id).count() == 1
        )


def test_duplicate_original_quote_unchanged(client, app, seeded):
    _duplicate(client, seeded["quote_id"], customer_id=seeded["other_customer_id"])

    with app.app_context():
        original = _db.session.get(Quote, seeded["quote_id"])
        assert original.status == QuoteStatus.SENT
        assert original.customer_id == seeded["original_customer_id"]
        assert len(original.line_items) == 2


def test_duplicate_requires_customer_choice(client, app, seeded):
    resp = _duplicate(client, seeded["quote_id"])
    assert resp.status_code == 400

    with app.app_context():
        assert _db.session.query(Quote).count() == 1


def test_duplicate_unknown_customer_400(client, app, seeded):
    resp = _duplicate(client, seeded["quote_id"], customer_id=99999)
    assert resp.status_code == 400

    with app.app_context():
        assert _db.session.query(Quote).count() == 1


def test_duplicate_deleted_quote_404(client, app, seeded):
    assert client.post(f"/quotes/{seeded['quote_id']}/delete").status_code == 302

    resp = _duplicate(client, seeded["quote_id"], customer_id=seeded["other_customer_id"])
    assert resp.status_code == 404
    resp = client.get(f"/quotes/{seeded['quote_id']}/duplicate-form")
    assert resp.status_code == 404


def test_duplicate_requires_login(app, seeded):
    anon = app.test_client()
    resp = anon.post(
        f"/quotes/{seeded['quote_id']}/duplicate",
        data={"customer_id": seeded["other_customer_id"]},
    )
    assert resp.status_code == 302
    assert "/quotes/" not in resp.headers["Location"]

    with app.app_context():
        assert _db.session.query(Quote).count() == 1


def test_duplicate_form_lists_customers(client, seeded):
    resp = client.get(f"/quotes/{seeded['quote_id']}/duplicate-form")
    assert resp.status_code == 200
    assert b"Acme Corp" in resp.data
    assert b"Bravo Builders" in resp.data
    assert f"/quotes/{seeded['quote_id']}/duplicate".encode() in resp.data


def test_duplicate_button_rendered_on_detail(client, seeded):
    resp = client.get(f"/quotes/{seeded['quote_id']}")
    assert f"/quotes/{seeded['quote_id']}/duplicate-form".encode() in resp.data


def test_duplicate_generates_sequential_quote_number(client, app, seeded):
    _duplicate(client, seeded["quote_id"], customer_id=seeded["other_customer_id"])

    new = _new_quote(app, seeded)
    year_prefix = f"1{datetime.utcnow().year % 100}"
    assert new.quote_number.startswith(f"{year_prefix}-")
