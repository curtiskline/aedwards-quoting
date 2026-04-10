"""Tests for quote detail route and clickable queue cards."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app import create_app
from app.extensions import db as _db
from app.models import Contact, Customer, Quote, QuoteLineItem, QuoteStatus, ShipToAddress, User


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
def quote_with_items(app):
    """Create a single quote with 3 line items for detail testing."""
    with app.app_context():
        q = Quote(
            quote_number="QU-2026-9001",
            status=QuoteStatus.NEW,
            customer_name_raw="Test Corp",
            contact_name="John Doe",
            contact_email="john@test.com",
            contact_phone="555-1234",
            ship_to_json={"city": "Tulsa", "state": "OK", "postal_code": "74103"},
            notes_customer="Handle with care",
        )
        _db.session.add(q)
        _db.session.flush()

        for i in range(3):
            li = QuoteLineItem(
                quote_id=q.id,
                product_type="sleeve",
                description=f"Test sleeve {i + 1}",
                quantity=10 + i,
                unit_price=25.00 + i,
                line_total=round((10 + i) * (25.00 + i), 2),
                sort_order=i,
                specs_json={"notes": f"Note {i}"} if i == 0 else None,
            )
            _db.session.add(li)

        _db.session.commit()
        yield q.id


@pytest.fixture()
def linked_customer_quote(app):
    with app.app_context():
        customer = Customer(company_name="Kline", discount_pct=0)
        _db.session.add(customer)
        _db.session.flush()

        _db.session.add(
            Contact(
                customer_id=customer.id,
                name="Kline Buyer",
                email="buyer@kline.com",
                phone="555-1000",
            )
        )
        _db.session.add(
            ShipToAddress(
                customer_id=customer.id,
                address_line1="14831 E 38th St",
                address_line2="Suite 200",
                city="Tulsa",
                state="OK",
                postal_code="74134",
                country="US",
                is_default=True,
            )
        )
        quote = Quote(
            quote_number="QU-2026-9002",
            status=QuoteStatus.NEW,
            customer_id=customer.id,
            customer_name_raw=customer.company_name,
            contact_name="Kline Buyer",
            contact_email="buyer@kline.com",
        )
        _db.session.add(quote)
        _db.session.commit()
        yield quote.id, customer.id


# --- GET detail (main_bp route) ---

def test_detail_page_renders(client, quote_with_items):
    resp = client.get(f"/quotes/{quote_with_items}")
    assert resp.status_code == 200
    assert b"QU-2026-9001" in resp.data
    assert b"Test sleeve 1" in resp.data


def test_detail_hydrates_ship_to_from_linked_customer(client, linked_customer_quote, app):
    quote_id, _ = linked_customer_quote
    resp = client.get(f"/quotes/{quote_id}")
    assert resp.status_code == 200
    assert b'14831 E 38th St' in resp.data
    assert b'value="Tulsa"' in resp.data
    with app.app_context():
        q = _db.session.get(Quote, quote_id)
        assert q.ship_to_json is not None
        assert q.ship_to_json["city"] == "Tulsa"


def test_detail_404(client):
    resp = client.get("/quotes/99999")
    assert resp.status_code == 404


# --- Queue cards are clickable ---

def test_queue_cards_have_links(client, quote_with_items):
    resp = client.get("/quotes/")
    assert resp.status_code == 200
    assert f'href="/quotes/{quote_with_items}"'.encode() in resp.data


# --- Status change (main_bp route) ---

def test_update_status(client, quote_with_items, app):
    resp = client.post(
        f"/quotes/{quote_with_items}/status",
        data={"status": "ready"},
    )
    assert resp.status_code == 200
    with app.app_context():
        q = _db.session.get(Quote, quote_with_items)
        assert q.status == QuoteStatus.READY


# --- Meta update (main_bp route) ---

def test_update_meta(client, quote_with_items, app):
    resp = client.post(
        f"/quotes/{quote_with_items}/meta",
        data={
            "quote_number": "QU-2026-UPDATED",
            "project_name": "Big Project",
            "notes_customer": "Rush order",
            "notes_internal": "Priority",
        },
    )
    assert resp.status_code == 200
    with app.app_context():
        q = _db.session.get(Quote, quote_with_items)
        assert q.quote_number == "QU-2026-UPDATED"
        assert q.project_name == "Big Project"


# --- Customer update (main_bp route) ---

def test_update_customer(client, quote_with_items, app):
    resp = client.post(
        f"/quotes/{quote_with_items}/customer",
        data={
            "customer_name_raw": "New Company",
            "contact_name": "Jane",
            "contact_email": "jane@new.com",
            "contact_phone": "555-9999",
            "ship_to_city": "OKC",
            "ship_to_state": "OK",
            "ship_to_postal_code": "73102",
        },
    )
    assert resp.status_code == 200
    with app.app_context():
        q = _db.session.get(Quote, quote_with_items)
        assert q.customer_name_raw == "New Company"
        assert q.ship_to_json["city"] == "OKC"


def test_update_customer_persists_back_to_linked_customer(client, linked_customer_quote, app):
    quote_id, customer_id = linked_customer_quote
    resp = client.post(
        f"/quotes/{quote_id}/customer",
        data={
            "customer_name_raw": "Kline Energy",
            "contact_name": "Taylor Buyer",
            "contact_email": "buyer@kline.com",
            "contact_phone": "555-7777",
            "ship_to_address_line1": "15000 E 39th St",
            "ship_to_address_line2": "",
            "ship_to_city": "Broken Arrow",
            "ship_to_state": "OK",
            "ship_to_postal_code": "74012",
            "ship_to_country": "US",
        },
    )
    assert resp.status_code == 200
    with app.app_context():
        customer = _db.session.get(Customer, customer_id)
        assert customer.company_name == "Kline Energy"
        assert customer.contacts[0].name == "Taylor Buyer"
        assert customer.contacts[0].phone == "555-7777"
        default_addr = next(a for a in customer.ship_to_addresses if a.is_default)
        assert default_addr.address_line1 == "15000 E 39th St"
        assert default_addr.city == "Broken Arrow"
        assert default_addr.postal_code == "74012"


# --- Line items (main_bp routes) ---

def test_add_line_item(client, quote_with_items, app):
    resp = client.post(
        f"/quotes/{quote_with_items}/line-items/add",
        data={
            "product_type": "bag",
            "description": "New bag item",
            "quantity": "5",
            "unit_price": "10.00",
        },
    )
    assert resp.status_code == 200
    with app.app_context():
        q = _db.session.get(Quote, quote_with_items)
        assert len(q.line_items) == 4


def test_delete_line_item(client, quote_with_items, app):
    with app.app_context():
        q = _db.session.get(Quote, quote_with_items)
        item_id = q.line_items[0].id

    resp = client.post(
        f"/quotes/{quote_with_items}/line-items/{item_id}/delete",
    )
    assert resp.status_code == 200
    with app.app_context():
        q = _db.session.get(Quote, quote_with_items)
        assert len(q.line_items) == 2


def test_update_line_item(client, quote_with_items, app):
    with app.app_context():
        q = _db.session.get(Quote, quote_with_items)
        item_id = q.line_items[0].id

    resp = client.post(
        f"/quotes/{quote_with_items}/line-items/{item_id}/update",
        data={
            "product_type": "bag",
            "description": "Updated desc",
            "quantity": "20",
            "unit_price": "30.00",
        },
    )
    assert resp.status_code == 200
    with app.app_context():
        li = _db.session.get(QuoteLineItem, item_id)
        assert li.description == "Updated desc"
        assert float(li.line_total) == 600.00


def test_move_line_item(client, quote_with_items, app):
    with app.app_context():
        q = _db.session.get(Quote, quote_with_items)
        items = sorted(q.line_items, key=lambda x: x.sort_order)
        first_id = items[0].id
        second_id = items[1].id

    resp = client.post(
        f"/quotes/{quote_with_items}/line-items/{first_id}/move",
        data={"direction": "down"},
    )
    assert resp.status_code == 200
    with app.app_context():
        first = _db.session.get(QuoteLineItem, first_id)
        second = _db.session.get(QuoteLineItem, second_id)
        assert first.sort_order > second.sort_order


# --- Send form ---

def test_send_form(client, quote_with_items):
    resp = client.get(f"/quotes/{quote_with_items}/send-form")
    assert resp.status_code == 200
    assert b"send-modal" in resp.data
    assert b"john@test.com" in resp.data
