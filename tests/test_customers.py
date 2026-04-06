"""Tests for customer CRUD and auto-match (task 80)."""

from __future__ import annotations

import json

import pytest

from app import create_app
from app.config import Config
from app.customers import auto_match
from app.extensions import db
from app.models import Contact, Customer, ShipToAddress, User


@pytest.fixture()
def app(tmp_path, monkeypatch):
    """Create a test app with an in-memory-like SQLite DB."""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(Config, "SQLALCHEMY_DATABASE_URI", f"sqlite:///{db_path}")
    monkeypatch.setattr(Config, "TESTING", True, raising=False)
    app = create_app()

    with app.app_context():
        db.create_all()
        user = User(email="owner@example.com", name="Owner", password_hash="")
        user.set_password("secret123")
        db.session.add(user)
        db.session.commit()
        yield app
        db.session.remove()


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
def seed_customer(app):
    """Seed a customer with 2 contacts and 2 ship-to addresses."""
    with app.app_context():
        c = Customer(company_name="Buckeye Pipeline", discount_pct=5.0, notes="High-repeat customer")
        db.session.add(c)
        db.session.flush()

        db.session.add(Contact(customer_id=c.id, name="John Smith", email="john@buckeye.com", phone="918-555-0001"))
        db.session.add(Contact(customer_id=c.id, name="Jane Doe", email="jane@buckeye.com"))

        db.session.add(
            ShipToAddress(
                customer_id=c.id,
                address_line1="100 Pipeline Rd",
                city="Tulsa",
                state="OK",
                postal_code="74101",
                is_default=True,
            )
        )
        db.session.add(
            ShipToAddress(
                customer_id=c.id,
                address_line1="200 Field Dr",
                city="Houston",
                state="TX",
                postal_code="77001",
                is_default=False,
            )
        )
        db.session.commit()
        return c.id


# ---------------------------------------------------------------------------
# Customer list
# ---------------------------------------------------------------------------


def test_customer_list_empty(client):
    resp = client.get("/customers/")
    assert resp.status_code == 200
    assert b"No customers found" in resp.data


def test_customer_list_shows_customer(client, seed_customer):
    resp = client.get("/customers/")
    assert resp.status_code == 200
    assert b"Buckeye Pipeline" in resp.data


def test_customer_search(client, seed_customer):
    resp = client.get("/customers/?q=buckeye")
    assert resp.status_code == 200
    assert b"Buckeye Pipeline" in resp.data

    resp = client.get("/customers/?q=nonexistent")
    assert b"No customers found" in resp.data


# ---------------------------------------------------------------------------
# Customer detail
# ---------------------------------------------------------------------------


def test_customer_detail(client, seed_customer):
    resp = client.get(f"/customers/{seed_customer}")
    assert resp.status_code == 200
    assert b"Buckeye Pipeline" in resp.data
    assert b"John Smith" in resp.data
    assert b"jane@buckeye.com" in resp.data
    assert b"100 Pipeline Rd" in resp.data
    assert b"200 Field Dr" in resp.data


def test_customer_detail_404(client):
    resp = client.get("/customers/9999")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Create customer
# ---------------------------------------------------------------------------


def test_create_customer_form(client):
    resp = client.get("/customers/new")
    assert resp.status_code == 200
    assert b"New Customer" in resp.data


def test_create_customer(client, app):
    resp = client.post(
        "/customers/",
        data={
            "company_name": "Test Corp",
            "discount_pct": "3.5",
            "notes": "A note",
            "contact_name": ["Alice", "Bob"],
            "contact_email": ["alice@test.com", "bob@test.com"],
            "contact_phone": ["555-0001", ""],
            "addr_line1": ["10 Main St"],
            "addr_line2": ["Suite 100"],
            "addr_city": ["Dallas"],
            "addr_state": ["TX"],
            "addr_zip": ["75201"],
            "addr_default": ["0"],
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Test Corp" in resp.data

    with app.app_context():
        cust = db.session.query(Customer).filter_by(company_name="Test Corp").one()
        assert float(cust.discount_pct) == 3.5
        assert len(cust.contacts) == 2
        assert len(cust.ship_to_addresses) == 1
        assert cust.ship_to_addresses[0].is_default is True


# ---------------------------------------------------------------------------
# Edit customer
# ---------------------------------------------------------------------------


def test_edit_customer(client, app, seed_customer):
    resp = client.get(f"/customers/{seed_customer}/edit")
    assert resp.status_code == 200
    assert b"Edit Customer" in resp.data

    resp = client.post(
        f"/customers/{seed_customer}",
        data={
            "company_name": "Buckeye Updated",
            "discount_pct": "10",
            "notes": "",
            "contact_name": ["New Contact"],
            "contact_email": ["new@buckeye.com"],
            "contact_phone": [""],
            "addr_line1": ["300 New St"],
            "addr_line2": [""],
            "addr_city": ["OKC"],
            "addr_state": ["OK"],
            "addr_zip": ["73101"],
            "addr_default": [],
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        cust = db.get_or_404(Customer, seed_customer)
        assert cust.company_name == "Buckeye Updated"
        assert float(cust.discount_pct) == 10
        assert len(cust.contacts) == 1
        assert cust.contacts[0].name == "New Contact"


# ---------------------------------------------------------------------------
# Delete customer
# ---------------------------------------------------------------------------


def test_delete_customer(client, app, seed_customer):
    resp = client.delete(f"/customers/{seed_customer}")
    # Should redirect
    assert resp.status_code in (200, 302)

    with app.app_context():
        assert db.session.get(Customer, seed_customer) is None


# ---------------------------------------------------------------------------
# Auto-match
# ---------------------------------------------------------------------------


def test_auto_match_by_email(app, seed_customer):
    with app.app_context():
        result = auto_match(contact_email="john@buckeye.com")
        assert result is not None
        assert result["confidence"] == 1.0
        assert result["match_type"] == "email"
        assert result["customer"].company_name == "Buckeye Pipeline"


def test_auto_match_by_email_case_insensitive(app, seed_customer):
    with app.app_context():
        result = auto_match(contact_email="JOHN@BUCKEYE.COM")
        assert result is not None
        assert result["confidence"] == 1.0


def test_auto_match_by_company_exact(app, seed_customer):
    with app.app_context():
        result = auto_match(company_name="Buckeye Pipeline")
        assert result is not None
        assert result["confidence"] == 0.95
        assert result["match_type"] == "company_exact"


def test_auto_match_by_company_partial(app, seed_customer):
    with app.app_context():
        result = auto_match(company_name="Buckeye")
        assert result is not None
        assert result["confidence"] == 0.7
        assert result["match_type"] == "company_partial"


def test_auto_match_by_contact_name(app, seed_customer):
    with app.app_context():
        result = auto_match(contact_name="John Smith")
        assert result is not None
        assert result["confidence"] == 0.8
        assert result["match_type"] == "contact_name"


def test_auto_match_no_match(app, seed_customer):
    with app.app_context():
        result = auto_match(company_name="Nonexistent Corp")
        assert result is None


def test_auto_match_api_endpoint(client, seed_customer):
    resp = client.get("/customers/api/match?email=john@buckeye.com")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["match"]["company_name"] == "Buckeye Pipeline"
    assert data["match"]["confidence"] == 1.0
    assert data["match"]["discount_pct"] == 5.0
    assert data["match"]["default_ship_to"]["city"] == "Tulsa"


def test_auto_match_api_no_match(client, seed_customer):
    resp = client.get("/customers/api/match?email=nobody@nothing.com")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["match"] is None


# ---------------------------------------------------------------------------
# HTMX partials
# ---------------------------------------------------------------------------


def test_partial_contact_row(client):
    resp = client.get("/customers/partial/contact-row?idx=2")
    assert resp.status_code == 200
    assert b"contact_name" in resp.data


def test_partial_address_row(client):
    resp = client.get("/customers/partial/address-row?idx=1")
    assert resp.status_code == 200
    assert b"addr_line1" in resp.data
