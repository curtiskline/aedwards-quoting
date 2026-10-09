"""A priced line must not stay unsendable over the engine's 'TBD' part number.

Chip-reported bug (quote 126-159, Troy Companies 'Sentinel' 42in service RFQ):
the engine could not price the on-site service lines and wrote its $0
placeholder with part_number='TBD' (pricing._tbd_line_item). Chip hand-priced
every line, but the leftover 'TBD' part number kept tripping
quote_has_tbd_items, so every Mark Ready immediately snapped back to
NEEDS_PRICING and the send form refused — with no visible cause.

Pinned here:
  1. _sync_quote_pricing_status clears the stale 'TBD' part-number placeholder
     from lines a human has since priced (same rule as the 126-107 stale-note
     fix), so marking Ready works once the quote is fully priced.
  2. A part number the user explicitly typed (specs part_number_override) is
     preserved, even if it says TBD — and keeps blocking.
  3. A genuinely unpriced $0 TBD line still blocks.
  4. The NEEDS PRICING banner now lists per-line reasons so the next stuck
     quote is self-explaining.
"""

from __future__ import annotations

import pytest

from app import create_app
from app.extensions import db as _db
from app.models import Quote, QuoteLineItem, QuoteStatus, User


@pytest.fixture()
def app(db_url, monkeypatch):
    from app.config import Config

    monkeypatch.setattr(Config, "SQLALCHEMY_DATABASE_URI", db_url)
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


def _make_quote(items: list[dict]) -> tuple[int, list[int]]:
    """Create a quote with the given line items; returns (quote_id, item_ids)."""
    q = Quote(
        quote_number="126-159",
        status=QuoteStatus.NEEDS_PRICING,
        customer_name_raw="The Troy Companies",
    )
    _db.session.add(q)
    _db.session.flush()
    item_ids = []
    for order, spec in enumerate(items, start=1):
        li = QuoteLineItem(
            quote_id=q.id,
            product_type=spec.get("product_type", "service"),
            description=spec.get("description", "On-site concrete coating"),
            quantity=spec.get("quantity", 1),
            unit_price=spec["unit_price"],
            line_total=spec["unit_price"] * spec.get("quantity", 1),
            part_number=spec.get("part_number"),
            sort_order=order,
            specs_json=spec.get("specs_json"),
        )
        _db.session.add(li)
        _db.session.flush()
        item_ids.append(li.id)
    _db.session.commit()
    return q.id, item_ids


def _mark_ready(client, quote_id: int):
    resp = client.post(f"/quotes/{quote_id}/status", data={"status": "ready"})
    assert resp.status_code == 200
    return resp


def test_priced_line_with_stale_tbd_part_number_can_be_marked_ready(client, app):
    """The 126-159 shape: every line priced, one leftover 'TBD' part number."""
    with app.app_context():
        quote_id, (item_id, _) = _make_quote(
            [
                {
                    "part_number": "TBD",
                    "unit_price": 52.00,
                    "quantity": 3600,
                    "specs_json": {"price_override": True},
                },
                {"part_number": None, "unit_price": 1950.00, "quantity": 30},
            ]
        )

    _mark_ready(client, quote_id)

    with app.app_context():
        quote = _db.session.get(Quote, quote_id)
        assert quote.status == QuoteStatus.READY
        assert _db.session.get(QuoteLineItem, item_id).part_number is None


def test_send_form_opens_once_stale_tbd_placeholder_is_cleared(client, app):
    with app.app_context():
        quote_id, _ = _make_quote([{"part_number": "TBD", "unit_price": 52.00}])

    _mark_ready(client, quote_id)
    resp = client.get(f"/quotes/{quote_id}/send-form")
    assert resp.status_code == 200
    assert b"needs pricing" not in resp.data.lower()


def test_explicitly_typed_tbd_part_number_is_kept_and_still_blocks(client, app):
    with app.app_context():
        quote_id, (item_id,) = _make_quote(
            [
                {
                    "part_number": "TBD",
                    "unit_price": 52.00,
                    "specs_json": {"part_number_override": "TBD"},
                }
            ]
        )

    _mark_ready(client, quote_id)

    with app.app_context():
        quote = _db.session.get(Quote, quote_id)
        assert quote.status == QuoteStatus.NEEDS_PRICING
        assert _db.session.get(QuoteLineItem, item_id).part_number == "TBD"


def test_unpriced_tbd_line_still_blocks_mark_ready(client, app):
    with app.app_context():
        quote_id, (item_id,) = _make_quote([{"part_number": "TBD", "unit_price": 0}])

    resp = _mark_ready(client, quote_id)

    with app.app_context():
        quote = _db.session.get(Quote, quote_id)
        assert quote.status == QuoteStatus.NEEDS_PRICING
        # The placeholder on a genuinely unpriced line is not stale — keep it.
        assert _db.session.get(QuoteLineItem, item_id).part_number == "TBD"
    assert b"NEEDS PRICING" in resp.data


def test_needs_pricing_banner_names_the_blocking_lines(client, app):
    with app.app_context():
        quote_id, _ = _make_quote(
            [
                {"part_number": None, "unit_price": 0, "description": "Mystery service"},
                {
                    "part_number": "TBD",
                    "unit_price": 52.00,
                    "description": "Coating",
                    "specs_json": {"part_number_override": "TBD"},
                },
            ]
        )

    resp = _mark_ready(client, quote_id)
    body = resp.data.decode()
    assert "Mystery service" in body and "has no price" in body
    assert "Coating" in body and "part number" in body


def test_viewing_the_quote_heals_a_stale_tbd_placeholder(client, app):
    """Deploy-then-refresh is enough: the GET detail view's status sync clears
    the stale placeholder and commits, so Chip needs no extra edits."""
    with app.app_context():
        quote_id, (item_id,) = _make_quote([{"part_number": "TBD", "unit_price": 52.00}])

    resp = client.get(f"/quotes/{quote_id}")
    assert resp.status_code == 200

    with app.app_context():
        quote = _db.session.get(Quote, quote_id)
        assert quote.status != QuoteStatus.NEEDS_PRICING
        assert _db.session.get(QuoteLineItem, item_id).part_number is None
