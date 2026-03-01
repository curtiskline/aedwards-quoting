"""End-to-end tests using mock LLM provider."""

import tempfile
from decimal import Decimal
from pathlib import Path

from allenedwards.parser import ParsedItem, ParsedRFQ, parse_rfq
from allenedwards.pdf_generator import generate_quote_pdf
from allenedwards.pricing import generate_quote
from allenedwards.providers.mock import SAMPLE_RFQ_RESPONSE, MockProvider


def test_full_rfq_to_quote_flow():
    """Test the full flow from RFQ email to quote."""
    test_data_dir = Path(__file__).parent / "test_data"
    eml_file = test_data_dir / "Mail Attachment.eml"

    if not eml_file.exists():
        return

    # Parse with mock provider
    provider = MockProvider(SAMPLE_RFQ_RESPONSE)
    rfq = parse_rfq(eml_file, provider)

    # Verify parsing
    assert rfq.customer_name == "FHR Pipeline and Terminals"
    assert rfq.contact_name == "Evan Bohlman"
    assert rfq.po_number == "PO-2026-1042"
    assert len(rfq.items) == 1

    item = rfq.items[0]
    assert item.product_type == "sleeve"
    assert item.quantity == 30
    assert item.diameter == 6.625
    assert item.wall_thickness == 0.25
    assert item.grade == 50
    assert item.length_ft == 10

    # Generate quote
    quote = generate_quote(rfq, "126-TEST")

    # Verify quote
    assert quote.quote_number == "126-TEST"
    assert quote.customer_name == "FHR Pipeline and Terminals"
    assert quote.po_number == "PO-2026-1042"
    assert len(quote.line_items) == 3

    line = quote.line_items[0]
    assert line.part_number == "S-6.58-14-50-10"
    assert line.quantity == 30
    assert line.is_note is False

    shipping_note = quote.line_items[1]
    assert shipping_note.is_note is True
    assert shipping_note.part_number == ""
    assert shipping_note.description == "*Ship LTL Prepay & Add"
    assert shipping_note.unit_price == Decimal("0.00")
    assert shipping_note.total == Decimal("0.00")

    rfq_note = quote.line_items[2]
    assert rfq_note.is_note is True
    assert rfq_note.part_number == ""
    assert rfq_note.description == "RFQ: Evan Bohlman 612-615-3517 evan.bohlman@fhr.com"
    assert rfq_note.unit_price == Decimal("0.00")
    assert rfq_note.total == Decimal("0.00")

    # Calculate expected price:
    # weight_per_ft = 10.69 * ((6.625 + 0.25) * 0.25) / 2 = 9.18 (approximately)
    # unit_price = 9.18 * 2.82 * 10 = 258.88 (approximately)
    # total = 258.88 * 30 = 7766.40 (approximately)
    assert line.unit_price > Decimal("250")
    assert line.total > Decimal("7500")

    # Verify subtotal matches
    assert quote.subtotal == line.total
    assert quote.total == quote.subtotal

    # Generate PDF
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        output_path = Path(f.name)

    try:
        generate_quote_pdf(quote, output_path)
        assert output_path.exists()
        assert output_path.stat().st_size > 1000  # Should be at least 1KB
    finally:
        if output_path.exists():
            output_path.unlink()


def test_price_verification():
    """Verify pricing against the sample quote from the data model spec.

    From the sample PDF:
    - S-12.34-38-50-10 (12-3/4" ID, 3/8" w/t, GR50, 10' long)
    - Quantity: 5
    - Unit Price: $678.48
    - Total: $3,392.40
    """
    from allenedwards.pricing import calculate_sleeve_price

    # 12.75" ID (12-3/4"), 3/8" (0.375) wall, GR50, 10' long
    unit_price, weight_per_ft, price_per_lb = calculate_sleeve_price(
        diameter=12.75,
        wall_thickness=0.375,
        grade=50,
        length_ft=10,
    )

    # The sample shows $678.48 per unit
    # Our calculation should be close (may differ slightly due to rounding)
    # weight_per_ft = 10.69 * ((12.75 + 0.375) * 0.375) / 2 = 26.30
    # unit_price = 26.30 * 2.57 * 10 = 675.91

    # Allow 5% tolerance for rounding differences
    assert abs(float(unit_price) - 678.48) / 678.48 < 0.05


def test_generate_quote_uses_carrier_shipping_note_and_rfq_row_order():
    """Shipping note should come before RFQ contact row when both are present."""
    rfq = ParsedRFQ(
        customer_name="Buckeye",
        contact_name="Daniel Cullison",
        contact_email="dcullison@buckeye.com",
        contact_phone="(835) 205-6974",
        ship_to=None,
        po_number=None,
        items=[
            ParsedItem(
                product_type="sleeve",
                quantity=1,
                description="desc",
                diameter=12.75,
                wall_thickness=0.375,
                grade=50,
                length_ft=10,
            )
        ],
        notes="Ship: Buckeye Transportation",
        confidence=1.0,
        raw_body="Ship: Buckeye Transportation",
    )

    quote = generate_quote(rfq, "126-TEST2")

    assert len(quote.line_items) == 3
    assert quote.line_items[0].is_note is False
    assert quote.line_items[1].is_note is True
    assert quote.line_items[1].description == "Ship: Buckeye Transportation"
    assert quote.line_items[2].is_note is True
    assert quote.line_items[2].description == "RFQ: Daniel Cullison (835) 205-6974 dcullison@buckeye.com"
