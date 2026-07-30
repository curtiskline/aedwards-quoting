"""Regression coverage for metric (mm / metre) RFQ decoding — task 330.

Source incident: Chip forwarded a fully metric RFQ from Azimuth Energy Sdn Bhd on
2026-07-29 (subject "Request for Quotation (RFQ) - NPS 36 Type B Pressure-Containing
Split Sleeve"). The decoder read the diameter but not the wall thickness, so
prod quote 126-086 (quote.id=87, quote_line_item.id=186) quoted a 3/8" sleeve for
a stated 12.7 mm (exactly 1/2") wall, stamped with the note
'wall thickness defaulted to 3/8"'.

The metric dimension was PRESENT. It only looked absent because ``float("12.7 mm")``
raises, so the field became None and task 326's default fired. These tests pin
both halves: metric input must convert, and a genuinely absent thickness must
still default (task 326 must not be weakened).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from allenedwards.parser import ParsedItem, _parse_items, parse_rfq
from allenedwards.pricing import DEFAULT_WALL_THICKNESS, generate_quote
from allenedwards.providers.mock import MockProvider
from allenedwards.units import (
    convert_diameter_to_inches,
    convert_length_to_feet,
    convert_thickness_to_inches,
    decimal_to_fraction,
    snap_to_catalog_fraction,
)

# Verbatim spec block from the Azimuth Energy RFQ, exactly as received.
AZIMUTH_SPEC_BLOCK = """Pipeline Nominal Size (OD): NPS 36 (914.4 mm)
Carrier Pipe Wall Thickness: 12.7 mm
Sleeve Length: 3,000 mm (3.0 metres)
Pipe Material Grade: API 5L X Series (PSL2)
Design Pressure / MAOP: 6,895 kPag
Quantity: 1 pc"""

DEFAULTED_THICKNESS_NOTE = "wall thickness defaulted"


def _azimuth_item(**overrides) -> dict:
    """A raw LLM item for the Azimuth RFQ, carrying metric values verbatim.

    This is what the updated PARSE_SYSTEM_PROMPT asks the LLM to return: the
    metric value passed straight through with its unit, unconverted.
    """
    item = {
        "product_type": "sleeve",
        "quantity": 1,
        "diameter": "36",
        "wall_thickness": "12.7 mm",
        "grade": "65",
        "length_ft": "3000 mm",
        "milling": False,
        "painting": False,
        "description": AZIMUTH_SPEC_BLOCK,
        "notes": None,
    }
    item.update(overrides)
    return item


def _decode_one(item_data: dict) -> ParsedItem:
    items = _parse_items([item_data])
    assert len(items) == 1
    return items[0]


# ---------------------------------------------------------------------------
# The core regression: 12.7 mm must land as 1/2", not the 3/8" default
# ---------------------------------------------------------------------------


def test_azimuth_metric_wall_thickness_decodes_to_half_inch():
    """12.7 mm is exactly 1/2" — it must never reach the 3/8" default."""
    item = _decode_one(_azimuth_item())

    assert item.wall_thickness == 0.5
    assert item.wall_thickness != DEFAULT_WALL_THICKNESS


def test_azimuth_metric_thickness_recovered_when_llm_returns_null():
    """A dropped metric dimension is recovered from the preserved spec text.

    The LLM sometimes returns null rather than a value it could not map. The
    thickness is still stated in the description, so decoding must find it there
    instead of defaulting.
    """
    item = _decode_one(_azimuth_item(wall_thickness=None, length_ft=None))

    assert item.wall_thickness == 0.5
    assert item.length_ft == 10.0


def test_azimuth_quote_line_does_not_claim_the_thickness_was_defaulted():
    """The quote must not carry the 'defaulted to 3/8"' note for a stated 1/2"."""
    quote = _generate_azimuth_quote(_azimuth_item())

    sleeve_line = quote.line_items[0]
    assert sleeve_line.notes is not None
    assert DEFAULTED_THICKNESS_NOTE not in (sleeve_line.notes or "")
    assert '1/2"' in sleeve_line.description

    all_notes = " ".join(line.description or "" for line in quote.line_items)
    assert DEFAULTED_THICKNESS_NOTE not in all_notes


def test_azimuth_quote_line_shows_the_metric_conversion():
    """A converted dimension must be visible on the line, not silent."""
    quote = _generate_azimuth_quote(_azimuth_item())

    notes = quote.line_items[0].notes or ""
    assert "12.7 mm" in notes
    assert '1/2"' in notes


def _generate_azimuth_quote(item_data: dict):
    """Decode the Azimuth RFQ end to end through a mock LLM and price it."""
    email_content = (
        "From: hasif.syazwi@azimuth.com.my\n"
        "Subject: Request for Quotation (RFQ) - NPS 36 Type B "
        "Pressure-Containing Split Sleeve\n"
        "Content-Type: text/plain; charset=utf-8\n"
        "\n"
        f"{AZIMUTH_SPEC_BLOCK}\n"
    )
    with tempfile.NamedTemporaryFile(suffix=".eml", mode="w", delete=False) as handle:
        handle.write(email_content)
        eml_path = Path(handle.name)

    try:
        provider = MockProvider(
            {
                "customer_name": "Azimuth Energy Sdn Bhd",
                "contact_name": "Hasif Syazwi",
                "contact_email": "hasif.syazwi@azimuth.com.my",
                "ship_to": None,
                "items": [item_data],
                "urgency": "normal",
                "confidence": 0.9,
            }
        )
        rfq = parse_rfq(eml_path, provider)
        return generate_quote(rfq, "126-METRIC")
    finally:
        if eml_path.exists():
            eml_path.unlink()


# ---------------------------------------------------------------------------
# Metric length policy (units.METRIC_LENGTH_POLICY)
# ---------------------------------------------------------------------------


def test_metric_length_rounds_to_standard_piece_with_a_visible_note():
    """3,000 mm is 9.843 ft; it is quoted as a 10 ft piece and says so.

    This distinguishes a deliberate round from the silent DEFAULT_LENGTH_SLEEVE
    fallback that produced the same 10.0 in prod.
    """
    feet, note = convert_length_to_feet("3,000 mm", 10.0)

    assert feet == 10.0
    assert note is not None
    assert "3,000 mm" in note
    assert "9.843" in note
    assert "defaulted" not in note


def test_metric_length_far_from_a_standard_piece_is_quoted_literally():
    """A metric length that is not near a stock piece is never reshaped."""
    feet, note = convert_length_to_feet("5000 mm", 10.0)

    assert feet == pytest.approx(16.404, abs=0.001)
    assert note is not None
    assert "verify" in note


def test_unitless_length_ft_is_feet_not_inches():
    """A bare number in length_ft already means feet.

    Guards the conversion layer against reading 10 as 10 inches, which would
    quote a 10 ft sleeve as a 10 in stub.
    """
    assert convert_length_to_feet(10, 10.0) == (10.0, None)
    assert convert_length_to_feet("10", 10.0) == (10.0, None)
    assert _decode_one(_azimuth_item(length_ft=10)).length_ft == 10.0


# ---------------------------------------------------------------------------
# Diameter (task 330 scope item 3)
# ---------------------------------------------------------------------------


def test_bare_metric_od_converts_to_inches():
    """914.4 mm with no NPS present must still resolve to 36"."""
    inches, note = convert_diameter_to_inches("914.4 mm")

    assert inches == 36.0
    assert note is not None


def test_bare_metric_od_recovered_from_spec_text_without_nps():
    """A metric-only OD line decodes without an accompanying NPS figure."""
    item = _decode_one(
        _azimuth_item(
            diameter=None,
            description="Pipeline OD: 914.4 mm\nWall Thickness: 12.7 mm",
        )
    )

    assert item.diameter == 36.0
    assert item.wall_thickness == 0.5


def test_imperial_diameter_is_left_exactly_as_stated():
    """Conversion must not perturb the imperial sizes the catalog is built on."""
    assert convert_diameter_to_inches("6.625") == (6.625, None)
    assert convert_diameter_to_inches(36) == (36.0, None)


# ---------------------------------------------------------------------------
# Task 326 must not be weakened
# ---------------------------------------------------------------------------


def test_absent_wall_thickness_still_defaults_to_three_eighths():
    """Task 326: a genuinely missing thickness still defaults, with its note."""
    quote = _generate_azimuth_quote(
        _azimuth_item(
            wall_thickness=None,
            description="NPS 36 split sleeve, 10 ft, quantity 1",
            length_ft=10,
        )
    )

    sleeve_line = quote.line_items[0]
    assert DEFAULTED_THICKNESS_NOTE in (sleeve_line.notes or "")
    assert '3/8"' in sleeve_line.description


def test_absent_thickness_default_survives_decoding_directly():
    item = _decode_one(
        _azimuth_item(wall_thickness=None, description="NPS 36 split sleeve", length_ft=10)
    )

    assert item.wall_thickness is None


# ---------------------------------------------------------------------------
# Unit conversion and fraction snapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12.7 mm", 0.5),
        ("12.7mm", 0.5),
        ("9.525 mm", 0.375),
        ("6.35 mm", 0.25),
        ("15.88 mm", 0.625),
        ("19.05 mm", 0.75),
        ("1.27 cm", 0.5),
        # Source documents round metric to 1 decimal, so the converted value
        # lands near but not on a catalog fraction and must snap to it.
        ("12.6 mm", 0.5),
        ("12.8 mm", 0.5),
    ],
)
def test_metric_thickness_snaps_to_a_catalog_fraction(raw, expected):
    inches, note = convert_thickness_to_inches(raw)

    assert inches == expected
    assert note is not None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0.5", 0.5),
        (0.375, 0.375),
        ("1/2", 0.5),
        ('1/2"', 0.5),
        ("0.25", 0.25),
    ],
)
def test_imperial_thickness_needs_no_conversion_note(raw, expected):
    inches, note = convert_thickness_to_inches(raw)

    assert inches == expected
    assert note is None


def test_bare_implausible_thickness_is_read_as_millimetres():
    """No Allan Edwards sleeve has a 12.7 INCH wall, so a bare 12.7 is mm."""
    inches, note = convert_thickness_to_inches(12.7)

    assert inches == 0.5
    assert note is not None


def test_fraction_strings_are_no_longer_discarded():
    """float() rejects '6-5/8' exactly as it rejects '12.7 mm'."""
    assert convert_diameter_to_inches("6-5/8")[0] == 6.625
    assert convert_diameter_to_inches("6 5/8")[0] == 6.625


def test_snapping_agrees_with_the_printed_fraction():
    """The stored number and the label on the quote must never disagree."""
    for raw_inches in (0.49, 0.51, 0.37, 6.62, 36.01, 12.74):
        snapped = snap_to_catalog_fraction(raw_inches)
        assert decimal_to_fraction(snapped) == decimal_to_fraction(raw_inches)


def test_non_dimensional_metric_values_are_not_mistaken_for_dimensions():
    """The Azimuth block also states a pressure in kPag; it is not a dimension."""
    item = _decode_one(
        _azimuth_item(
            wall_thickness=None,
            length_ft=None,
            diameter=None,
            description="Design Pressure / MAOP: 6,895 kPag\nQuantity: 1 pc",
        )
    )

    assert item.wall_thickness is None
    assert item.length_ft is None
    assert item.diameter is None
