"""Tests for monitor → database integration (db_writer module)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app import create_app
from app.config import Config
from app.extensions import db
from app.models import (
    AuditLog,
    Customer,
    Contact,
    ProcessedInboundEmail,
    Quote as DBQuote,
    QuoteAttachment,
    QuoteLineItem as DBQuoteLineItem,
    QuoteStatus,
    ShipToAddress,
)
from allenedwards.monitor import InboxMonitor
from allenedwards.db_writer import (
    MAX_ATTACHMENT_BYTES,
    write_quote_to_db,
    _generate_fiscal_quote_number,
    _normalize_company_name,
    _match_customer,
    _name_similarity,
    _extract_email_domain,
)
from allenedwards.outlook import OutlookMessage
from allenedwards.outlook import OutlookAttachment
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
            sku="S-12.34-38-50-10",
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
        attachments = [
            OutlookAttachment(
                filename="rfq.pdf",
                content_bytes=b"%PDF-test",
                content_type="application/pdf",
            )
        ]
        db_quote = write_quote_to_db(msg, rfq, priced_quote, "126-100", attachments=attachments)

        assert db_quote.id is not None
        assert db_quote.quote_number == "126-100"
        assert db_quote.status == QuoteStatus.NEEDS_PRICING
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
        assert cust.ship_to_addresses[0].human_confirmed is False

        # 2 real line items (note row skipped)
        assert len(db_quote.line_items) == 2
        assert len(db_quote.attachments) == 1
        assert db_quote.attachments[0].filename == "rfq.pdf"
        assert db_quote.attachments[0].content_type == "application/pdf"
        assert db_quote.attachments[0].size_bytes == len(b"%PDF-test")
        assert db_quote.attachments[0].is_stored is True
        assert db_quote.attachments[0].content_bytes == b"%PDF-test"
        li1 = db_quote.line_items[0]
        assert li1.product_type == "sleeve"
        assert li1.quantity == 100
        assert float(li1.unit_price) == 45.50
        assert float(li1.line_total) == 4550.00
        assert li1.specs_json["weight_per_ft"] == "12.5"
        # Single identifier: a generated part_number wins over the catalog-match
        # SKU, which is folded in only when part_number is blank.
        assert li1.part_number == "SLV-12-375"

        # Audit log
        audits = AuditLog.query.filter_by(quote_id=db_quote.id).all()
        assert len(audits) == 1
        assert audits[0].action == "created_from_email"


def test_atomic_claim_crash_is_reprocessed_from_state_file(app, tmp_path, msg, rfq, priced_quote):
    """A saved state entry retries when its in-transaction claim rolled back."""
    monitor = InboxMonitor(
        outlook=MagicMock(),
        provider=MagicMock(),
        poll_interval_seconds=60,
        state_path=tmp_path / "state.json",
        output_dir=tmp_path / "quotes",
        enable_db_writes=True,
        enable_outlook_drafts=False,
        flask_app=app,
    )
    monitor.state.add(msg.id)

    original_flush = db.session.flush
    flush_count = 0

    def crash_after_claim(*args, **kwargs):
        nonlocal flush_count
        flush_count += 1
        if flush_count == 2:
            raise RuntimeError("simulated crash after claim flush")
        return original_flush(*args, **kwargs)

    with patch.object(db.session, "flush", side_effect=crash_after_claim):
        with pytest.raises(RuntimeError, match="after claim flush"):
            monitor._write_to_db(msg, [(rfq, priced_quote, "126-claim")], [])

    with app.app_context():
        assert ProcessedInboundEmail.query.filter_by(source_email_id=msg.id).count() == 0
        assert DBQuote.query.filter_by(source_email_id=msg.id).count() == 0

    retry_outlook = MagicMock()
    retry_outlook.fetch_messages.return_value = [msg]
    retry = InboxMonitor(
        outlook=retry_outlook,
        provider=MagicMock(),
        poll_interval_seconds=60,
        state_path=tmp_path / "state.json",
        output_dir=tmp_path / "quotes",
        enable_db_writes=True,
        enable_outlook_drafts=False,
        flask_app=app,
    )
    retry._process_message = MagicMock(
        side_effect=lambda _msg: (retry._write_to_db(_msg, [(rfq, priced_quote, "126-claim")], []), True)[1]
    )

    assert retry.run_once() == 1
    retry._process_message.assert_called_once_with(msg)

    with app.app_context():
        assert ProcessedInboundEmail.query.filter_by(source_email_id=msg.id).count() == 1
        assert DBQuote.query.filter_by(source_email_id=msg.id).count() == 1


def test_write_quote_without_attachments_leaves_attachment_list_empty(app, msg, rfq, priced_quote):
    with app.app_context():
        db_quote = write_quote_to_db(msg, rfq, priced_quote, "126-101")

        attachments = QuoteAttachment.query.filter_by(quote_id=db_quote.id).all()
        assert attachments == []


def test_write_quote_stores_metadata_only_for_oversized_attachment(app, msg, rfq, priced_quote):
    with app.app_context():
        attachments = [
            OutlookAttachment(
                filename="large-step.zip",
                content_bytes=b"x" * (MAX_ATTACHMENT_BYTES + 1),
                content_type="application/zip",
            )
        ]
        db_quote = write_quote_to_db(msg, rfq, priced_quote, "126-102", attachments=attachments)

        saved = QuoteAttachment.query.filter_by(quote_id=db_quote.id).one()
        assert saved.filename == "large-step.zip"
        assert saved.content_type == "application/zip"
        assert saved.size_bytes == MAX_ATTACHMENT_BYTES + 1
        assert saved.is_stored is False
        assert saved.content_bytes == b""


def test_write_zero_quote_sets_needs_pricing(app, msg, rfq, priced_quote):
    priced_quote.subtotal = Decimal("0")
    with app.app_context():
        db_quote = write_quote_to_db(msg, rfq, priced_quote, "126-002")
        assert db_quote.status == QuoteStatus.NEEDS_PRICING


def test_write_quote_with_any_unpriced_line_item_sets_needs_pricing(app, msg, rfq, priced_quote):
    priced_quote.subtotal = Decimal("4550.00")
    with app.app_context():
        db_quote = write_quote_to_db(msg, rfq, priced_quote, "126-002A")
        assert db_quote.status == QuoteStatus.NEEDS_PRICING
        assert len(db_quote.line_items) == 2
        assert float(db_quote.line_items[1].unit_price) == 0.0
        assert float(db_quote.line_items[1].line_total) == 0.0


def test_customer_auto_match_by_name(app, msg, rfq, priced_quote):
    with app.app_context():
        cust = Customer(company_name="Acme Pipeline Co", discount_pct=0)
        db.session.add(cust)
        db.session.commit()
        cust_id = cust.id

        db_quote = write_quote_to_db(msg, rfq, priced_quote, "126-003")
        assert db_quote.customer_id == cust_id


def test_customer_auto_match_by_email_when_no_customer_name(app, msg, rfq, priced_quote):
    rfq.customer_name = None
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


class _MultiRFQProvider:
    """Stub LLM provider: classifies as RFQ and extracts TWO quotes from one email.

    Mirrors the shape the real parser expects so the monitor takes its multi-RFQ
    numbering branch ("{base}-01", "{base}-02").
    """

    def _quote(self, city):
        return {
            "project_line": None,
            "ship_to": {"company": "Test Corp", "city": city, "state": "TX"},
            "po_number": None,
            "items": [
                {
                    "product_type": "sleeve",
                    "quantity": 10,
                    "diameter": "6.625",
                    "wall_thickness": "0.25",
                    "grade": "50",
                    "length_ft": 40,
                    "milling": False,
                    "painting": False,
                    "description": "6-5/8 x 0.25 GR50 sleeve",
                }
            ],
        }

    def complete_json(self, prompt: str, system: str = "") -> dict:
        if "Classify" in system or "classifier" in system:
            return {"is_rfq": True, "confidence": 0.95, "reason": "pipe products"}
        return {
            "customer_name": "Test Corp",
            "contact_name": "Test User",
            "contact_email": "test@example.com",
            "contact_phone": None,
            "quote_number": None,
            "quotes": [self._quote("Houston"), self._quote("Dallas")],
            "urgency": "normal",
            "notes": None,
            "confidence": 0.9,
        }


def test_multi_rfq_email_numbers_never_alias_existing_quote(app, tmp_path):
    """A multi-RFQ email's base + -NN children never alias an existing quote (task 378).

    End-to-end regression for the 2026-08-13 symptom where a 2-RFQ email was
    numbered 126-003-01/-02 and appeared to be revisions of the UNRELATED
    standalone quote 126-003. Root cause was the T374 quote-number generator
    collision (fixed): the base equalled an existing quote number, so the -NN
    children landed inside that quote's number namespace.

    We seed the exact hazard: standalones 126-001..126-003 plus a prior multi-RFQ
    child 126-003-01. The OLD generator (string-max + split("-")[-1]) picks that
    child as the lexicographic max, reads "01" as the sequence, and hands back base
    126-002 — whose children 126-002-01/-02 nest under the unrelated standalone
    126-002 (the aliasing this task is about). The fixed generator parses the
    sequence segment, returns base 126-004, and its children (126-004-01/-02) alias
    nothing. Drive the real monitor path so the generator, the inline -NN suffixing,
    and the DB write are all exercised.
    """
    from allenedwards.outlook import OutlookMessage

    with app.app_context():
        for number in ("126-001", "126-002", "126-003", "126-003-01"):
            db.session.add(DBQuote(quote_number=number, status=QuoteStatus.NEW))
        db.session.commit()
        existing_numbers = {q.quote_number for q in DBQuote.query.all()}

    msg = OutlookMessage(
        id="multi-rfq-msg",
        subject="RFQ - two sleeve orders",
        sender_name="Test User",
        sender_email="test@example.com",
        body_preview="Please quote two sleeve orders",
        body_content="Please quote 10 pcs 6-5/8 x 0.25 GR50 sleeves for two sites",
        body_content_type="text",
        internet_message_id="<multi@example.com>",
        received_datetime="2026-08-14T12:00:00Z",
        has_attachments=False,
    )

    monitor = InboxMonitor(
        outlook=MagicMock(),
        provider=_MultiRFQProvider(),
        poll_interval_seconds=60,
        state_path=tmp_path / "state.json",
        output_dir=tmp_path / "quotes",
        enable_db_writes=True,
        enable_outlook_drafts=False,
        flask_app=app,
    )

    assert monitor._process_message(msg) is True

    with app.app_context():
        new_quotes = DBQuote.query.filter_by(source_email_id="multi-rfq-msg").all()
        assert len(new_quotes) == 2
        new_numbers = sorted(q.quote_number for q in new_quotes)
        assert new_numbers == ["126-004-01", "126-004-02"]

        for number in new_numbers:
            # No new number equals an existing quote number...
            assert number not in existing_numbers
            # ...and none nests under (reads as a revision/child of) an existing
            # quote, which is exactly the folding this task is about.
            for existing in existing_numbers:
                assert not number.startswith(f"{existing}-"), (
                    f"{number} nests under unrelated existing quote {existing}"
                )


def test_fiscal_quote_number_ignores_revision_suffix(app):
    """A revision-suffixed number must not be read as the sequence.

    Regression for the 2026-08-13 prod outage: with a revision present
    (126-097-R1), the old split("-")[-1] logic read the revision suffix as the
    sequence and regenerated an existing base number (126-001/126-003), tripping
    the UNIQUE constraint and blocking ALL new auto-quotes. The next number must
    follow the SEQUENCE segment: 126-098.
    """
    with app.app_context():
        db.session.add(DBQuote(quote_number="126-097", status=QuoteStatus.NEW))
        db.session.add(DBQuote(quote_number="126-097-R1", status=QuoteStatus.NEW))
        db.session.commit()

        assert _generate_fiscal_quote_number() == "126-098"


def test_fiscal_quote_number_ignores_numeric_revision_suffix(app):
    """Same defense for a numeric revision suffix (126-097-02 style).

    The old code's split("-")[-1] would read "02" here and produce 126-003.
    """
    with app.app_context():
        db.session.add(DBQuote(quote_number="126-097", status=QuoteStatus.NEW))
        db.session.add(DBQuote(quote_number="126-097-02", status=QuoteStatus.NEW))
        db.session.commit()

        assert _generate_fiscal_quote_number() == "126-098"


def test_fiscal_quote_number_no_collision_over_sequence_with_revisions(app):
    """Generating N sequential quotes never collides even when revisions exist."""
    with app.app_context():
        # Seed a base and a revision of it.
        db.session.add(DBQuote(quote_number="126-005", status=QuoteStatus.NEW))
        db.session.add(DBQuote(quote_number="126-005-R1", status=QuoteStatus.NEW))
        db.session.commit()

        seen = {"126-005", "126-005-R1"}
        for _ in range(5):
            num = _generate_fiscal_quote_number()
            assert num not in seen, f"collision on {num}"
            seen.add(num)
            # Persist it so the next call advances (mirrors real inserts).
            db.session.add(DBQuote(quote_number=num, status=QuoteStatus.NEW))
            db.session.commit()

        # Sequence continued past the seeded base, ignoring the revision suffix.
        assert "126-006" in seen
        assert "126-010" in seen


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


# --- Fuzzy name matching tests ---

def test_fuzzy_match_minor_typo(app, msg, rfq, priced_quote):
    """Slight spelling variation should still match (above threshold)."""
    with app.app_context():
        cust = Customer(company_name="Acme Pipline Co", discount_pct=0)  # typo: Pipline
        db.session.add(cust)
        db.session.commit()
        cust_id = cust.id

        rfq.customer_name = "Acme Pipeline Co"
        db_quote = write_quote_to_db(msg, rfq, priced_quote, "126-020")
        assert db_quote.customer_id == cust_id


def test_fuzzy_no_match_different_company(app, msg, rfq, priced_quote):
    """Completely different company name should NOT match — leave unmatched."""
    with app.app_context():
        cust = Customer(company_name="Baker Hughes Corporation", discount_pct=0)
        db.session.add(cust)
        db.session.commit()

        rfq.customer_name = "Acme Pipeline Co"
        rfq.contact_email = None
        db_quote = write_quote_to_db(msg, rfq, priced_quote, "126-021")
        # Should create a new customer, not match Baker Hughes
        assert db_quote.customer_id is not None
        new_cust = Customer.query.get(db_quote.customer_id)
        assert new_cust.company_name == "Acme Pipeline Co"


def test_short_name_requires_exact_match(app, msg, rfq, priced_quote):
    """Very short company names should require exact normalized match."""
    with app.app_context():
        cust = Customer(company_name="Apex", discount_pct=0)
        db.session.add(cust)
        db.session.commit()

        rfq.customer_name = "Ajax"
        rfq.contact_email = None
        db_quote = write_quote_to_db(msg, rfq, priced_quote, "126-022")
        # Should NOT match Apex — short names need exact match
        new_cust = Customer.query.get(db_quote.customer_id)
        assert new_cust.company_name == "Ajax"


def test_short_name_exact_match_works(app, msg, rfq, priced_quote):
    """Short name that exactly matches (after normalization) should still work."""
    with app.app_context():
        cust = Customer(company_name="Apex Inc.", discount_pct=0)
        db.session.add(cust)
        db.session.commit()
        cust_id = cust.id

        rfq.customer_name = "Apex"
        db_quote = write_quote_to_db(msg, rfq, priced_quote, "126-023")
        assert db_quote.customer_id == cust_id


# --- Email domain matching tests ---

def test_match_by_email_domain(app, msg, rfq, priced_quote):
    """Corporate email domain should match when it uniquely identifies a customer."""
    with app.app_context():
        cust = Customer(company_name="Acme Pipeline Co", discount_pct=0)
        db.session.add(cust)
        db.session.flush()
        contact = Contact(customer_id=cust.id, name="Jane", email="jane@acmepipeline.com")
        db.session.add(contact)
        db.session.commit()
        cust_id = cust.id

        rfq.customer_name = None  # no name to match on
        rfq.contact_email = "bob@acmepipeline.com"
        db_quote = write_quote_to_db(msg, rfq, priced_quote, "126-024")
        assert db_quote.customer_id == cust_id


def test_no_match_generic_email_domain(app, msg, rfq, priced_quote):
    """Gmail/Yahoo/etc domains should NOT trigger domain matching."""
    with app.app_context():
        cust = Customer(company_name="Some Company", discount_pct=0)
        db.session.add(cust)
        db.session.flush()
        contact = Contact(customer_id=cust.id, name="Jane", email="jane@gmail.com")
        db.session.add(contact)
        db.session.commit()

        rfq.customer_name = None
        rfq.contact_email = "bob@gmail.com"
        db_quote = write_quote_to_db(msg, rfq, priced_quote, "126-025")
        # Should NOT match — gmail is a generic domain
        assert db_quote.customer_id is None


def test_no_domain_match_when_ambiguous(app, msg, rfq, priced_quote):
    """When two customers share the same email domain, don't match either."""
    with app.app_context():
        cust1 = Customer(company_name="Acme Houston", discount_pct=0)
        db.session.add(cust1)
        db.session.flush()
        Contact(customer_id=cust1.id, name="A", email="a@acme.com")
        db.session.add(Contact(customer_id=cust1.id, name="A", email="a@acme.com"))

        cust2 = Customer(company_name="Acme Dallas", discount_pct=0)
        db.session.add(cust2)
        db.session.flush()
        db.session.add(Contact(customer_id=cust2.id, name="B", email="b@acme.com"))
        db.session.commit()

        rfq.customer_name = None
        rfq.contact_email = "c@acme.com"
        db_quote = write_quote_to_db(msg, rfq, priced_quote, "126-026")
        # Ambiguous domain — should not match
        assert db_quote.customer_id is None


# --- Priority / signal ordering tests ---

def test_parsed_customer_name_beats_conflicting_exact_email(app, msg, rfq, priced_quote):
    """Parsed customer name should win over a conflicting sender/contact email match."""
    with app.app_context():
        cust_name = Customer(company_name="Acme Pipeline Co", discount_pct=0)
        db.session.add(cust_name)
        db.session.flush()

        cust_email = Customer(company_name="Totally Different Corp", discount_pct=0)
        db.session.add(cust_email)
        db.session.flush()
        db.session.add(Contact(customer_id=cust_email.id, name="John", email="buyer@example.com"))
        db.session.commit()
        email_cust_id = cust_email.id

        rfq.customer_name = "Acme Pipeline Co"
        rfq.contact_email = "buyer@example.com"
        db_quote = write_quote_to_db(msg, rfq, priced_quote, "126-027")
        assert db_quote.customer_id == cust_name.id


def test_parsed_customer_name_blocks_sender_domain_fallback(app, msg, rfq, priced_quote):
    """When parsed customer name is present, do not fall back to a sender-domain customer."""
    with app.app_context():
        sender_customer = Customer(company_name="918.software", discount_pct=0)
        db.session.add(sender_customer)
        db.session.flush()
        db.session.add(Contact(customer_id=sender_customer.id, name="Devin", email="devin@918.software"))
        db.session.commit()

        rfq.customer_name = "CenterPoint Energy"
        rfq.contact_email = "devin@918.software"
        msg.sender_email = "devin@918.software"

        db_quote = write_quote_to_db(msg, rfq, priced_quote, "126-028")

        assert db_quote.customer_id is not None
        linked_customer = Customer.query.get(db_quote.customer_id)
        assert linked_customer.company_name == "CenterPoint Energy"


# --- Helper function unit tests ---

def test_name_similarity_identical():
    assert _name_similarity("acme pipeline", "acme pipeline") == 1.0


def test_name_similarity_reordered_tokens():
    score = _name_similarity("pipeline acme", "acme pipeline")
    assert score == 1.0


def test_name_similarity_very_different():
    score = _name_similarity("acme pipeline", "baker hughes")
    assert score < 0.5


def test_extract_email_domain():
    assert _extract_email_domain("bob@acme.com") == "acme.com"
    assert _extract_email_domain("BOB@ACME.COM") == "acme.com"
    assert _extract_email_domain("") is None
    assert _extract_email_domain("nope") is None


def test_ship_to_json_is_written_in_the_canonical_shape(app, msg, rfq, priced_quote):
    """The RFQ path used to store `street`, which the web editor could not read."""
    rfq.ship_to = ShipTo(
        company="Buckeye Huntington",
        attention="Site Manager",
        street="1234 Pipeline Rd",
        city="Huntington",
        state="IN",
        postal_code="46750",
    )
    quote = write_quote_to_db(msg, rfq, priced_quote, "126-401")

    assert quote.ship_to_json["address_line1"] == "1234 Pipeline Rd"
    assert quote.ship_to_json["company"] == "Buckeye Huntington"
    assert quote.ship_to_json["attention"] == "Site Manager"
    assert "street" not in quote.ship_to_json


def test_bill_to_is_preserved_in_internal_notes_not_in_ship_to(app, msg, rfq, priced_quote):
    """Chip's rule: the signature address is a bill-to. It stays off ship_to (and so
    off freight) but is not discarded — it is the customer's real mailing address."""
    rfq.ship_to = None
    rfq.bill_to = ShipTo(
        company="Azimuth Energy",
        street="No 47-2, Level 2, Jalan Neutron U16/Q, Denai Alam",
        city="Shah Alam",
        state="Selangor",
        postal_code="40160",
        country="Malaysia",
    )
    quote = write_quote_to_db(msg, rfq, priced_quote, "126-402")

    assert quote.ship_to_json is None
    assert "Bill-to (from email signature):" in quote.notes_internal
    assert "Azimuth Energy" in quote.notes_internal
    assert "Shah Alam, Selangor, 40160" in quote.notes_internal
    # The parsed RFQ notes are kept alongside it, not replaced.
    assert "Rush order" in quote.notes_internal


def test_no_bill_to_leaves_notes_internal_untouched(app, msg, rfq, priced_quote):
    rfq.bill_to = None
    quote = write_quote_to_db(msg, rfq, priced_quote, "126-403")
    assert quote.notes_internal == "Rush order"


def test_bill_to_stored_in_bill_to_json_canonical_shape(app, msg, rfq, priced_quote):
    """Task 332: the bill-to is stored in bill_to_json in the canonical
    normalize_ship_to shape (the parser's `street` mapped to `address_line1`), so the
    PDF can render it. It must NOT reach ship_to_json (which drives freight)."""
    rfq.ship_to = None
    rfq.bill_to = ShipTo(
        company="Azimuth Energy",
        street="No 47-2, Level 2, Jalan Neutron U16/Q, Denai Alam",
        city="Shah Alam",
        state="Selangor",
        postal_code="40160",
        country="Malaysia",
    )
    quote = write_quote_to_db(msg, rfq, priced_quote, "126-404")

    assert quote.ship_to_json is None
    assert quote.bill_to_json is not None
    assert quote.bill_to_json["company"] == "Azimuth Energy"
    assert quote.bill_to_json["address_line1"] == "No 47-2, Level 2, Jalan Neutron U16/Q, Denai Alam"
    assert quote.bill_to_json["postal_code"] == "40160"
    assert quote.bill_to_json["country"] == "Malaysia"
    # Canonical shape uses address_line1, never the dataclass's raw `street` key.
    assert "street" not in quote.bill_to_json


def test_no_bill_to_leaves_bill_to_json_null(app, msg, rfq, priced_quote):
    rfq.bill_to = None
    quote = write_quote_to_db(msg, rfq, priced_quote, "126-405")
    assert quote.bill_to_json is None


def test_line_item_specs_persist_pricing_inputs(app, msg, rfq):
    """specs_json carries the specs a line was priced from (task 329).

    The quote editor re-prices from specs_json, so a line written without
    diameter/wall/grade/length cannot be re-priced when a reviewer edits a spec.
    """
    priced = PricingQuote(
        quote_number="126-329",
        customer_name="Acme Pipeline Co",
        contact_name=None,
        contact_email=None,
        contact_phone=None,
        ship_to=None,
        line_items=[
            PricingLineItem(
                sort_order=1,
                product_type="sleeve",
                part_number="S-36-38-65-20",
                description='Half Sole, 36" ID, 3/8" w/t, A572 GR65, 20\' long.',
                quantity=1,
                unit_price=Decimal("4010.05"),
                total=Decimal("4010.05"),
                weight_per_ft=Decimal("72.91"),
                price_per_lb=Decimal("2.75"),
                notes='wall thickness defaulted to 3/8"',
                diameter=36.0,
                wall_thickness=0.375,
                grade=65,
                length_ft=20.0,
            )
        ],
        subtotal=Decimal("4010.05"),
        shipping_amount=None,
        tax_amount=Decimal("0"),
        total=Decimal("4010.05"),
        notes=None,
    )

    with app.app_context():
        db_quote = write_quote_to_db(msg, rfq, priced, "126-329")
        line = DBQuoteLineItem.query.filter_by(quote_id=db_quote.id).one()
        specs = dict(line.specs_json or {})
        assert specs["diameter"] == "36.0"
        assert specs["wall_thickness"] == "0.375"
        assert specs["grade"] == "65"
        assert specs["length_ft"] == "20.0"
        assert specs["milling"] is False
        assert specs["painting"] is False
        # Existing pricing provenance is untouched.
        assert specs["weight_per_ft"] == "72.91"
        assert specs["price_per_lb"] == "2.75"
        assert specs["notes"] == 'wall thickness defaulted to 3/8"'


def test_sku_folds_into_part_number_when_part_number_blank(app, msg, rfq):
    """Task 358: the two identifier columns are collapsed into part_number.

    When pricing did not generate a part_number, the catalog-match SKU is folded
    into part_number so the single identifier is never lost.
    """
    priced = PricingQuote(
        quote_number="126-358",
        customer_name="Acme Pipeline Co",
        contact_name=None,
        contact_email=None,
        contact_phone=None,
        ship_to=None,
        line_items=[
            PricingLineItem(
                sort_order=1,
                product_type="accessory",
                sku="ACC-CATALOG-MATCH",
                part_number="",
                description="Accessory picked from the catalog",
                quantity=2,
                unit_price=Decimal("10.00"),
                total=Decimal("20.00"),
            )
        ],
        subtotal=Decimal("20.00"),
        shipping_amount=None,
        tax_amount=Decimal("0"),
        total=Decimal("20.00"),
        notes=None,
    )

    with app.app_context():
        db_quote = write_quote_to_db(msg, rfq, priced, "126-358")
        line = DBQuoteLineItem.query.filter_by(quote_id=db_quote.id).one()
        assert line.part_number == "ACC-CATALOG-MATCH"
