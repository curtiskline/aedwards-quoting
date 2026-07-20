"""Tests for the parser module."""

import tempfile
from email.message import EmailMessage
from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfWriter

from allenedwards import parser
from allenedwards.parser import (
    CLASSIFY_SYSTEM_PROMPT,
    PARSE_SYSTEM_PROMPT,
    classify_rfq,
    extract_email_text,
    parse_rfq,
    parse_rfq_multi,
    _extract_quote_number,
    _resolve_quote_number,
)
from allenedwards.pricing import generate_quote
from allenedwards.providers.base import LLMProvider
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


def _write_pdf_email(tmp_path: Path, attachments: list[tuple[str, bytes]]) -> Path:
    """Write a multipart email containing the supplied PDF attachments."""
    message = EmailMessage()
    message.set_content("Please see the attached RFQ.")
    for filename, content in attachments:
        message.add_attachment(content, maintype="application", subtype="pdf", filename=filename)
    path = tmp_path / "rfq.eml"
    path.write_bytes(message.as_bytes())
    return path


def test_extract_email_text_includes_duke_butler_pdf_text(tmp_path):
    """The RFQ parser includes the attachment text that contains the 16-inch specification."""
    fixture = Path(__file__).parents[1] / "data/investigations/duke-butler/26-58-sub-rfp-form.pdf"
    eml_path = _write_pdf_email(tmp_path, [(fixture.name, fixture.read_bytes())])

    _, body = extract_email_text(eml_path)

    assert f"--- Attachment: {fixture.name} ---" in body
    assert "Install 5.1 miles of 16”" in body
    assert f"--- End Attachment: {fixture.name} ---" in body


def test_extract_email_text_includes_multiple_pdf_attachments(tmp_path):
    fixture = Path(__file__).parents[1] / "data/investigations/duke-butler/26-58-sub-rfp-form.pdf"
    contents = fixture.read_bytes()
    eml_path = _write_pdf_email(
        tmp_path,
        [("primary-rfp.pdf", contents), ("supplemental-rfp.pdf", contents)],
    )

    _, body = extract_email_text(eml_path)

    assert "--- Attachment: primary-rfp.pdf ---" in body
    assert "--- Attachment: supplemental-rfp.pdf ---" in body
    assert body.count("Install 5.1 miles of 16”") == 2


def test_extract_email_text_notes_pdf_with_no_text(tmp_path):
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    pdf = BytesIO()
    writer.write(pdf)
    eml_path = _write_pdf_email(tmp_path, [("scanned.pdf", pdf.getvalue())])

    _, body = extract_email_text(eml_path)

    assert "--- Attachment: scanned.pdf ---" in body
    assert "No extractable text found" in body


def test_extract_email_text_truncates_long_pdf_text(tmp_path, monkeypatch):
    fixture = Path(__file__).parents[1] / "data/investigations/duke-butler/26-58-sub-rfp-form.pdf"
    monkeypatch.setattr(parser, "MAX_PDF_EXTRACTION_CHARS", 100)
    eml_path = _write_pdf_email(tmp_path, [(fixture.name, fixture.read_bytes())])

    _, body = extract_email_text(eml_path)

    assert "[Text truncated at 100 characters.]" in body


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


def test_parse_rfq_normalizes_oversleeve_product_type_to_sleeve():
    """Oversleeve requests should parse as sleeve product types."""
    email_content = (
        "From: buyer@example.com\n"
        "Subject: Quote request\n"
        "Content-Type: text/plain; charset=utf-8\n"
        "\n"
        "Please quote 2 oversleeves."
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
                "items": [
                    {
                        "product_type": "oversleeve",
                        "quantity": 2,
                        "description": "ovsz half sole",
                        "diameter": "14.75",
                        "wall_thickness": "0.375",
                        "grade": "50",
                        "length_ft": 10,
                    }
                ],
                "urgency": "normal",
                "confidence": 0.9,
            }
        )
        rfq = parse_rfq(eml_path, provider)
        assert len(rfq.items) == 1
        assert rfq.items[0].product_type == "sleeve"
    finally:
        if eml_path.exists():
            eml_path.unlink()


def test_duke_butler_bag_request_splits_empty_and_on_site_fill_options():
    """Duke–Butler must retain the catalog bag line and flag fill for review."""
    corpus_email = (
        Path(__file__).resolve().parents[1]
        / "data"
        / "test-corpus"
        / "emails"
        / "20260715_112200_cedwards_FW_Duke-Butler.eml"
    )
    provider = MockProvider(
        {
            "customer_name": "Price Gregory International, LLC",
            "contact_name": "Nina Durr",
            "contact_email": "NDurr@pricegregory.com",
            "ship_to": {"company": "Duke - Butler", "state": "OH"},
            "items": [
                {
                    "product_type": "bag",
                    "quantity": 20,
                    "diameter": 16,
                    "description": (
                        "Geotextile bag weights. Price as empty and include separate "
                        "pricing to have them filled on site."
                    ),
                }
            ],
            "urgency": "normal",
            "confidence": 0.95,
        }
    )

    rfq = parse_rfq(corpus_email, provider)

    assert [(item.product_type, item.quantity) for item in rfq.items] == [
        ("bag", 20),
        ("service", 20),
    ]
    assert rfq.items[0].diameter == 16
    assert "empty" in rfq.items[0].description.lower()
    assert "on-site bag filling" in rfq.items[1].description.lower()

    quote = generate_quote(rfq, "126-064")
    material_lines = [line for line in quote.line_items if not line.is_note]

    assert material_lines[0].part_number == "GTW 16"
    assert material_lines[0].unit_price > 0
    assert "empty" in material_lines[0].description.lower()
    assert material_lines[1].part_number == "TBD"
    assert material_lines[1].unit_price == 0
    assert "on-site bag filling" in material_lines[1].description.lower()


def test_parse_prompt_requires_separate_empty_and_on_site_fill_bag_options():
    prompt = PARSE_SYSTEM_PROMPT.lower()

    assert "return two items" in prompt
    assert "empty bags" in prompt
    assert "on-site bag filling" in prompt
    assert "per-pound fill rate" in prompt


@pytest.mark.xfail(
    reason="Depends on task 298: PDF attachment text must reach the RFQ parser prompt.",
)
def test_duke_butler_pdf_attachment_supplies_16in_bag_spec(tmp_path):
    """The Duke–Butler PDF must provide the size used for the empty bag line."""
    corpus_email = (
        Path(__file__).resolve().parents[1]
        / "data"
        / "test-corpus"
        / "emails"
        / "20260715_112200_cedwards_FW_Duke-Butler.eml"
    )
    source_pdf = (
        Path(__file__).resolve().parents[1]
        / "data"
        / "test-corpus"
        / "attachments"
        / "20260715_duke-butler-26-58-sub-rfp-form.pdf"
    )
    _, body = extract_email_text(corpus_email)
    message = EmailMessage()
    message["From"] = "Chip Edwards <cedwards@allanedwards.com>"
    message["Subject"] = "Fwd: Duke - Butler"
    message.set_content(body)
    message.add_attachment(
        source_pdf.read_bytes(),
        maintype="application",
        subtype="pdf",
        filename="26-58 Sub RFP Form.pdf",
    )
    eml_path = tmp_path / "duke-butler-with-rfp.eml"
    eml_path.write_bytes(message.as_bytes())

    class RecordingProvider(MockProvider):
        def __init__(self, response):
            super().__init__(response)
            self.parse_prompt = ""

        def complete_json(self, prompt: str, system: str | None = None) -> dict:
            if prompt.startswith("Parse this RFQ email"):
                self.parse_prompt = prompt
            return super().complete_json(prompt, system)

    provider = RecordingProvider(
        {
            "customer_name": "Price Gregory International, LLC",
            "contact_name": "Nina Durr",
            "contact_email": "NDurr@pricegregory.com",
            "ship_to": {"company": "Duke - Butler", "state": "OH"},
            "items": [
                {
                    "product_type": "bag",
                    "quantity": 20,
                    "diameter": 16,
                    "description": "Price as empty and separately filled on site.",
                }
            ],
            "urgency": "normal",
            "confidence": 0.95,
        }
    )

    rfq = parse_rfq(eml_path, provider)

    assert "attachment: 26-58 sub rfp form.pdf" in provider.parse_prompt.lower()
    assert "16-inch" in provider.parse_prompt.lower()
    assert rfq.items[0].diameter == 16
    assert generate_quote(rfq, "126-064").line_items[0].part_number == "GTW 16"


def test_parse_rfq_fills_missing_contact_from_external_from_header():
    """External From header should fill blanks left by the model."""
    email_content = (
        'From: "Rick Jackson" <rick.jackson@blackhillscorp.com>\n'
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
                "customer_name": None,
                "contact_name": None,
                "contact_email": None,
                "ship_to": None,
                "items": [],
                "urgency": "normal",
                "confidence": 0.9,
            }
        )
        rfq = parse_rfq(eml_path, provider)
        assert rfq.customer_name == "Black Hills Corp."
        assert rfq.contact_name == "Rick Jackson"
        assert rfq.contact_email == "rick.jackson@blackhillscorp.com"
    finally:
        if eml_path.exists():
            eml_path.unlink()


def test_parse_rfq_expands_first_name_from_matching_from_header():
    """First-name-only model output should be expanded from sender display name."""
    email_content = (
        'From: "Michael Connolly" <michael.connolly@atmosenergy.com>\n'
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
                "customer_name": None,
                "contact_name": "Michael",
                "contact_email": None,
                "ship_to": None,
                "items": [],
                "urgency": "normal",
                "confidence": 0.9,
            }
        )
        rfq = parse_rfq(eml_path, provider)
        assert rfq.customer_name == "Atmos Energy"
        assert rfq.contact_name == "Michael Connolly"
        assert rfq.contact_email == "michael.connolly@atmosenergy.com"
    finally:
        if eml_path.exists():
            eml_path.unlink()


def test_parse_rfq_does_not_fill_contact_from_internal_from_header():
    """Allan Edwards senders should not become customer contacts."""
    email_content = (
        'From: "Kent Webber" <kwebber@allanedwards.com>\n'
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
                "customer_name": None,
                "contact_name": None,
                "contact_email": None,
                "ship_to": None,
                "items": [],
                "urgency": "normal",
                "confidence": 0.9,
            }
        )
        rfq = parse_rfq(eml_path, provider)
        assert rfq.customer_name is None
        assert rfq.contact_name is None
        assert rfq.contact_email is None
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


def test_parse_rfq_extracts_item_sku():
    email_content = (
        "From: buyer@example.com\n"
        "Subject: SKU quote\n"
        "Content-Type: text/plain; charset=utf-8\n"
        "\n"
        "Please quote one sleeve."
    )
    with tempfile.NamedTemporaryFile(suffix=".eml", mode="w", delete=False) as f:
        f.write(email_content)
        eml_path = Path(f.name)

    try:
        provider = MockProvider(
            {
                "customer_name": "ACME Corp",
                "contact_name": "Buyer",
                "contact_email": "buyer@example.com",
                "ship_to": None,
                "items": [
                    {
                        "product_type": "sleeve",
                        "quantity": 1,
                        "description": "Half Sole 6-5/8",
                        "sku": "S-6.58-38-50-10",
                    }
                ],
                "urgency": "normal",
                "confidence": 0.95,
            }
        )
        rfq = parse_rfq(eml_path, provider)
        assert len(rfq.items) == 1
        assert rfq.items[0].sku == "S-6.58-38-50-10"
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


class _ClassifierProvider(LLMProvider):
    def __init__(self, response):
        self.response = response

    def complete(self, prompt: str, system: str | None = None) -> str:
        return "{}"

    def complete_json(self, prompt: str, system: str | None = None) -> dict:
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def test_classify_rfq_returns_true_when_model_flags_rfq():
    provider = _ClassifierProvider({"is_rfq": True, "confidence": 0.9, "reason": "quote request"})
    is_rfq, reason = classify_rfq("Need quote", "Please quote 20 sleeves", provider)
    assert is_rfq is True
    assert reason is None


def test_classify_rfq_biases_true_when_provider_fails():
    provider = _ClassifierProvider(RuntimeError("temporary provider failure"))
    is_rfq, reason = classify_rfq("status update", "this is probably not rfq", provider)
    assert is_rfq is True
    assert reason is None


def test_classify_rfq_only_flips_near_random_false_to_true():
    provider = _ClassifierProvider(
        {"is_rfq": False, "confidence": 0.1, "reason": "too little context"}
    )
    is_rfq, reason = classify_rfq("check pricing", "unclear request", provider)
    assert is_rfq is True
    assert reason is None


def test_classify_rfq_trusts_low_confidence_non_rfq_above_random():
    provider = _ClassifierProvider(
        {"is_rfq": False, "confidence": 0.4, "reason": "maybe not a quote request"}
    )
    is_rfq, reason = classify_rfq("check pricing", "unclear request", provider)
    assert is_rfq is True
    assert reason is None


def test_classify_rfq_trusts_medium_confidence_non_rfq():
    provider = _ClassifierProvider({"is_rfq": False, "confidence": 0.6, "reason": "not a quote request"})
    is_rfq, reason = classify_rfq("status update", "this is not an rfq", provider)
    assert is_rfq is False
    assert reason == "not a quote request"


def test_classify_rfq_returns_false_for_personal_email_pattern():
    provider = _ClassifierProvider(
        {
            "is_rfq": False,
            "confidence": 0.83,
            "reason": "personal schedule message with no product request",
        }
    )
    is_rfq, reason = classify_rfq("Dinner plans", "Can you make it to dinner Saturday?", provider)
    assert is_rfq is False
    assert reason == "personal schedule message with no product request"


def test_classify_prompt_does_not_instruct_false_positive_bias():
    prompt = CLASSIFY_SYSTEM_PROMPT.lower()

    assert "prefer false positives" not in prompt
    assert "lean true" not in prompt
    assert "personal messages" in prompt


def test_claude_provider_retries_on_json_decode_error(monkeypatch):
    """complete_json should retry once when Claude returns malformed JSON."""
    import json
    from unittest.mock import patch, MagicMock
    from allenedwards.providers.claude import ClaudeProvider

    call_count = [0]
    good_response = '{"is_rfq": true}'
    bad_response = '{"is_rfq": true,}'  # trailing comma — invalid JSON

    def fake_complete(self, prompt, system=None):
        call_count[0] += 1
        if call_count[0] == 1:
            return bad_response
        return good_response

    with patch.object(ClaudeProvider, "complete", fake_complete):
        provider = ClaudeProvider.__new__(ClaudeProvider)
        result = provider.complete_json("test prompt")

    assert call_count[0] == 2
    assert result == {"is_rfq": True}


def test_claude_provider_raises_on_repeated_json_decode_error(monkeypatch):
    """complete_json should raise JSONDecodeError if both attempts return bad JSON."""
    import json
    from unittest.mock import patch
    from allenedwards.providers.claude import ClaudeProvider

    bad_response = '{"broken": ,}'

    def fake_complete(self, prompt, system=None):
        return bad_response

    with patch.object(ClaudeProvider, "complete", fake_complete):
        provider = ClaudeProvider.__new__(ClaudeProvider)
        try:
            provider.complete_json("test prompt")
            assert False, "Expected JSONDecodeError"
        except json.JSONDecodeError:
            pass
