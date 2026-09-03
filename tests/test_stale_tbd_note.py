"""Stale "Pricing TBD, contact sales" must never render on a priced line.

Chip-reported bug (quote 126-107): the engine writes that note when it cannot
price a line ($0 TBD placeholder). When Chip then typed a real price, the note
stayed in specs_json['notes'] and the customer PDF printed it under a line
showing $145.00 / $6,000.00.

Two fixes, both pinned here:
  1. Price-set time: the line-item update route strips the TBD clause once the
     line has a nonzero price, so storage stops carrying the stale text.
  2. Render time: customer_note(priced=True) drops the TBD clause even if it is
     still stored (old rows from before fix 1).

Legitimate pack/pallet-rounding notes must keep printing on priced lines. The
sleeve bundle-of-5 note is the exception: K124 says "Do NOT mention the minimum
on the quote" and it is editor-only (task 458, Chip flagged it leaking on quote
126-111), so it is pinned here as hidden even on a priced line.
"""

from __future__ import annotations

import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pypdf import PdfReader

from allenedwards.line_notes import customer_note, strip_tbd_clauses
from allenedwards.pdf_generator import generate_quote_pdf
from allenedwards.pricing import Quote as PricingQuote
from allenedwards.pricing import QuoteLineItem as PricingLineItem

from app import create_app
from app.extensions import db as _db
from app.models import Quote, QuoteLineItem, QuoteStatus, User

TBD_NOTE = "Pricing TBD, contact sales"


# --------------------------------------------------------------------------
# line_notes unit behavior


def test_customer_note_drops_tbd_on_a_priced_line():
    assert customer_note(TBD_NOTE, priced=True) is None


def test_customer_note_keeps_tbd_on_an_unpriced_line():
    assert customer_note(TBD_NOTE) == TBD_NOTE
    assert customer_note(TBD_NOTE, priced=False) == TBD_NOTE


def test_customer_note_priced_keeps_the_other_clauses():
    mixed = f"{TBD_NOTE}; Quantity not specified, defaulted to 1"
    assert customer_note(mixed, priced=True) == "Priced for quantity 1"


def test_customer_note_priced_keeps_pack_rounding_notes():
    pack = "billed as 1 pack of 10"
    assert customer_note(pack, priced=True) == "Billed as 1 pack of 10"


def test_customer_note_drops_sleeve_bundle_note_even_on_a_priced_line():
    """Task 458: the sleeve bundle-of-5 note is editor-only (K124)."""
    bundle = "Priced as 1 bundle (5 pcs / 50 ft)"
    assert customer_note(bundle, priced=True) is None
    assert customer_note(bundle) is None


def test_strip_tbd_clauses():
    assert strip_tbd_clauses(TBD_NOTE) is None
    assert strip_tbd_clauses(None) is None
    assert (
        strip_tbd_clauses(f"{TBD_NOTE}; Quantity not specified, defaulted to 1")
        == "Quantity not specified, defaulted to 1"
    )
    bundle = "Priced as 1 bundle (5 pcs / 50 ft)"
    assert strip_tbd_clauses(bundle) == bundle


# --------------------------------------------------------------------------
# Rendered PDF output (the render-time guard)


def _pdf_text(items: list[PricingLineItem]) -> str:
    quote = PricingQuote(
        quote_number="126-107",
        customer_name="Azimuth Energy",
        contact_name="Buyer",
        contact_email="buyer@example.com",
        contact_phone=None,
        ship_to=None,
        line_items=items,
        subtotal=sum((i.total for i in items), Decimal("0.00")),
        shipping_amount=None,
        tax_amount=Decimal("0.00"),
        total=sum((i.total for i in items), Decimal("0.00")),
        notes=None,
    )
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
        path = Path(handle.name)
    try:
        generate_quote_pdf(quote, path, quote_date=date(2026, 8, 27))
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    finally:
        path.unlink(missing_ok=True)


def _line(description: str, unit_price: str, notes: str | None, order: int = 1) -> PricingLineItem:
    price = Decimal(unit_price)
    return PricingLineItem(
        sort_order=order,
        product_type="accessory",
        part_number="FB-1" if price > 0 else "TBD",
        description=description,
        quantity=10,
        unit_price=price,
        total=price * 10,
        notes=notes,
    )


def test_pdf_hides_stale_tbd_note_on_a_priced_line():
    """Fails against unfixed code: the PDF printed 'Pricing TBD' under $145.00."""
    text = _pdf_text([_line("Flat Bar", "145.00", TBD_NOTE)])
    assert "Pricing TBD" not in text
    assert "contact sales" not in text


def test_pdf_still_shows_tbd_note_on_a_genuinely_unpriced_line():
    text = _pdf_text([_line("Mystery item", "0.00", TBD_NOTE)])
    assert "Pricing TBD, contact sales" in text


def test_pdf_keeps_pack_rounding_note_on_a_priced_line():
    """A legitimate pack-rounding note still prints on a priced line; only TBD
    and the sleeve bundle-of-5 note are suppressed."""
    text = _pdf_text([_line("Backing Strip", "724.75", "billed as 1 pack of 10")])
    assert "Billed as 1 pack of 10" in text


def test_pdf_hides_sleeve_bundle_note_on_a_priced_line():
    """Task 458: the sleeve bundle-of-5 packaging note is editor-only (K124:
    'Do NOT mention the minimum on the quote'); Chip flagged it leaking onto the
    customer PDF (quote 126-111)."""
    text = _pdf_text([_line("Sleeve, Sealing", "724.75", "Priced as 1 bundle (5 pcs / 50 ft)")])
    assert "bundle" not in text.lower()


# --------------------------------------------------------------------------
# Price-set time: the update route clears the stored note


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


@pytest.fixture()
def quote_with_tbd_line(app):
    """A quote holding one $0 TBD line, the way pricing._tbd_line_item writes it."""
    with app.app_context():
        q = Quote(
            quote_number="126-107",
            status=QuoteStatus.NEEDS_PRICING,
            customer_name_raw="Azimuth Energy",
        )
        _db.session.add(q)
        _db.session.flush()
        li = QuoteLineItem(
            quote_id=q.id,
            product_type="accessory",
            description="Flat Bar",
            quantity=10,
            unit_price=0,
            line_total=0,
            part_number="TBD",
            sort_order=1,
            specs_json={"notes": TBD_NOTE},
        )
        _db.session.add(li)
        _db.session.commit()
        yield q.id, li.id


def _post_update(client, quote_id: int, item_id: int, unit_price: str) -> None:
    resp = client.post(
        f"/quotes/{quote_id}/line-items/{item_id}/update",
        data={
            "product_type": "accessory",
            "description": "Flat Bar",
            "quantity": "10",
            "unit_price": unit_price,
            "unit_price_baseline": "0.00",
        },
    )
    assert resp.status_code == 200


def test_typing_a_price_clears_the_stored_tbd_note(client, quote_with_tbd_line, app):
    quote_id, item_id = quote_with_tbd_line
    _post_update(client, quote_id, item_id, "145.00")
    with app.app_context():
        li = _db.session.get(QuoteLineItem, item_id)
        specs = dict(li.specs_json or {})
        assert float(li.unit_price) == 145.00
        assert "Pricing TBD" not in str(specs.get("notes") or "")


def test_saving_without_a_price_keeps_the_tbd_note(client, quote_with_tbd_line, app):
    quote_id, item_id = quote_with_tbd_line
    _post_update(client, quote_id, item_id, "0.00")
    with app.app_context():
        li = _db.session.get(QuoteLineItem, item_id)
        assert float(li.unit_price) == 0.0
        assert dict(li.specs_json or {}).get("notes") == TBD_NOTE


def test_typing_a_price_keeps_non_tbd_clauses(client, app):
    with app.app_context():
        q = Quote(
            quote_number="126-108",
            status=QuoteStatus.NEEDS_PRICING,
            customer_name_raw="Azimuth Energy",
        )
        _db.session.add(q)
        _db.session.flush()
        li = QuoteLineItem(
            quote_id=q.id,
            product_type="accessory",
            description="Mystery accessory",
            quantity=1,
            unit_price=0,
            line_total=0,
            part_number="TBD",
            sort_order=1,
            specs_json={"notes": f"{TBD_NOTE}; Quantity not specified, defaulted to 1"},
        )
        _db.session.add(li)
        _db.session.commit()
        quote_id, item_id = q.id, li.id

    resp = client.post(
        f"/quotes/{quote_id}/line-items/{item_id}/update",
        data={
            "product_type": "accessory",
            "description": "Mystery accessory",
            "quantity": "1",
            "unit_price": "99.00",
            "unit_price_baseline": "0.00",
        },
    )
    assert resp.status_code == 200
    with app.app_context():
        li = _db.session.get(QuoteLineItem, item_id)
        assert dict(li.specs_json or {}).get("notes") == "Quantity not specified, defaulted to 1"
