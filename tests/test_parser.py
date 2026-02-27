"""Tests for the parser module."""

import tempfile
from pathlib import Path

from allenedwards.parser import extract_email_text, parse_rfq
from allenedwards.providers.mock import MockProvider


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
