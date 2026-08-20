"""CP-2a: per-quote confidence score + component signals.

Covers every signal (including the no-history=unknown tolerance case), the
composite math and stored breakdown, the monitor write path, and the
edit-recomputes regression.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app import create_app
from app.confidence import (
    DEFAULT_PRICE_TOLERANCE_PCT,
    FAIL,
    PASS,
    SIGNAL_WEIGHTS,
    UNKNOWN,
    compute_quote_confidence,
    sync_quote_confidence,
)
from app.extensions import db as _db
from app.models import (
    Customer,
    Quote,
    QuoteConfidence,
    QuoteLineItem,
    QuoteStatus,
    ShipToAddress,
    User,
)

LONG_AGO = datetime(2026, 1, 1)

SHIP_TO = {
    "company": "",
    "attention": "",
    "address_line1": "100 Main St",
    "address_line2": "",
    "city": "Tulsa",
    "state": "OK",
    "postal_code": "74103",
    "country": "US",
}


@pytest.fixture()
def app(db_url, monkeypatch):
    from app.config import Config

    monkeypatch.setattr(Config, "SQLALCHEMY_DATABASE_URI", db_url)
    monkeypatch.setattr(Config, "TESTING", True, raising=False)
    monkeypatch.delenv("SEND_EMAIL_ALLOWLIST", raising=False)
    monkeypatch.delenv("CONFIDENCE_PRICE_TOLERANCE_PCT", raising=False)
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


def _make_customer(created_at=LONG_AGO, with_confirmed_ship_to=False) -> Customer:
    customer = Customer(company_name="Acme Pipeline", discount_pct=0)
    customer.created_at = created_at
    _db.session.add(customer)
    _db.session.flush()
    if with_confirmed_ship_to:
        _db.session.add(
            ShipToAddress(
                customer_id=customer.id,
                address_line1=SHIP_TO["address_line1"],
                city=SHIP_TO["city"],
                state=SHIP_TO["state"],
                postal_code=SHIP_TO["postal_code"],
                country=SHIP_TO["country"],
                is_default=True,
                human_confirmed=True,
            )
        )
        _db.session.flush()
    return customer


def _make_quote(customer=None, contact_email="buyer@acme.com", ship_to=None, number="126-500") -> Quote:
    quote = Quote(
        quote_number=number,
        status=QuoteStatus.IN_REVIEW,
        customer_id=customer.id if customer else None,
        contact_email=contact_email,
        ship_to_json=ship_to,
    )
    _db.session.add(quote)
    _db.session.flush()
    return quote


def _add_line(quote, unit_price=100.0, quantity=2, product_type="sleeve", specs=None,
              description="12in sleeve", part_number="SLV-12"):
    item = QuoteLineItem(
        quote_id=quote.id,
        product_type=product_type,
        description=description,
        quantity=quantity,
        unit_price=unit_price,
        line_total=unit_price * quantity,
        specs_json=specs,
        part_number=part_number,
        sort_order=len(quote.line_items) + 1,
    )
    _db.session.add(item)
    _db.session.flush()
    _db.session.expire(quote, ["line_items"])
    return item


def _sent_history_quote(number, unit_price, specs, customer=None):
    """A SENT quote with one priced sleeve line — price-tolerance history."""
    quote = Quote(
        quote_number=number,
        status=QuoteStatus.SENT,
        customer_id=customer.id if customer else None,
    )
    _db.session.add(quote)
    _db.session.flush()
    _add_line(quote, unit_price=unit_price, specs=specs)
    return quote


def _signal(quote, name):
    _, components = compute_quote_confidence(quote)
    return components[name]["status"]


SPECS = {"diameter": "12", "wall_thickness": "0.375", "grade": "50"}


# ---------------------------------------------------------------------------
# decode_clean
# ---------------------------------------------------------------------------


def test_decode_clean_passes_on_clean_quote(app):
    quote = _make_quote(_make_customer())
    _add_line(quote, specs=SPECS)
    assert _signal(quote, "decode_clean") == PASS


def test_decode_clean_fails_on_tbd_marker(app):
    quote = _make_quote(_make_customer())
    _add_line(quote, description="Pricing TBD, contact sales", part_number=None)
    assert _signal(quote, "decode_clean") == FAIL


def test_decode_clean_fails_on_price_stale(app):
    quote = _make_quote(_make_customer())
    _add_line(quote, specs={**SPECS, "price_stale": True})
    assert _signal(quote, "decode_clean") == FAIL


def test_decode_clean_fails_on_defaults_applied_note(app):
    quote = _make_quote(_make_customer())
    _add_line(quote, specs={**SPECS, "notes": 'wall thickness defaulted to 3/8"'})
    assert _signal(quote, "decode_clean") == FAIL


def test_decode_clean_fails_on_needs_pricing_status(app):
    quote = _make_quote(_make_customer())
    _add_line(quote, specs=SPECS)
    quote.status = QuoteStatus.NEEDS_PRICING
    assert _signal(quote, "decode_clean") == FAIL


# ---------------------------------------------------------------------------
# all_lines_priced
# ---------------------------------------------------------------------------


def test_all_lines_priced_passes_when_priced(app):
    quote = _make_quote(_make_customer())
    _add_line(quote, unit_price=100.0)
    assert _signal(quote, "all_lines_priced") == PASS


def test_all_lines_priced_fails_on_zero_price_line(app):
    quote = _make_quote(_make_customer())
    _add_line(quote, unit_price=0.0)
    assert _signal(quote, "all_lines_priced") == FAIL


def test_all_lines_priced_counts_manual_no_charge_as_priced(app):
    quote = _make_quote(_make_customer())
    _add_line(quote, unit_price=100.0)
    _add_line(quote, unit_price=0.0, specs={"manual_no_charge": True},
              description="Courtesy sample", part_number=None)
    assert _signal(quote, "all_lines_priced") == PASS


def test_all_lines_priced_unknown_when_no_material_lines(app):
    quote = _make_quote(_make_customer())
    assert _signal(quote, "all_lines_priced") == UNKNOWN


# ---------------------------------------------------------------------------
# customer_known
# ---------------------------------------------------------------------------


def test_customer_known_fails_without_customer(app):
    quote = _make_quote(None)
    assert _signal(quote, "customer_known") == FAIL


def test_customer_known_passes_for_preexisting_customer(app):
    quote = _make_quote(_make_customer(created_at=LONG_AGO))
    assert _signal(quote, "customer_known") == PASS


def test_customer_known_fails_for_customer_created_with_quote(app):
    # Customer auto-created from this same RFQ moments before the quote row.
    customer = _make_customer(created_at=datetime.utcnow())
    quote = _make_quote(customer)
    assert _signal(quote, "customer_known") == FAIL


def test_customer_known_passes_when_new_customer_has_prior_quote(app):
    customer = _make_customer(created_at=datetime.utcnow())
    prior = _make_quote(customer, number="126-499")
    prior.created_at = datetime.utcnow() - timedelta(days=1)
    _db.session.flush()
    quote = _make_quote(customer, number="126-500b")
    assert _signal(quote, "customer_known") == PASS


# ---------------------------------------------------------------------------
# ship_to_confirmed
# ---------------------------------------------------------------------------


def test_ship_to_confirmed_unknown_without_ship_to(app):
    quote = _make_quote(_make_customer())
    assert _signal(quote, "ship_to_confirmed") == UNKNOWN


def test_ship_to_confirmed_passes_on_confirmed_match(app):
    customer = _make_customer(with_confirmed_ship_to=True)
    quote = _make_quote(customer, ship_to=dict(SHIP_TO))
    assert _signal(quote, "ship_to_confirmed") == PASS


def test_ship_to_confirmed_fails_on_unconfirmed_match(app):
    customer = _make_customer(with_confirmed_ship_to=True)
    for address in customer.ship_to_addresses:
        address.human_confirmed = False
    quote = _make_quote(customer, ship_to=dict(SHIP_TO))
    assert _signal(quote, "ship_to_confirmed") == FAIL


def test_ship_to_confirmed_fails_when_no_stored_address_matches(app):
    customer = _make_customer(with_confirmed_ship_to=True)
    other = dict(SHIP_TO, address_line1="999 Elsewhere Ave")
    quote = _make_quote(customer, ship_to=other)
    assert _signal(quote, "ship_to_confirmed") == FAIL


# ---------------------------------------------------------------------------
# price_in_tolerance
# ---------------------------------------------------------------------------


def test_price_in_tolerance_unknown_with_no_history(app):
    quote = _make_quote(_make_customer())
    _add_line(quote, unit_price=100.0, specs=SPECS)
    assert _signal(quote, "price_in_tolerance") == UNKNOWN


def test_price_in_tolerance_passes_within_tolerance(app):
    customer = _make_customer()
    _sent_history_quote("126-101", 100.0, SPECS, customer)
    _sent_history_quote("126-102", 110.0, SPECS, customer)
    quote = _make_quote(customer)
    _add_line(quote, unit_price=100.0, specs=SPECS)
    assert _signal(quote, "price_in_tolerance") == PASS


def test_price_in_tolerance_fails_out_of_tolerance(app):
    customer = _make_customer()
    _sent_history_quote("126-101", 100.0, SPECS, customer)
    quote = _make_quote(customer)
    _add_line(quote, unit_price=100.0 * (1 + DEFAULT_PRICE_TOLERANCE_PCT) + 5, specs=SPECS)
    assert _signal(quote, "price_in_tolerance") == FAIL


def test_price_in_tolerance_ignores_draft_history(app):
    # Non-SENT quotes are not a trusted baseline — with only a draft in
    # history the signal stays unknown.
    customer = _make_customer()
    draft = _make_quote(customer, number="126-101")
    _add_line(draft, unit_price=100.0, specs=SPECS)
    quote = _make_quote(customer, number="126-102")
    _add_line(quote, unit_price=100.0, specs=SPECS)
    assert _signal(quote, "price_in_tolerance") == UNKNOWN


def test_price_in_tolerance_requires_matching_specs(app):
    customer = _make_customer()
    _sent_history_quote("126-101", 100.0, {**SPECS, "diameter": "24"}, customer)
    quote = _make_quote(customer)
    _add_line(quote, unit_price=500.0, specs=SPECS)
    assert _signal(quote, "price_in_tolerance") == UNKNOWN


def test_price_in_tolerance_excludes_own_revision_chain(app):
    customer = _make_customer()
    sent = _sent_history_quote("126-101", 100.0, SPECS, customer)
    revision = Quote(
        quote_number="126-101-R1",
        status=QuoteStatus.IN_REVIEW,
        customer_id=customer.id,
        replaces_quote_id=sent.id,
        revision_number=1,
    )
    sent.status = QuoteStatus.SENT  # chain ancestor stays SENT in history
    _db.session.add(revision)
    _db.session.flush()
    _add_line(revision, unit_price=100.0, specs=SPECS)
    # The only SENT comparable is the revision's own ancestor — excluded.
    assert _signal(revision, "price_in_tolerance") == UNKNOWN


# ---------------------------------------------------------------------------
# recipient_allowlisted
# ---------------------------------------------------------------------------


def test_recipient_fails_without_contact_email(app):
    quote = _make_quote(_make_customer(), contact_email=None)
    assert _signal(quote, "recipient_allowlisted") == FAIL


def test_recipient_passes_when_allowlist_unset(app):
    quote = _make_quote(_make_customer())
    assert _signal(quote, "recipient_allowlisted") == PASS


def test_recipient_allowlist_enforced(app, monkeypatch):
    quote = _make_quote(_make_customer(), contact_email="buyer@acme.com")
    monkeypatch.setenv("SEND_EMAIL_ALLOWLIST", "someone@else.com")
    assert _signal(quote, "recipient_allowlisted") == FAIL
    monkeypatch.setenv("SEND_EMAIL_ALLOWLIST", "someone@else.com, Buyer@Acme.com")
    assert _signal(quote, "recipient_allowlisted") == PASS


# ---------------------------------------------------------------------------
# Composite + persistence
# ---------------------------------------------------------------------------


def test_composite_score_is_sum_of_passing_weights(app):
    customer = _make_customer(with_confirmed_ship_to=True)
    _sent_history_quote("126-101", 100.0, SPECS, customer)
    quote = _make_quote(customer, ship_to=dict(SHIP_TO))
    _add_line(quote, unit_price=100.0, specs=SPECS)
    score, components = compute_quote_confidence(quote)
    assert all(c["status"] == PASS for c in components.values()), components
    assert score == Decimal("1.000")

    expected = sum(
        (SIGNAL_WEIGHTS[name] for name, c in components.items() if c["status"] == PASS),
        Decimal("0"),
    )
    assert score == expected.quantize(Decimal("0.001"))


def test_unknown_earns_zero_points_not_pass(app):
    quote = _make_quote(_make_customer())
    _add_line(quote, unit_price=100.0, specs=SPECS)  # no history → unknown
    score, components = compute_quote_confidence(quote)
    assert components["price_in_tolerance"]["status"] == UNKNOWN
    assert components["price_in_tolerance"]["points"] == 0.0
    assert score < Decimal("1.000")


def test_sync_persists_row_with_breakdown(app):
    quote = _make_quote(_make_customer())
    _add_line(quote, unit_price=100.0, specs=SPECS)
    changed = sync_quote_confidence(quote)
    _db.session.commit()
    assert changed is True
    row = _db.session.query(QuoteConfidence).filter_by(quote_id=quote.id).one()
    assert float(row.score) == float(sum(
        (SIGNAL_WEIGHTS[name] for name in SIGNAL_WEIGHTS
         if row.components_json[name]["status"] == PASS),
        Decimal("0"),
    ))
    for name in SIGNAL_WEIGHTS:
        assert getattr(row, name) == row.components_json[name]["status"]
    # Second sync with nothing changed reports no change.
    assert sync_quote_confidence(quote) is False


# ---------------------------------------------------------------------------
# Recompute triggers
# ---------------------------------------------------------------------------


def test_monitor_write_creates_confidence_row(app):
    from allenedwards.db_writer import write_quote_to_db
    from allenedwards.email_provider import EmailMessage
    from allenedwards.parser import ParsedRFQ
    from allenedwards.pricing import Quote as PricingQuote, QuoteLineItem as PricingLineItem

    msg = EmailMessage(
        id="msg-conf-1",
        subject="RFQ sleeves",
        sender_name="Buyer",
        sender_email="buyer@acme.com",
        body_preview="please quote",
        body_content="please quote",
        body_content_type="text",
        internet_message_id="<conf-1@acme.com>",
    )
    rfq = ParsedRFQ(
        customer_name="Acme Pipeline New Co",
        contact_name="Buyer",
        contact_email="buyer@acme.com",
        contact_phone=None,
        ship_to=None,
        po_number=None,
        quote_number=None,
        items=[],
    )
    priced = PricingQuote(
        quote_number="126-900",
        customer_name="Acme Pipeline New Co",
        contact_name="Buyer",
        contact_email="buyer@acme.com",
        contact_phone=None,
        ship_to=None,
        line_items=[
            PricingLineItem(
                sort_order=1,
                product_type="sleeve",
                part_number="SLV-12-375",
                description="12in sleeve",
                quantity=2,
                unit_price=Decimal("100.00"),
                total=Decimal("200.00"),
            )
        ],
        subtotal=Decimal("200.00"),
        shipping_amount=None,
        tax_amount=Decimal("0"),
        total=Decimal("200.00"),
        notes=None,
    )
    db_quote = write_quote_to_db(msg, rfq, priced, "126-900")
    row = _db.session.query(QuoteConfidence).filter_by(quote_id=db_quote.id).one()
    assert row.components_json["all_lines_priced"]["status"] == PASS
    # Customer was auto-created from this RFQ → not a known customer.
    assert row.customer_known == FAIL


def test_editing_line_item_recomputes_confidence(app, client):
    customer = _make_customer()
    quote = _make_quote(customer)
    item = _add_line(quote, unit_price=100.0, specs=SPECS)
    sync_quote_confidence(quote)
    _db.session.commit()
    before = _db.session.query(QuoteConfidence).filter_by(quote_id=quote.id).one()
    assert before.all_lines_priced == PASS
    before_computed_at = before.computed_at

    # A human editing the description to a TBD marker must resurface in the
    # stored confidence without any explicit recompute call.
    response = client.post(
        f"/quotes/{quote.id}/line-items/{item.id}/update",
        data={
            "product_type": "sleeve",
            "description": "Pricing TBD, contact sales",
            "quantity": "2",
            "unit_price": "100.00",
            "unit_price_baseline": "100.00",
        },
    )
    assert response.status_code == 200
    after = _db.session.query(QuoteConfidence).filter_by(quote_id=quote.id).one()
    assert after.all_lines_priced == FAIL
    assert after.decode_clean == FAIL
    assert after.computed_at >= before_computed_at


def test_blank_quote_create_scores(app, client):
    response = client.post("/quotes/", follow_redirects=False)
    assert response.status_code == 302
    quote_id = int(response.headers["Location"].rstrip("/").rsplit("/", 1)[-1])
    row = _db.session.query(QuoteConfidence).filter_by(quote_id=quote_id).one()
    assert row.all_lines_priced == UNKNOWN
    assert row.customer_known == FAIL
