"""Tests for the parser module."""

from pathlib import Path

from allenedwards.parser import extract_email_text


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
