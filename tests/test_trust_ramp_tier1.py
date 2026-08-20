"""CP-2b: Tier-1 assisted send — recommend-only dashboard + holds + tier.

Covers the single shared recommend rule (confidence.quote_recommendation),
the queue dashboard's confidence/recommendation display + filter/sort, the
editor's basis panel, the send-form display, and the trust-ramp admin UI
(tier setting + per-customer / per-product-type holds).
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app import create_app
from app.confidence import (
    quote_recommendation,
    sync_quote_confidence,
)
from app.extensions import db as _db
from app.models import (
    Customer,
    Quote,
    QuoteLineItem,
    QuoteStatus,
    SendHold,
    ShipToAddress,
    TrustRampConfig,
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

SPECS = {"diameter": "12", "wall_thickness": "0.375", "grade": "50"}


@pytest.fixture()
def app(db_url, monkeypatch):
    from app.config import Config

    monkeypatch.setattr(Config, "SQLALCHEMY_DATABASE_URI", db_url)
    monkeypatch.setattr(Config, "TESTING", True, raising=False)
    monkeypatch.delenv("SEND_EMAIL_ALLOWLIST", raising=False)
    monkeypatch.delenv("CONFIDENCE_PRICE_TOLERANCE_PCT", raising=False)
    monkeypatch.delenv("CONFIDENCE_RECOMMEND_THRESHOLD", raising=False)
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


def _make_customer(created_at=LONG_AGO, with_confirmed_ship_to=True, name="Acme Pipeline"):
    customer = Customer(company_name=name, discount_pct=0)
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


def _make_quote(customer=None, contact_email="buyer@acme.com", ship_to=None, number="126-500",
                status=QuoteStatus.IN_REVIEW):
    quote = Quote(
        quote_number=number,
        status=status,
        customer_id=customer.id if customer else None,
        contact_email=contact_email,
        ship_to_json=ship_to,
        customer_name_raw=customer.company_name if customer else "Somebody",
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
    quote = Quote(
        quote_number=number,
        status=QuoteStatus.SENT,
        customer_id=customer.id if customer else None,
    )
    _db.session.add(quote)
    _db.session.flush()
    _add_line(quote, unit_price=unit_price, specs=specs)
    return quote


def _perfect_quote(customer=None, number="126-500"):
    """A quote for which every component signal passes."""
    if customer is None:
        customer = _make_customer()
    _sent_history_quote(f"{number}-H1", 100.0, SPECS, customer)
    _sent_history_quote(f"{number}-H2", 105.0, SPECS, customer)
    quote = _make_quote(customer, ship_to=dict(SHIP_TO), number=number)
    _add_line(quote, unit_price=100.0, specs=SPECS)
    sync_quote_confidence(quote)
    _db.session.commit()
    return quote


def _scored(quote):
    sync_quote_confidence(quote)
    _db.session.commit()
    return quote


# ---------------------------------------------------------------------------
# The shared recommend rule
# ---------------------------------------------------------------------------


def test_perfect_quote_is_recommended(app):
    quote = _perfect_quote()
    rec = quote_recommendation(quote)
    assert rec["recommended"] is True
    assert rec["score"] == 1.0
    assert rec["reasons"] == []
    assert rec["tier"] == 1


def test_unscored_quote_is_not_recommended(app):
    quote = _make_quote(_make_customer())
    rec = quote_recommendation(quote)
    assert rec["recommended"] is False
    assert any("not been scored" in r for r in rec["reasons"])


def test_failing_signal_blocks_recommendation(app):
    customer = _make_customer()
    _sent_history_quote("126-600-H1", 100.0, SPECS, customer)
    quote = _make_quote(customer, ship_to=dict(SHIP_TO), number="126-600")
    # 50% over median: price_in_tolerance FAILS.
    _add_line(quote, unit_price=150.0, specs=SPECS)
    _scored(quote)
    rec = quote_recommendation(quote)
    assert rec["recommended"] is False
    assert any("Price in tolerance failed" in r for r in rec["reasons"])


def test_unpriced_guardrail_is_named_even_below_threshold(app):
    quote = _make_quote(_make_customer(), number="126-601")
    _add_line(quote, unit_price=0.0, specs=SPECS)
    _scored(quote)
    rec = quote_recommendation(quote)
    assert rec["recommended"] is False
    assert any("guardrail" in r.lower() and "All lines priced" in r for r in rec["reasons"])


def test_unknown_price_history_lands_below_threshold(app):
    # Everything passes except price_in_tolerance = unknown (no history):
    # score 0.80 < 0.90 threshold, and the reason names the unknown signal.
    quote = _make_quote(_make_customer(), ship_to=dict(SHIP_TO), number="126-602")
    _add_line(quote, unit_price=100.0, specs=SPECS)
    _scored(quote)
    rec = quote_recommendation(quote)
    assert rec["recommended"] is False
    assert rec["score"] == pytest.approx(0.80)
    assert any("below the" in r and "Price in tolerance" in r for r in rec["reasons"])


def test_missing_ship_to_alone_stays_recommended(app):
    # Product-only quote: ship_to_confirmed unknown, everything else passes;
    # 0.90 meets the default threshold.
    customer = _make_customer(with_confirmed_ship_to=False)
    _sent_history_quote("126-603-H1", 100.0, SPECS, customer)
    quote = _make_quote(customer, ship_to=None, number="126-603")
    _add_line(quote, unit_price=100.0, specs=SPECS)
    _scored(quote)
    rec = quote_recommendation(quote)
    assert rec["score"] == pytest.approx(0.90)
    assert rec["recommended"] is True


def test_threshold_env_override(app, monkeypatch):
    customer = _make_customer(with_confirmed_ship_to=False)
    _sent_history_quote("126-604-H1", 100.0, SPECS, customer)
    quote = _make_quote(customer, ship_to=None, number="126-604")
    _add_line(quote, unit_price=100.0, specs=SPECS)
    _scored(quote)
    monkeypatch.setenv("CONFIDENCE_RECOMMEND_THRESHOLD", "0.95")
    rec = quote_recommendation(quote)
    assert rec["recommended"] is False


def test_customer_hold_forces_not_recommended(app):
    quote = _perfect_quote()
    _db.session.add(SendHold(customer_id=quote.customer_id, reason="pricing dispute"))
    _db.session.commit()
    rec = quote_recommendation(quote)
    assert rec["recommended"] is False
    assert any("admin hold on customer" in r and "pricing dispute" in r for r in rec["reasons"])
    assert rec["holds"][0]["kind"] == "customer"


def test_product_type_hold_matches_line_items(app):
    quote = _perfect_quote(number="126-605")
    _db.session.add(SendHold(product_type="sleeve"))
    _db.session.commit()
    rec = quote_recommendation(quote)
    assert rec["recommended"] is False
    assert any("admin hold on product type sleeve" in r for r in rec["reasons"])


def test_unrelated_hold_does_not_block(app):
    quote = _perfect_quote(number="126-606")
    other = _make_customer(name="Other Co")
    _db.session.add(SendHold(customer_id=other.id))
    _db.session.add(SendHold(product_type="omegawrap"))
    _db.session.commit()
    assert quote_recommendation(quote)["recommended"] is True


def test_tier_zero_kill_switch_suspends_recommendations(app):
    quote = _perfect_quote(number="126-607")
    cfg = _db.session.get(TrustRampConfig, 1) or TrustRampConfig(id=1)
    cfg.active_tier = 0
    _db.session.add(cfg)
    _db.session.commit()
    rec = quote_recommendation(quote)
    assert rec["recommended"] is False
    assert any("kill-switch" in r for r in rec["reasons"])


def test_sent_quote_is_not_recommended(app):
    quote = _perfect_quote(number="126-608")
    quote.status = QuoteStatus.SENT
    _db.session.commit()
    rec = quote_recommendation(quote)
    assert rec["recommended"] is False
    assert any("quote is sent" in r for r in rec["reasons"])


def test_default_tier_is_one(app):
    quote = _perfect_quote(number="126-609")
    assert quote_recommendation(quote)["tier"] == 1


# ---------------------------------------------------------------------------
# Queue dashboard display + filter/sort
# ---------------------------------------------------------------------------


def test_queue_shows_recommendation_and_confidence(app, client):
    _perfect_quote(number="126-700")
    weak = _make_quote(_make_customer(name="Weak Co"), number="126-701")
    _add_line(weak, unit_price=0.0, specs=SPECS)
    _scored(weak)

    page = client.get("/quotes/").get_data(as_text=True)
    # The class names, not the words: "Not recommended" contains "Recommended".
    assert "rec-pill rec-yes" in page
    assert "rec-pill rec-no" in page
    assert "100% confidence" in page
    assert "signal-dot signal-pass" in page
    assert "signal-dot signal-fail" in page
    assert "Trust ramp: Tier 1" in page


def test_queue_filter_recommended(app, client):
    _perfect_quote(number="126-702")
    weak = _make_quote(_make_customer(name="Weak Co"), number="126-703")
    _add_line(weak, unit_price=0.0, specs=SPECS)
    _scored(weak)

    # Match the quote-number span exactly: history quotes ("126-702-H1")
    # contain the parent number as a substring.
    page = client.get("/quotes/?rec=recommended").get_data(as_text=True)
    assert ">126-702<" in page
    assert ">126-703<" not in page

    page = client.get("/quotes/?rec=not_recommended").get_data(as_text=True)
    assert ">126-702<" not in page
    assert ">126-703<" in page


def test_queue_sort_by_confidence_puts_recommended_first(app, client):
    weak = _make_quote(_make_customer(name="Weak Co"), number="126-704")
    _add_line(weak, unit_price=0.0, specs=SPECS)
    _scored(weak)
    # Created later, so "newest" order would put it first anyway — flip:
    # the weak quote is newest, the perfect one oldest.
    strong = _perfect_quote(number="126-705")
    weak.created_at = datetime.utcnow()
    strong.created_at = LONG_AGO
    _db.session.commit()

    newest = client.get("/quotes/?sort=newest").get_data(as_text=True)
    assert newest.index(">126-704<") < newest.index(">126-705<")

    by_conf = client.get("/quotes/?sort=confidence").get_data(as_text=True)
    assert by_conf.index(">126-705<") < by_conf.index(">126-704<")


def test_queue_unscored_quote_shows_not_scored(app, client):
    _make_quote(_make_customer(), number="126-706")
    _db.session.commit()
    page = client.get("/quotes/").get_data(as_text=True)
    assert "not scored" in page


# ---------------------------------------------------------------------------
# Editor panel: every signal with its basis
# ---------------------------------------------------------------------------


def test_editor_panel_shows_signals_and_price_basis(app, client):
    quote = _perfect_quote(number="126-710")
    page = client.get(f"/quotes/{quote.id}").get_data(as_text=True)
    assert "Recommended for send?" in page
    for label in ("Clean decode", "All lines priced", "Customer known",
                  "Ship-to confirmed", "Price in tolerance", "Recipient allowed"):
        assert label in page
    # The price-tolerance basis: which history it compared against.
    assert "median" in page
    assert "comparable sent quote" in page
    assert "Confidence 100%" in page


def test_editor_panel_lists_why_not(app, client):
    weak = _make_quote(_make_customer(name="Weak Co"), number="126-711")
    _add_line(weak, unit_price=0.0, specs=SPECS)
    _scored(weak)
    page = client.get(f"/quotes/{weak.id}").get_data(as_text=True)
    assert "Why not:" in page
    assert "All lines priced" in page


def test_editor_panel_shows_active_hold(app, client):
    quote = _perfect_quote(number="126-712")
    _db.session.add(SendHold(customer_id=quote.customer_id, reason="dispute"))
    _db.session.commit()
    page = client.get(f"/quotes/{quote.id}").get_data(as_text=True)
    assert "Active holds on this quote" in page
    assert "dispute" in page


# ---------------------------------------------------------------------------
# Send form: display-only score, send stays human-initiated
# ---------------------------------------------------------------------------


def test_send_form_shows_recommendation(app, client):
    quote = _perfect_quote(number="126-720")
    page = client.get(f"/quotes/{quote.id}/send-form").get_data(as_text=True)
    assert "Recommended for send" in page
    assert "confidence 100%" in page
    # The send button is still there — recommend-only never blocks the human.
    assert "Send Quote" in page


def test_send_form_not_recommended_still_sendable(app, client):
    customer = _make_customer(with_confirmed_ship_to=False, name="NoHist Co")
    quote = _make_quote(customer, ship_to=None, number="126-721")
    _add_line(quote, unit_price=100.0, specs=SPECS)
    quote.status = QuoteStatus.READY
    _scored(quote)
    _db.session.add(SendHold(customer_id=customer.id))
    _db.session.commit()
    page = client.get(f"/quotes/{quote.id}/send-form").get_data(as_text=True)
    assert "Not recommended" in page
    assert 'hx-post="/quotes/' in page  # form still posts to /send


# ---------------------------------------------------------------------------
# Admin: trust-ramp tab, tier setting, holds CRUD
# ---------------------------------------------------------------------------


def test_admin_trust_tab_renders(app, client):
    page = client.get("/admin/pricing?tab=trust").get_data(as_text=True)
    assert "Trust Ramp" in page
    assert "Tier 1" in page
    assert "Send holds" in page


def test_admin_set_tier_zero_and_back(app, client):
    resp = client.post("/admin/trust-ramp/tier", data={"active_tier": "0"}, follow_redirects=True)
    assert resp.status_code == 200
    with_zero = _db.session.get(TrustRampConfig, 1)
    assert with_zero.active_tier == 0
    client.post("/admin/trust-ramp/tier", data={"active_tier": "1"}, follow_redirects=True)
    assert _db.session.get(TrustRampConfig, 1).active_tier == 1


def test_admin_rejects_unknown_tier(app, client):
    # Tier 2 became valid with CP-2c; Tier 3 does not exist yet.
    client.post("/admin/trust-ramp/tier", data={"active_tier": "3"}, follow_redirects=True)
    assert _db.session.get(TrustRampConfig, 1).active_tier == 1
    client.post("/admin/trust-ramp/tier", data={"active_tier": "banana"}, follow_redirects=True)
    assert _db.session.get(TrustRampConfig, 1).active_tier == 1


def test_admin_add_and_remove_customer_hold(app, client):
    customer = _make_customer()
    _db.session.commit()
    client.post(
        "/admin/trust-ramp/holds/add",
        data={"hold_type": "customer", "customer_id": str(customer.id), "reason": "audit"},
        follow_redirects=True,
    )
    hold = _db.session.query(SendHold).filter_by(customer_id=customer.id).one()
    assert hold.reason == "audit"

    # Duplicate is rejected, not doubled.
    client.post(
        "/admin/trust-ramp/holds/add",
        data={"hold_type": "customer", "customer_id": str(customer.id)},
        follow_redirects=True,
    )
    assert _db.session.query(SendHold).filter_by(customer_id=customer.id).count() == 1

    client.post(f"/admin/trust-ramp/holds/{hold.id}/delete", follow_redirects=True)
    assert _db.session.query(SendHold).count() == 0


def test_admin_add_product_type_hold(app, client):
    client.post(
        "/admin/trust-ramp/holds/add",
        data={"hold_type": "product_type", "product_type": "sleeve"},
        follow_redirects=True,
    )
    assert _db.session.query(SendHold).filter_by(product_type="sleeve").count() == 1


def test_admin_hold_requires_target(app, client):
    client.post(
        "/admin/trust-ramp/holds/add",
        data={"hold_type": "customer"},
        follow_redirects=True,
    )
    assert _db.session.query(SendHold).count() == 0


def test_send_hold_exactly_one_target_constraint(app):
    from sqlalchemy.exc import IntegrityError

    _db.session.add(SendHold(customer_id=None, product_type=None))
    with pytest.raises(IntegrityError):
        _db.session.commit()
    _db.session.rollback()
