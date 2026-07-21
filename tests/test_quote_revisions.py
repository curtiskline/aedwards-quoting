"""Tests for linked quote revisions."""

from __future__ import annotations

import pytest

from app import create_app
from app.extensions import db as _db
from app.models import AuditLog, Customer, Quote, QuoteAttachment, QuoteLineItem, QuoteStatus, User


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
    client = app.test_client()
    client.post(
        "/auth/password",
        data={"email": "owner@example.com", "password": "secret123"},
        follow_redirects=True,
    )
    return client


@pytest.fixture()
def source_quote(app):
    with app.app_context():
        customer = Customer(company_name="Acme Corp", discount_pct=0)
        _db.session.add(customer)
        _db.session.flush()
        quote = Quote(
            quote_number="126-042",
            status=QuoteStatus.SENT,
            customer_id=customer.id,
            customer_name_raw="Acme Corp",
            contact_name="Alice Acme",
            contact_email="alice@example.com",
            contact_phone="555-0100",
            project_name="River Crossing",
            notes_customer="Updated scope welcome",
            notes_internal="Keep pricing private",
            po_number="PO-101",
            ship_to_json={"city": "Tulsa", "state": "OK"},
            tax_amount=12.34,
        )
        _db.session.add(quote)
        _db.session.flush()
        _db.session.add_all(
            [
                QuoteLineItem(
                    quote_id=quote.id,
                    product_type="sleeve",
                    sku="SLV-100",
                    description="12in sleeve",
                    quantity=10,
                    unit_price=25,
                    line_total=250,
                    specs_json={"diameter": 12},
                    part_number="PN-12",
                    sort_order=1,
                ),
                QuoteAttachment(
                    quote_id=quote.id,
                    filename="rfq.pdf",
                    content_type="application/pdf",
                    size_bytes=3,
                    content_bytes=b"pdf",
                ),
            ]
        )
        _db.session.commit()
        return quote.id


def _revision_id(app, source_id):
    with app.app_context():
        return _db.session.query(Quote.id).filter(Quote.replaces_quote_id == source_id).one()[0]


def test_revise_copies_details_and_closes_original(client, app, source_quote):
    response = client.post(f"/quotes/{source_quote}/revise")
    assert response.status_code == 302

    with app.app_context():
        source = _db.session.get(Quote, source_quote)
        revision = _db.session.get(Quote, _revision_id(app, source_quote))
        assert response.headers["Location"].endswith(f"/quotes/{revision.id}")
        assert source.status == QuoteStatus.REPLACED
        assert source.replaced_by.id == revision.id
        assert revision.replaces.id == source.id
        assert revision.quote_number == "126-042-R1"
        assert revision.revision_number == 1
        assert revision.status == QuoteStatus.IN_REVIEW
        assert revision.customer_id == source.customer_id
        assert revision.contact_email == source.contact_email
        assert revision.project_name == source.project_name
        assert revision.ship_to_json == source.ship_to_json
        assert revision.ship_to_json is not source.ship_to_json
        assert [
            (item.sku, item.part_number, float(item.line_total)) for item in revision.line_items
        ] == [("SLV-100", "PN-12", 250.0)]
        assert revision.attachments == []
        assert (
            _db.session.query(AuditLog).filter_by(quote_id=source.id, action="revised_by").count()
            == 1
        )
        assert (
            _db.session.query(AuditLog).filter_by(quote_id=revision.id, action="revised_from").count()
            == 1
        )


def test_revisions_chain_from_latest_version_without_forking(client, app, source_quote):
    assert client.post(f"/quotes/{source_quote}/revise").status_code == 302
    first_revision_id = _revision_id(app, source_quote)

    assert client.post(f"/quotes/{first_revision_id}/revise").status_code == 302
    second_revision_id = _revision_id(app, first_revision_id)

    with app.app_context():
        source = _db.session.get(Quote, source_quote)
        first = _db.session.get(Quote, first_revision_id)
        second = _db.session.get(Quote, second_revision_id)
        assert first.status == QuoteStatus.REPLACED
        assert second.replaces_quote_id == first.id
        assert second.replaces_quote_id != source.id
        assert second.quote_number == "126-042-R2"
        assert second.revision_number == 2

    assert client.post(f"/quotes/{source_quote}/revise").status_code == 409


def test_replaced_quotes_are_hidden_from_active_queue_and_history_is_linked(
    client, app, source_quote
):
    assert client.post(f"/quotes/{source_quote}/revise").status_code == 302
    revision_id = _revision_id(app, source_quote)

    queue = client.get("/quotes/")
    assert b"126-042-R1" in queue.data
    assert b">126-042<" not in queue.data
    assert b"Replaced" not in queue.data

    source_detail = client.get(f"/quotes/{source_quote}")
    assert b"This quote was replaced by" in source_detail.data
    assert f"/quotes/{revision_id}".encode() in source_detail.data

    revision_detail = client.get(f"/quotes/{revision_id}")
    assert b"Revision History" in revision_detail.data
    assert b"126-042" in revision_detail.data
    assert b"126-042-R1" in revision_detail.data


def test_revise_requires_login(app, source_quote):
    response = app.test_client().post(f"/quotes/{source_quote}/revise")
    assert response.status_code == 302
    with app.app_context():
        assert _db.session.get(Quote, source_quote).status == QuoteStatus.SENT
