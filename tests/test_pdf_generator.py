"""Tests for the PDF generator module."""

import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

from allenedwards.pdf_generator import generate_quote_pdf
from allenedwards.pricing import Quote, QuoteLineItem


def test_generate_quote_pdf():
    """Test generating a quote PDF."""
    # Create a sample quote
    line_item = QuoteLineItem(
        sort_order=1,
        product_type="sleeve",
        part_number="S-6.625-14-50-10",
        description='Sleeve, Sealing, 6.625" ID, 1/4" w/t, A572 GR50, 10\' long',
        quantity=30,
        unit_price=Decimal("259.11"),
        total=Decimal("7773.30"),
        weight_per_ft=Decimal("9.18"),
        price_per_lb=Decimal("2.82"),
    )

    quote = Quote(
        quote_number="126-001",
        customer_name="FHR Pipeline and Terminals",
        contact_name="Evan Bohlman",
        contact_email="evan.bohlman@fhr.com",
        contact_phone="612-615-3517",
        ship_to={
            "company": "Cottage Grove Terminal",
            "attention": None,
            "street": "6483 85th St S",
            "city": "Cottage Grove",
            "state": "MN",
            "postal_code": "55016",
            "country": "United States",
        },
        po_number="PO-2026-1042",
        line_items=[line_item],
        subtotal=Decimal("7773.30"),
        shipping_amount=None,
        tax_amount=Decimal("0"),
        total=Decimal("7773.30"),
        notes=None,
    )

    # Generate PDF to temp file
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        output_path = Path(f.name)

    try:
        result_path = generate_quote_pdf(quote, output_path, quote_date=date(2026, 2, 10))

        # Verify PDF was created
        assert result_path.exists()
        assert result_path.stat().st_size > 0

        # Basic PDF validation - check for PDF header
        with open(result_path, "rb") as f:
            header = f.read(8)
            assert header.startswith(b"%PDF-")

    finally:
        # Cleanup
        if output_path.exists():
            output_path.unlink()
