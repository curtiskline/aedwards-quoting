"""Tests for the parser module."""

import tempfile
from pathlib import Path

from allenedwards.parser import (
    extract_email_text,
    parse_rfq,
    parse_rfq_multi,
    _extract_quote_number,
    _resolve_quote_number,
)
from allenedwards.providers.mock import MockProvider, SAMPLE_MULTI_QUOTE_RESPONSE


def test_extract_email_text():
    """Test extracting text from .eml file."""
    test_data_dir = Path(__file__).parent / "test_data"
    eml_file = test_data_dir / "Mail Attachment.eml"

    if not eml_file.exists():
        # Skip if test data not available
        return

    msg, body = extract_email_text(eml_file)

    # Check email headers
    assert msg.get("From") is not None
    assert "Bohlman" in msg.get("From")
    assert msg.get("Subject") == "Pipeline sleeve quote request"

    # Check body extraction
    assert "30 pieces" in body
    assert "6-5/8" in body
    assert "1/4" in body
    assert "GR50" in body
    assert "10'" in body or "10 feet" in body.lower() or "10' long" in body


def test_extract_email_text_includes_embedded_rfc822():
    """Text from message/rfc822 attachments should be included in body extraction."""
    embedded_message = (
        "From: buyer@example.com\n"
        "Subject: RFQ details\n"
        "Content-Type: text/plain; charset=utf-8\n"
        "\n"
        "Need 12 pieces of 6-5/8 x 1/4 GR50 sleeves, 10 ft long."
    )
    email_content = (
        "From: forwarder@example.com\n"
        "Subject: Fwd: RFQ\n"
        "MIME-Version: 1.0\n"
        'Content-Type: multipart/mixed; boundary="outer"\n'
        "\n"
        "--outer\n"
        "Content-Type: text/plain; charset=utf-8\n"
        "\n"
        "Forwarding RFQ attached.\n"
        "--outer\n"
        "Content-Type: message/rfc822\n"
        "Content-Disposition: attachment; filename=\"rfq.eml\"\n"
        "\n"
        f"{embedded_message}\n"
        "--outer--\n"
    )

    with tempfile.NamedTemporaryFile(suffix=".eml", mode="w", delete=False) as f:
        f.write(email_content)
        eml_path = Path(f.name)

    try:
        _, body = extract_email_text(eml_path)
        assert "Forwarding RFQ attached." in body
        assert "Need 12 pieces of 6-5/8 x 1/4 GR50 sleeves" in body
    finally:
        if eml_path.exists():
            eml_path.unlink()


def test_parse_rfq_uses_llm_po_number():
    """PO number from model response should be preserved."""
    email_content = (
        "From: buyer@example.com\n"
        "Subject: Quote request\n"
        "Content-Type: text/plain; charset=utf-8\n"
        "\n"
        "Please quote 2 sleeves."
    )
    with tempfile.NamedTemporaryFile(suffix=".eml", mode="w", delete=False) as f:
        f.write(email_content)
        eml_path = Path(f.name)

    try:
        provider = MockProvider(
            {
                "customer_name": "ACME",
                "contact_name": "Buyer",
                "contact_email": "buyer@example.com",
                "ship_to": None,
                "po_number": "PO-777",
                "items": [],
                "urgency": "normal",
                "confidence": 0.9,
            }
        )
        rfq = parse_rfq(eml_path, provider)
        assert rfq.po_number == "PO-777"
    finally:
        if eml_path.exists():
            eml_path.unlink()


def test_parse_rfq_extracts_po_number_from_body():
    """Fallback PO extractor should parse PO values from email text."""
    email_content = (
        "From: buyer@example.com\n"
        "Subject: Quote request\n"
        "Content-Type: text/plain; charset=utf-8\n"
        "\n"
        "Please quote 2 sleeves. PO #: ABC-12345."
    )
    with tempfile.NamedTemporaryFile(suffix=".eml", mode="w", delete=False) as f:
        f.write(email_content)
        eml_path = Path(f.name)

    try:
        provider = MockProvider(
            {
                "customer_name": "ACME",
                "contact_name": "Buyer",
                "contact_email": "buyer@example.com",
                "ship_to": None,
                "items": [],
                "urgency": "normal",
                "confidence": 0.9,
            }
        )
        rfq = parse_rfq(eml_path, provider)
        assert rfq.po_number == "ABC-12345"
    finally:
        if eml_path.exists():
            eml_path.unlink()


def test_parse_rfq_multi_returns_multiple_quotes():
    """Test that parse_rfq_multi returns multiple ParsedRFQ objects for multi-quote emails."""
    email_content = (
        "From: buyer@buckeye.com\n"
        "Subject: Multiple quote requests\n"
        "Content-Type: text/plain; charset=utf-8\n"
        "\n"
        "Please quote for 4 separate project lines."
    )
    with tempfile.NamedTemporaryFile(suffix=".eml", mode="w", delete=False) as f:
        f.write(email_content)
        eml_path = Path(f.name)

    try:
        provider = MockProvider(SAMPLE_MULTI_QUOTE_RESPONSE)
        rfqs = parse_rfq_multi(eml_path, provider)

        # Should return 4 separate quote requests
        assert len(rfqs) == 4

        # Check first quote
        assert rfqs[0].customer_name == "Buckeye Partners"
        assert rfqs[0].project_line == "XB403CL Line"
        assert rfqs[0].ship_to is not None
        assert rfqs[0].ship_to.city == "Huntington"
        assert rfqs[0].ship_to.state == "IN"
        assert len(rfqs[0].items) == 1
        assert rfqs[0].items[0].diameter == 8.625

        # Check second quote
        assert rfqs[1].project_line == "HM999A3 Line"
        assert rfqs[1].ship_to.city == "Elburn"
        assert rfqs[1].ship_to.state == "IL"
        assert rfqs[1].items[0].milling is True

        # Check third quote
        assert rfqs[2].project_line == "XF001-002XB Line"
        assert rfqs[2].ship_to.city == "Griffith"
        assert rfqs[2].items[0].painting is True

        # Check fourth quote
        assert rfqs[3].project_line == "ZI165LI-2 Line"
        assert rfqs[3].ship_to.city == "Lima"
        assert rfqs[3].ship_to.state == "OH"

    finally:
        if eml_path.exists():
            eml_path.unlink()


def test_parse_rfq_multi_handles_single_quote():
    """Test that parse_rfq_multi returns a list with one element for single-quote emails."""
    email_content = (
        "From: buyer@example.com\n"
        "Subject: Single quote request\n"
        "Content-Type: text/plain; charset=utf-8\n"
        "\n"
        "Please quote 10 sleeves."
    )
    with tempfile.NamedTemporaryFile(suffix=".eml", mode="w", delete=False) as f:
        f.write(email_content)
        eml_path = Path(f.name)

    try:
        # Use legacy format (no quotes array)
        provider = MockProvider(
            {
                "customer_name": "ACME Corp",
                "contact_name": "Buyer",
                "contact_email": "buyer@example.com",
                "ship_to": {
                    "company": "ACME Warehouse",
                    "city": "Dallas",
                    "state": "TX",
                },
                "items": [
                    {
                        "product_type": "sleeve",
                        "quantity": 10,
                        "diameter": "6.625",
                        "wall_thickness": "0.25",
                        "grade": "50",
                        "length_ft": 10,
                    }
                ],
                "urgency": "normal",
                "confidence": 0.95,
            }
        )
        rfqs = parse_rfq_multi(eml_path, provider)

        # Should return a list with one element
        assert len(rfqs) == 1
        assert rfqs[0].customer_name == "ACME Corp"
        assert rfqs[0].ship_to.city == "Dallas"
        assert rfqs[0].project_line is None  # No project line for single quotes

    finally:
        if eml_path.exists():
            eml_path.unlink()


def test_parse_rfq_returns_first_quote_from_multi():
    """Test that parse_rfq (single) returns the first quote when given a multi-quote email."""
    email_content = (
        "From: buyer@buckeye.com\n"
        "Subject: Multiple quote requests\n"
        "Content-Type: text/plain; charset=utf-8\n"
        "\n"
        "Please quote for 4 separate project lines."
    )
    with tempfile.NamedTemporaryFile(suffix=".eml", mode="w", delete=False) as f:
        f.write(email_content)
        eml_path = Path(f.name)

    try:
        provider = MockProvider(SAMPLE_MULTI_QUOTE_RESPONSE)
        rfq = parse_rfq(eml_path, provider)  # Use single-quote function

        # Should return the first quote
        assert rfq.customer_name == "Buckeye Partners"
        assert rfq.project_line == "XB403CL Line"
        assert rfq.ship_to.city == "Huntington"

    finally:
        if eml_path.exists():
            eml_path.unlink()

def test_parse_rfq_filters_type_leak_po_number():
    """LLM returning 'int' or other type names should be filtered out."""
    email_content = (
        "From: buyer@example.com\n"
        "Subject: Quote request\n"
        "Content-Type: text/plain; charset=utf-8\n"
        "\n"
        "Please quote 2 sleeves."
    )
    with tempfile.NamedTemporaryFile(suffix=".eml", mode="w", delete=False) as f:
        f.write(email_content)
        eml_path = Path(f.name)

    try:
        # Mock response where LLM returns "int" as the po_number (type leak)
        provider = MockProvider(
            {
                "customer_name": "Test Co",
                "po_number": "int",  # Type name leak
                "items": [
                    {
                        "product_type": "sleeve",
                        "quantity": 2,
                        "diameter": "12.75",
                        "wall_thickness": "0.375",
                        "grade": "50",
                        "length_ft": 10,
                    }
                ],
                "confidence": 1.0,
            }
        )
        rfq = parse_rfq(eml_path, provider)

        # po_number should be None, not "int"
        assert rfq.po_number is None

    finally:
        if eml_path.exists():
            eml_path.unlink()


def test_parse_rfq_rejects_name_fragment_po_number():
    """LLM-provided PO values that look like name fragments should be dropped."""
    email_content = (
        "From: Bonnie Portner <bonniep@mkspvf.com>\n"
        "Subject: Re: RFQ\n"
        "Content-Type: text/plain; charset=utf-8\n"
        "\n"
        "Happy Monday Jamee.\n"
        "Please quote 1 - 6 5/8 ID 250 wall half sole.\n"
        "--\n"
        "Bonnie Portner\n"
    )
    with tempfile.NamedTemporaryFile(suffix=".eml", mode="w", delete=False) as f:
        f.write(email_content)
        eml_path = Path(f.name)

    try:
        provider = MockProvider(
            {
                "customer_name": "MKS Pipe & Valve",
                "contact_name": "Bonnie Portner",
                "po_number": "rtner",
                "items": [{"product_type": "sleeve", "quantity": 1, "description": "6 5/8 ID"}],
                "confidence": 0.9,
            }
        )
        rfq = parse_rfq(eml_path, provider)
        assert rfq.po_number is None
    finally:
        if eml_path.exists():
            eml_path.unlink()


def test_extract_quote_number_from_subject():
    """Quote number regex should find supported identifiers in text."""
    assert _extract_quote_number("Re: QUO-126-048 HALF SOLE") == "QUO-126-048"
    assert _extract_quote_number("QUO-125-383 Sleeve Order") == "QUO-125-383"
    assert _extract_quote_number("FW: QUO-125-567 SO-125-0348") == "SO-125-0348"
    assert _extract_quote_number("Invoice INV-125-0428 attached") == "INV-125-0428"
    assert _extract_quote_number("No quote here") is None
    assert _extract_quote_number("") is None


def test_resolve_quote_number_prefers_llm():
    """LLM-provided quote number should be used when valid."""
    assert _resolve_quote_number("QUO-126-048", "Some subject", "body") == "QUO-126-048"
    assert _resolve_quote_number("SO-125-0348", "Some subject", "body") == "SO-125-0348"
    assert _resolve_quote_number("INV-125-0428", "Some subject", "body") == "INV-125-0428"


def test_resolve_quote_number_falls_back_to_subject():
    """When LLM returns null, regex should extract from subject."""
    assert _resolve_quote_number(None, "Re: QUO-126-048 stuff", "body") == "QUO-126-048"
    assert _resolve_quote_number(None, "FW: QUO-125-567 SO-125-0348", "body") == "SO-125-0348"


def test_resolve_quote_number_falls_back_to_body():
    """When subject has no match, regex should extract from body."""
    assert _resolve_quote_number(None, "No match", "See QUO-125-383 for details") == "QUO-125-383"


def test_resolve_quote_number_rejects_invalid():
    """Invalid LLM value should trigger fallback."""
    assert _resolve_quote_number("not-a-quote", "Re: QUO-126-048", "body") == "QUO-126-048"
    assert _resolve_quote_number("123", "no match", "no match") is None


def test_resolve_quote_number_extracts_from_llm_free_text():
    """LLM free-text values should still allow quote extraction."""
    assert _resolve_quote_number("quote: QUO-125-513", "subject", "body") == "QUO-125-513"


def test_parse_rfq_extracts_quote_number_from_subject():
    """Parser should extract quote_number from email subject via regex fallback."""
    email_content = (
        "From: buyer@example.com\n"
        "Subject: Re: QUO-126-048 HALF SOLE 16IDX5FT\n"
        "Content-Type: text/plain; charset=utf-8\n"
        "\n"
        "Please quote 2 sleeves."
    )
    with tempfile.NamedTemporaryFile(suffix=".eml", mode="w", delete=False) as f:
        f.write(email_content)
        eml_path = Path(f.name)

    try:
        provider = MockProvider(
            {
                "customer_name": "ACME",
                "contact_name": "Buyer",
                "contact_email": "buyer@example.com",
                "items": [],
                "confidence": 0.9,
            }
        )
        rfq = parse_rfq(eml_path, provider)
        assert rfq.quote_number == "QUO-126-048"
    finally:
        if eml_path.exists():
            eml_path.unlink()


def test_parse_rfq_uses_llm_quote_number():
    """Parser should use LLM-provided quote_number when valid."""
    email_content = (
        "From: buyer@example.com\n"
        "Subject: Quote request\n"
        "Content-Type: text/plain; charset=utf-8\n"
        "\n"
        "Please quote 2 sleeves."
    )
    with tempfile.NamedTemporaryFile(suffix=".eml", mode="w", delete=False) as f:
        f.write(email_content)
        eml_path = Path(f.name)

    try:
        provider = MockProvider(
            {
                "customer_name": "ACME",
                "quote_number": "QUO-126-052",
                "items": [],
                "confidence": 0.9,
            }
        )
        rfq = parse_rfq(eml_path, provider)
        assert rfq.quote_number == "QUO-126-052"
    finally:
        if eml_path.exists():
            eml_path.unlink()


def test_parse_rfq_no_quote_number():
    """When no quote number exists, field should be None."""
    email_content = (
        "From: buyer@example.com\n"
        "Subject: Quote request\n"
        "Content-Type: text/plain; charset=utf-8\n"
        "\n"
        "Please quote 2 sleeves."
    )
    with tempfile.NamedTemporaryFile(suffix=".eml", mode="w", delete=False) as f:
        f.write(email_content)
        eml_path = Path(f.name)

    try:
        provider = MockProvider(
            {
                "customer_name": "ACME",
                "items": [],
                "confidence": 0.9,
            }
        )
        rfq = parse_rfq(eml_path, provider)
        assert rfq.quote_number is None
    finally:
        if eml_path.exists():
            eml_path.unlink()


def test_parse_rfq_uses_contact_info_for_ship_to_fallback():
    """When no explicit ship-to is provided, LLM should use contact's company/location."""
    email_content = (
        "From: Veronica Pisani <veronica.pisani@bp.com>\n"
        "Subject: BP Pipeline 20\" Sleeve Order\n"
        "Content-Type: text/plain; charset=utf-8\n"
        "\n"
        "Please quote sleeves.\n"
        "\n"
        "Veronica Pisani\n"
        "BP Terminals & Pipelines\n"
        "Chicago, IL\n"
    )
    with tempfile.NamedTemporaryFile(suffix=".eml", mode="w", delete=False) as f:
        f.write(email_content)
        eml_path = Path(f.name)

    try:
        # Simulate LLM using contact info as ship-to fallback
        provider = MockProvider(
            {
                "customer_name": "BP",
                "contact_name": "Veronica Pisani",
                "contact_email": "veronica.pisani@bp.com",
                "quotes": [
                    {
                        "ship_to": {
                            "company": "BP Terminals & Pipelines",
                            "city": "Chicago",
                            "state": "IL",
                        },
                        "items": [
                            {
                                "product_type": "sleeve",
                                "quantity": 10,
                                "diameter": "20",
                                "wall_thickness": "0.375",
                                "grade": "50",
                                "length_ft": 10,
                            }
                        ],
                    }
                ],
                "confidence": 0.9,
            }
        )
        rfq = parse_rfq(eml_path, provider)
        # Verify ship_to was populated from contact info
        assert rfq.ship_to is not None
        assert rfq.ship_to.company == "BP Terminals & Pipelines"
        assert rfq.ship_to.city == "Chicago"
        assert rfq.ship_to.state == "IL"
    finally:
        if eml_path.exists():
            eml_path.unlink()
