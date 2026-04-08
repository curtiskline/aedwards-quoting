"""Tests for monitor → database integration (db_writer module)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app import create_app
from app.config import Config
from app.extensions import db
from app.models import AuditLog, Customer, Contact, Quote as DBQuote, QuoteLineItem as DBQuoteLineItem, QuoteStatus, ShipToAddress
from allenedwards.db_writer import write_quote_to_db, _generate_fiscal_quote_number, _normalize_company_name
from allenedwards.outlook import OutlookMessage
from allenedwards.parser import ParsedRFQ, ShipTo
from allenedwards.pricing import Quote as PricingQuote, QuoteLineItem as PricingLineItem


@pytest.fixture()
def app(tmp_path: Path):
    """Create Flask app with a fresh SQLite database per test."""
    db_path = tmp_path / "test.db"
    import os
    previous_database_url = os.environ.get("DATABASE_URL")
    previous_config_database_url = Config.SQLALCHEMY_DATABASE_URI
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path}"
    app = create_app()
    app.config["TESTING"] = True

    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()
    Config.SQLALCHEMY_DATABASE_URI = previous_config_database_url
    if previous_database_url is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = previous_database_url


@pytest.fixture()
def msg():
    return OutlookMessage(
        id="AAMk-test-123",
        subject="RFQ - 12\" sleeves",
        sender_email="buyer@example.com",
        sender_name="John Buyer",
        body_content="Please quote 100 sleeves...",
        body_preview="Please quote 100 sleeves...",
        received_datetime="2026-04-06T12:00:00Z",
        has_attachments=False,
        internet_message_id="<test@example.com>",
        body_content_type="text",
    )


@pytest.fixture()
def rfq():
    return ParsedRFQ(
        customer_name="Acme Pipeline Co",
        contact_name="John Buyer",
        contact_email="buyer@example.com",
        contact_phone="555-1234",
        ship_to=ShipTo(
            company="Acme Pipeline Co",
            city="Tulsa",
            state="OK",
            postal_code="74101",
        ),
        po_number="PO-2026-001",
        quote_number=None,
        items=[],
        notes="Rush order",
    )


@pytest.fixture()
def priced_quote():
    items = [
        PricingLineItem(
            sort_order=1,
            product_type="sleeve",
            part_number="SLV-12-375",
            description='12" x 0.375 w/t Full Encirclement Sleeve',
            quantity=100,
            unit_price=Decimal("45.50"),
            total=Decimal("4550.00"),
            weight_per_ft=Decimal("12.5"),
            price_per_lb=Decimal("1.25"),
        ),
        PricingLineItem(
            sort_order=2,
            product_type="sleeve",
            part_number="SLV-8-250",
            description='8" x 0.250 w/t Full Encirclement Sleeve',
            quantity=50,
            unit_price=Decimal("0.00"),
            total=Decimal("0.00"),
            notes="Could not price",
        ),
        PricingLineItem(
            sort_order=99,
            product_type="note",
            part_number="",
            description="Prices valid for 30 days",
            quantity=0,
            unit_price=Decimal("0"),
            total=Decimal("0"),
            is_note=True,
        ),
    ]
    return PricingQuote(
        quote_number="126-001",
        customer_name="Acme Pipeline Co",
        contact_name="John Buyer",
        contact_email="buyer@example.com",
        contact_phone="555-1234",
        ship_to={"company": "Acme Pipeline Co", "city": "Tulsa", "state": "OK"},
        line_items=items,
        subtotal=Decimal("4550.00"),
        shipping_amount=None,
        tax_amount=Decimal("0"),
        total=Decimal("4550.00"),
        notes="Rush order",
        po_number="PO-2026-001",
        project_line="Test Project",
    )


def test_write_quote_creates_records(app, msg, rfq, priced_quote):
    with app.app_context():
        db_quote = write_quote_to_db(msg, rfq, priced_quote, "126-100")

        assert db_quote.id is not None
        assert db_quote.quote_number == "126-100"
        assert db_quote.status == QuoteStatus.NEW
        assert db_quote.source_email_id == "AAMk-test-123"
        assert db_quote.sender_email == "buyer@example.com"
        assert db_quote.sender_name == "John Buyer"
        assert db_quote.subject == "RFQ - 12\" sleeves"
        assert db_quote.customer_name_raw == "Acme Pipeline Co"
        assert db_quote.contact_name == "John Buyer"
        assert db_quote.contact_email == "buyer@example.com"
        assert db_quote.po_number == "PO-2026-001"
        assert db_quote.project_name == "Test Project"
        assert db_quote.ship_to_json["city"] == "Tulsa"
        # Auto-created customer from RFQ data
        assert db_quote.customer_id is not None
        cust = Customer.query.get(db_quote.customer_id)
        assert cust.company_name == "Acme Pipeline Co"
        # Contact auto-created
        assert len(cust.contacts) == 1
        assert cust.contacts[0].email == "buyer@example.com"
        # Ship-to address auto-created
        assert len(cust.ship_to_addresses) == 1
        assert cust.ship_to_addresses[0].city == "Tulsa"

        # 2 real line items (note row skipped)
        assert len(db_quote.line_items) == 2
        li1 = db_quote.line_items[0]
        assert li1.product_type == "sleeve"
        assert li1.quantity == 100
        assert float(li1.unit_price) == 45.50
        assert float(li1.line_total) == 4550.00
        assert li1.specs_json["weight_per_ft"] == "12.5"

        # Audit log
        audits = AuditLog.query.filter_by(quote_id=db_quote.id).all()
        assert len(audits) == 1
        assert audits[0].action == "created_from_email"


def test_write_zero_quote_sets_needs_pricing(app, msg, rfq, priced_quote):
    priced_quote.subtotal = Decimal("0")
    with app.app_context():
        db_quote = write_quote_to_db(msg, rfq, priced_quote, "126-002")
        assert db_quote.status == QuoteStatus.NEEDS_PRICING


def test_customer_auto_match_by_name(app, msg, rfq, priced_quote):
    with app.app_context():
        cust = Customer(company_name="Acme Pipeline Co", discount_pct=0)
        db.session.add(cust)
        db.session.commit()
        cust_id = cust.id

        db_quote = write_quote_to_db(msg, rfq, priced_quote, "126-003")
        assert db_quote.customer_id == cust_id


def test_customer_auto_match_by_email(app, msg, rfq, priced_quote):
    rfq.customer_name = "Different Name"
    with app.app_context():
        cust = Customer(company_name="Acme Pipeline Co", discount_pct=0)
        db.session.add(cust)
        db.session.flush()
        contact = Contact(customer_id=cust.id, name="John", email="buyer@example.com")
        db.session.add(contact)
        db.session.commit()
        cust_id = cust.id

        db_quote = write_quote_to_db(msg, rfq, priced_quote, "126-004")
        assert db_quote.customer_id == cust_id


def test_fiscal_quote_number_sequence(app):
    with app.app_context():
        num1 = _generate_fiscal_quote_number()
        assert num1 == "126-001"

        # Write a quote to advance the sequence
        q = DBQuote(quote_number="126-001", status=QuoteStatus.NEW)
        db.session.add(q)
        db.session.commit()

        num2 = _generate_fiscal_quote_number()
        assert num2 == "126-002"


# --- Normalize company name tests ---

def test_normalize_strips_legal_suffixes():
    assert _normalize_company_name("Acme Pipeline Inc.") == "acme pipeline"
    assert _normalize_company_name("Acme Pipeline, LLC") == "acme pipeline"
    assert _normalize_company_name("Acme Pipeline Corp") == "acme pipeline"
    assert _normalize_company_name("Acme Pipeline Ltd.") == "acme pipeline"
    assert _normalize_company_name("Acme Pipeline Co.") == "acme pipeline"


def test_normalize_case_insensitive():
    assert _normalize_company_name("ACME PIPELINE") == _normalize_company_name("acme pipeline")


def test_normalize_strips_punctuation_and_whitespace():
    assert _normalize_company_name("  Acme   Pipeline,  Inc. ") == "acme pipeline"


# --- Dedup / normalized matching tests ---

def test_dedup_matches_with_suffix_difference(app, msg, rfq, priced_quote):
    """Customer 'Acme Pipeline Co, Inc.' should match RFQ for 'Acme Pipeline Co'."""
    with app.app_context():
        cust = Customer(company_name="Acme Pipeline Co, Inc.", discount_pct=0)
        db.session.add(cust)
        db.session.commit()
        cust_id = cust.id

        db_quote = write_quote_to_db(msg, rfq, priced_quote, "126-010")
        assert db_quote.customer_id == cust_id


def test_dedup_matches_case_difference(app, msg, rfq, priced_quote):
    """Customer 'ACME PIPELINE CO' should match RFQ for 'Acme Pipeline Co'."""
    with app.app_context():
        cust = Customer(company_name="ACME PIPELINE CO", discount_pct=0)
        db.session.add(cust)
        db.session.commit()
        cust_id = cust.id

        db_quote = write_quote_to_db(msg, rfq, priced_quote, "126-011")
        assert db_quote.customer_id == cust_id


# --- New contact on existing customer ---

def test_new_contact_added_to_existing_customer(app, msg, rfq, priced_quote):
    """When a quote comes in for existing customer with a new email, add the contact."""
    with app.app_context():
        cust = Customer(company_name="Acme Pipeline Co", discount_pct=0)
        db.session.add(cust)
        db.session.flush()
        existing_contact = Contact(customer_id=cust.id, name="Old Contact", email="old@example.com")
        db.session.add(existing_contact)
        db.session.commit()
        cust_id = cust.id

        # RFQ has a different email (buyer@example.com)
        db_quote = write_quote_to_db(msg, rfq, priced_quote, "126-012")
        assert db_quote.customer_id == cust_id

        contacts = Contact.query.filter_by(customer_id=cust_id).all()
        emails = {c.email for c in contacts}
        assert "old@example.com" in emails
        assert "buyer@example.com" in emails
        assert len(contacts) == 2


def test_no_duplicate_contact_on_existing_customer(app, msg, rfq, priced_quote):
    """When contact email already exists on customer, don't create a duplicate."""
    with app.app_context():
        cust = Customer(company_name="Acme Pipeline Co", discount_pct=0)
        db.session.add(cust)
        db.session.flush()
        existing_contact = Contact(customer_id=cust.id, name="John Buyer", email="buyer@example.com")
        db.session.add(existing_contact)
        db.session.commit()
        cust_id = cust.id

        db_quote = write_quote_to_db(msg, rfq, priced_quote, "126-013")
        assert db_quote.customer_id == cust_id

        contacts = Contact.query.filter_by(customer_id=cust_id).all()
        assert len(contacts) == 1


def test_no_customer_created_without_name(app, msg, rfq, priced_quote):
    """When RFQ has no customer_name, don't create a customer."""
    rfq.customer_name = None
    rfq.contact_email = "unknown@example.com"
    with app.app_context():
        db_quote = write_quote_to_db(msg, rfq, priced_quote, "126-014")
        assert db_quote.customer_id is None
        assert Customer.query.count() == 0
