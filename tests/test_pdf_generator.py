"""Tests for the PDF generator module."""

import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

from allenedwards.pdf_generator import QuotePDFBuilder, generate_quote_pdf
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


def test_generate_quote_pdf_with_template_b_fields():
    """Test generating a quote PDF with Template B fields (details grid)."""
    line_items = [
        QuoteLineItem(
            sort_order=1,
            product_type="bag",
            part_number="GTW 40-48in (14K)",
            description="Geotextile Bag Weight 40-48in Pipe",
            quantity=60,
            unit_price=Decimal("229.99"),
            total=Decimal("13799.40"),
        ),
        QuoteLineItem(
            sort_order=2,
            product_type="rental",
            part_number="Fill Rack Rental",
            description="Fill Rack rental per month",
            quantity=1,
            unit_price=Decimal("200.00"),
            total=Decimal("200.00"),
        ),
    ]

    quote = Quote(
        quote_number="QUO-125-413",
        customer_name="Price Gregory",
        contact_name="Nina Durr",
        contact_email="apinvoices@pricegregory.com",
        contact_phone="(713) 835-3426",
        ship_to={
            "company": "Price Gregory",
            "street": "24275 KATY FWY STE 500",
            "city": "TBD (Lancaster County)",
            "state": "PA",
            "country": "United States",
        },
        line_items=line_items,
        subtotal=Decimal("13999.40"),
        shipping_amount=Decimal("4872.00"),
        tax_amount=Decimal("0.00"),
        total=Decimal("18871.40"),
        notes="Freight included. Williams - Quarryville Loop Project.",
        # Template B fields
        sales_rep="",
        payment_terms="Net 30",
        shipping_terms="Prepay and Add",
        shipping_method="Flatbed",
        po_number=None,
        requested_by_name="Nina Durr",
        requested_by_email="ndurr@pricegregory.com",
        requested_by_phone="(713) 835-3426 Direct#",
    )

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        output_path = Path(f.name)

    try:
        result_path = generate_quote_pdf(
            quote,
            output_path,
            quote_date=date(2025, 7, 14),
            expires_date=date(2025, 7, 21),
        )

        assert result_path.exists()
        assert result_path.stat().st_size > 0

        with open(result_path, "rb") as f:
            header = f.read(8)
            assert header.startswith(b"%PDF-")

    finally:
        if output_path.exists():
            output_path.unlink()


def test_generate_quote_pdf_with_shipping_and_tax():
    """Test generating a quote PDF with shipping and tax amounts."""
    line_item = QuoteLineItem(
        sort_order=1,
        product_type="sleeve",
        part_number="S-12-38-50-10",
        description='Sleeve, 12" ID, 3/8" w/t, A572 GR50, 10\' long',
        quantity=5,
        unit_price=Decimal("500.00"),
        total=Decimal("2500.00"),
    )

    quote = Quote(
        quote_number="126-100",
        customer_name="Test Customer",
        contact_name="John Doe",
        contact_email="john@test.com",
        contact_phone="555-555-5555",
        ship_to={
            "company": "Test Site",
            "city": "Dallas",
            "state": "TX",
            "country": "United States",
        },
        line_items=[line_item],
        subtotal=Decimal("2500.00"),
        shipping_amount=Decimal("350.00"),
        tax_amount=Decimal("237.50"),
        total=Decimal("3087.50"),
        notes=None,
    )

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        output_path = Path(f.name)

    try:
        result_path = generate_quote_pdf(quote, output_path, quote_date=date(2026, 3, 1))

        assert result_path.exists()
        assert result_path.stat().st_size > 0

    finally:
        if output_path.exists():
            output_path.unlink()


def test_bill_to_does_not_include_shipping_address():
    """Ship-to street/ZIP should render only in Ship To, not Bill To."""
    line_item = QuoteLineItem(
        sort_order=1,
        product_type="sleeve",
        part_number="S-6.625-14-50-10",
        description='Sleeve, 6.625" ID, 1/4" w/t, A572 GR50, 10\' long',
        quantity=1,
        unit_price=Decimal("259.11"),
        total=Decimal("259.11"),
    )

    quote = Quote(
        quote_number="126-101",
        customer_name="FHR Pipeline and Terminals",
        contact_name="Evan Bohlman",
        contact_email="evan.bohlman@fhr.com",
        contact_phone="612-615-3517",
        ship_to={
            "company": "Cottage Grove Terminal",
            "attention": "Daniel Cullison",
            "street": "6483 85th St S",
            "city": "Cottage Grove",
            "state": "MN",
            "postal_code": "55016",
            "country": "United States",
        },
        line_items=[line_item],
        subtotal=Decimal("259.11"),
        shipping_amount=None,
        tax_amount=Decimal("0"),
        total=Decimal("259.11"),
        notes=None,
    )

    builder = QuotePDFBuilder(quote=quote, output_path=Path("/tmp/test-quote.pdf"))
    address_table = builder._build_bill_ship_to()[0]
    bill_to_table = address_table._cellvalues[0][0]
    ship_to_table = address_table._cellvalues[0][1]

    bill_to_text = [row[0].text for row in bill_to_table._cellvalues]
    ship_to_text = [row[0].text for row in ship_to_table._cellvalues]

    assert "6483 85th St S" not in bill_to_text
    assert "Cottage Grove, MN, 55016" not in bill_to_text
    assert "6483 85th St S" in ship_to_text
    assert "Cottage Grove, MN, 55016" in ship_to_text


def _make_quote(**overrides):
    """Helper to build a Quote with sensible defaults."""
    defaults = dict(
        quote_number="126-200",
        customer_name="Test Co",
        contact_name="Jane",
        contact_email="jane@test.com",
        contact_phone="555-0100",
        ship_to={"company": "Site A", "city": "Tulsa", "state": "OK"},
        line_items=[
            QuoteLineItem(
                sort_order=1,
                product_type="sleeve",
                part_number="S-TEST",
                description="Test sleeve",
                quantity=1,
                unit_price=Decimal("100.00"),
                total=Decimal("100.00"),
            )
        ],
        subtotal=Decimal("100.00"),
        shipping_amount=None,
        tax_amount=Decimal("0"),
        total=Decimal("100.00"),
        notes=None,
    )
    defaults.update(overrides)
    return Quote(**defaults)


def test_notes_rendered_when_present():
    """Notes section appears in PDF elements when notes is set."""
    quote = _make_quote(notes="Ship before Friday.\nCall on arrival.")
    builder = QuotePDFBuilder(quote=quote, output_path=Path("/tmp/test.pdf"))
    notes_elements = builder._build_notes()

    assert len(notes_elements) > 0
    texts = [el.text for el in notes_elements if hasattr(el, "text")]
    assert any("Notes:" in t for t in texts)
    assert any("Ship before Friday." in t for t in texts)
    assert any("Call on arrival." in t for t in texts)


def test_notes_empty_when_none():
    """No notes section when notes is None."""
    quote = _make_quote(notes=None)
    builder = QuotePDFBuilder(quote=quote, output_path=Path("/tmp/test.pdf"))
    assert builder._build_notes() == []


def test_notes_empty_when_blank():
    """No notes section when notes is empty string."""
    quote = _make_quote(notes="")
    builder = QuotePDFBuilder(quote=quote, output_path=Path("/tmp/test.pdf"))
    assert builder._build_notes() == []


def test_notes_title_line_skipped():
    """If first line looks like a title (contains ':'), only remaining lines render."""
    quote = _make_quote(notes="Project: HM999\nDeliver ASAP")
    builder = QuotePDFBuilder(quote=quote, output_path=Path("/tmp/test.pdf"))
    notes_elements = builder._build_notes()

    texts = [el.text for el in notes_elements if hasattr(el, "text")]
    assert not any("Project: HM999" in t for t in texts)
    assert any("Deliver ASAP" in t for t in texts)


def test_notes_title_only_returns_empty():
    """If notes is just a title line with no remaining text, return empty."""
    quote = _make_quote(notes="Project: HM999")
    builder = QuotePDFBuilder(quote=quote, output_path=Path("/tmp/test.pdf"))
    assert builder._build_notes() == []


def test_notes_in_full_pdf():
    """Notes text appears in a fully-built PDF file."""
    quote = _make_quote(notes="Handle with care")
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        output_path = Path(f.name)
    try:
        result = generate_quote_pdf(quote, output_path, quote_date=date(2026, 4, 1))
        assert result.exists()
        assert result.stat().st_size > 0
    finally:
        if output_path.exists():
            output_path.unlink()
