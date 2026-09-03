"""Line-item provenance notes must be visible, and split by audience.

Pricing writes a note on a line whenever it assumed something. Task 345: those
notes have to reach a human. The quote editor already renders the full internal
text (routes._line_item_view -> "note", quotes/_line_items.html); the customer
PDF rendered nothing at all, which is what this file fixes and pins.

The PDF gets a curated subset (allenedwards.line_notes). Anything worded for
Chip — "interim conversion, confirm with Chip", "verify" — stays out of it.
"""

from __future__ import annotations

import os
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

from pypdf import PdfReader

from allenedwards.line_notes import customer_note
from allenedwards.pdf_generator import generate_quote_pdf
from allenedwards.pricing import Quote as PricingQuote
from allenedwards.pricing import QuoteLineItem as PricingLineItem
from allenedwards.pricing import _backing_strip_packs
from allenedwards.parser import ParsedItem
from allenedwards.units import convert_length_to_feet

from app import create_app
from app.config import Config
from app.extensions import db
from app.models import Quote, QuoteLineItem, QuoteStatus, User


# --------------------------------------------------------------------------
# helpers


def _pdf_text(quote: PricingQuote) -> str:
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
        path = Path(handle.name)
    try:
        generate_quote_pdf(quote, path, quote_date=date(2026, 7, 30))
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    finally:
        path.unlink(missing_ok=True)


def _quote_with_note(notes: str | None, description: str = "Sleeve, Sealing") -> PricingQuote:
    item = PricingLineItem(
        sort_order=1,
        product_type="sleeve",
        part_number="S-12.75-375-50-10",
        description=description,
        quantity=4,
        unit_price=Decimal("250.00"),
        total=Decimal("1000.00"),
        notes=notes,
    )
    return PricingQuote(
        quote_number="126-345",
        customer_name="Azimuth Energy",
        contact_name="Buyer",
        contact_email="buyer@example.com",
        contact_phone=None,
        ship_to=None,
        line_items=[item],
        subtotal=Decimal("1000.00"),
        shipping_amount=None,
        tax_amount=Decimal("0.00"),
        total=Decimal("1000.00"),
        notes=None,
    )


def _make_app(db_url):
    os.environ["DATABASE_URL"] = db_url
    Config.SQLALCHEMY_DATABASE_URI = db_url
    app = create_app()
    app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    app.config["TESTING"] = True
    return app


def _seed_quote(notes_by_line: list[tuple[str, str, dict]]) -> tuple[int, int]:
    """Create a quote whose lines carry the given (product_type, description, specs)."""
    user = User(email="chip@example.com", name="Chip", password_hash="x")
    db.session.add(user)
    quote = Quote(quote_number="126-345", status=QuoteStatus.NEW, customer_name_raw="Azimuth Energy")
    db.session.add(quote)
    db.session.flush()
    for order, (product_type, description, specs) in enumerate(notes_by_line, start=1):
        db.session.add(
            QuoteLineItem(
                quote_id=quote.id,
                product_type=product_type,
                description=description,
                quantity=4,
                unit_price=250,
                line_total=1000,
                specs_json=specs,
                sort_order=order,
            )
        )
    db.session.commit()
    return quote.id, user.id


# --------------------------------------------------------------------------
# The note strings pricing actually produces.
#
# These pin the wording the allowlist in line_notes.py matches on. Reword a note
# in pricing.py or units.py and this fails here, loudly, instead of silently
# dropping that line's customer note off the PDF.


def test_pricing_note_strings_are_the_ones_the_allowlist_matches():
    _, defaulted_wall = _apply_defaults_note()
    assert 'wall thickness defaulted to 3/8"' in defaulted_wall
    assert "grade defaulted to GR50" in defaulted_wall

    _, metric_note = convert_length_to_feet("6 m", 10.0)
    assert metric_note == "length 6 m converted to 19.685 ft, verify"

    _, standard_note = convert_length_to_feet("3 m", 10.0)
    assert standard_note == "length 3 m (9.843 ft) quoted as standard 10 ft piece"

    _, strip_note = _backing_strip_packs(
        ParsedItem(product_type="accessory", description="50 lf of backing strip", quantity=1)
    )
    assert "interim conversion, confirm with Chip" in strip_note
    assert "billed as 1 pack of 10" in strip_note


def _apply_defaults_note() -> tuple[object, str]:
    from allenedwards.pricing import _apply_item_defaults

    item, notes = _apply_item_defaults(
        ParsedItem(product_type="sleeve", description="12.75 sleeve", quantity=4, diameter=12.75)
    )
    return item, "; ".join(notes)


def test_no_em_dashes_in_note_strings_that_reach_a_human():
    """Devin reads an em dash in client-visible text as an LLM tell (PM, task 345)."""
    _, defaulted_wall = _apply_defaults_note()
    _, metric_note = convert_length_to_feet("6 m", 10.0)
    _, strip_note = _backing_strip_packs(
        ParsedItem(product_type="accessory", description="50 lf of backing strip", quantity=1)
    )
    from allenedwards.pricing import _tbd_line_item

    tbd_note = _tbd_line_item(
        ParsedItem(product_type="sleeve", description="mystery item", quantity=1), 1
    ).notes

    for note in (defaulted_wall, metric_note, strip_note, tbd_note):
        assert "—" not in note, f"em dash in note string: {note!r}"


# --------------------------------------------------------------------------
# The audience split itself


def test_internal_backing_strip_basis_never_becomes_customer_text():
    _, strip_note = _backing_strip_packs(
        ParsedItem(product_type="accessory", description="50 lf of backing strip", quantity=1)
    )
    rendered = customer_note(strip_note)
    assert rendered == "Billed as 1 pack of 10"
    assert "Chip" not in rendered
    assert "interim" not in rendered.lower()


def test_grayscale_bundle_of_5_note_never_becomes_customer_text():
    """Chip flagged the bundle-of-5 packaging note leaking to the PDF (task 458).

    The "Priced as N bundles (...)" clause is an internal packaging/billing
    breakdown and must stay in the editor only. Other clauses on the same line
    still print.
    """
    bundle_note = "Priced as 2 bundles (10 pcs / 100 ft)"
    assert customer_note(bundle_note) is None
    assert customer_note(f'wall thickness defaulted to 3/8"; {bundle_note}') == (
        'Priced at 3/8" wall'
    )


def test_pdf_hides_the_grayscale_bundle_of_5_note():
    text = _pdf_text(_quote_with_note("Priced as 2 bundles (10 pcs / 100 ft)"))
    assert "bundle" not in text.lower()
    assert "10 pcs" not in text


def test_unrecognized_clause_is_treated_as_internal():
    """Fail closed: a note added to pricing later must not leak to the customer."""
    assert customer_note("some brand new provenance note nobody has classified") is None
    assert customer_note('wall thickness defaulted to 3/8"; a brand new unclassified clause') == (
        'Priced at 3/8" wall'
    )


def test_verify_wording_is_dropped_but_the_converted_length_still_prints():
    _, metric_note = convert_length_to_feet("6 m", 10.0)
    rendered = customer_note(metric_note)
    assert rendered == "Length 6 m quoted as 19.685 ft"
    assert "verify" not in rendered.lower()


# --------------------------------------------------------------------------
# Rendered PDF output
#
# Every assertion below reads text back out of a generated PDF. All four fail
# against main, where _build_line_items_table never touched item.notes.


def test_pdf_prints_customer_note_for_a_defaulted_wall_sleeve():
    _, defaulted_wall = _apply_defaults_note()
    text = _pdf_text(_quote_with_note(defaulted_wall))
    assert 'Priced at 3/8" wall' in text
    assert "Priced at Grade 50" in text
    assert "defaulted" not in text


def test_pdf_prints_customer_note_for_a_metric_converted_length():
    _, metric_note = convert_length_to_feet("6 m", 10.0)
    text = _pdf_text(_quote_with_note(metric_note))
    assert "Length 6 m quoted as 19.685 ft" in text
    assert "verify" not in text.lower()


def test_pdf_prints_the_billing_unit_for_a_backing_strip_but_not_the_basis():
    _, strip_note = _backing_strip_packs(
        ParsedItem(product_type="accessory", description="50 lf of backing strip", quantity=1)
    )
    text = _pdf_text(_quote_with_note(strip_note, description="Backing Strip"))
    assert "Billed as 1 pack of 10" in text
    assert "Chip" not in text
    assert "interim" not in text.lower()
    assert "5 ft per strip" not in text


def test_pdf_prints_no_note_when_nothing_is_customer_facing():
    text = _pdf_text(_quote_with_note("an unclassified internal working note"))
    assert "unclassified" not in text
    assert "internal working note" not in text


# --------------------------------------------------------------------------
# The route that builds the PDF a customer receives


def test_quote_pdf_route_carries_notes_out_of_specs_json(db_url):
    """Fails against main: _db_quote_to_pricing_quote dropped notes on the floor."""
    app = _make_app(db_url)
    with app.app_context():
        db.create_all()
        _, strip_note = _backing_strip_packs(
            ParsedItem(product_type="accessory", description="50 lf of backing strip", quantity=1)
        )
        quote_id, _ = _seed_quote(
            [
                (
                    "sleeve",
                    "Sleeve, Sealing",
                    {
                        "diameter": "12.75",
                        "length_ft": "10",
                        "notes": 'wall thickness defaulted to 3/8"',
                    },
                ),
                ("accessory", "Backing Strip", {"notes": strip_note}),
            ]
        )

        from app.routes import _generate_pdf_bytes

        pdf_bytes, _filename = _generate_pdf_bytes(db.session.get(Quote, quote_id))

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
        path = Path(handle.name)
    try:
        path.write_bytes(pdf_bytes)
        text = "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    finally:
        path.unlink(missing_ok=True)

    assert 'Priced at 3/8" wall' in text
    assert "Billed as 1 pack of 10" in text
    assert "Chip" not in text
    assert "interim" not in text.lower()


# --------------------------------------------------------------------------
# Editor regression guard
#
# Unlike the PDF tests, this one PASSES against main. The editor has rendered
# line notes since Apr 2026 (routes.py "note" key, _line_items.html); the ticket
# for 345 said otherwise and was wrong. It is here so the audience split cannot
# be extended to the editor by accident — Chip must keep seeing everything.


def test_editor_shows_the_full_internal_note_including_what_the_pdf_hides(db_url):
    app = _make_app(db_url)
    with app.app_context():
        db.create_all()
        _, strip_note = _backing_strip_packs(
            ParsedItem(product_type="accessory", description="50 lf of backing strip", quantity=1)
        )
        _, metric_note = convert_length_to_feet("6 m", 10.0)
        quote_id, user_id = _seed_quote(
            [
                (
                    "sleeve",
                    "Sleeve, Sealing",
                    {
                        "diameter": "12.75",
                        "length_ft": "10",
                        "notes": 'wall thickness defaulted to 3/8"',
                    },
                ),
                ("accessory", "Backing Strip", {"notes": strip_note}),
                ("sleeve", "Metric Sleeve", {"notes": metric_note}),
            ]
        )

    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True

    # Matched loosely on purpose: the em-dash pass changed the punctuation inside
    # these strings, and this test is about visibility, not wording. Wording is
    # pinned by test_pricing_note_strings_are_the_ones_the_allowlist_matches.
    html = client.get(f"/quotes/{quote_id}").data.decode()
    assert "wall thickness defaulted to 3/8" in html
    assert "interim conversion" in html
    assert "confirm with Chip" in html
    assert "converted to 19.685 ft" in html
    assert "verify" in html
