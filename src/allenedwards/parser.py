"""RFQ email parser using LLM."""

import email
import re
from dataclasses import dataclass
from email.message import Message
from pathlib import Path
from typing import Any

from .providers.base import LLMProvider

PARSE_SYSTEM_PROMPT = """You are an expert at parsing Request for Quote (RFQ) emails for Allan Edwards Inc.,
a pipeline products manufacturer specializing in sleeves, girth weld bands, and related products.

Your task is to extract structured information from RFQ emails and return it as JSON.

Key domain knowledge:
- Sleeves are identified by inner diameter (ID), wall thickness (w/t), grade (GR50 or GR65), and length
- Common wall thicknesses: 1/4" (0.25), 5/16" (0.3125), 3/8" (0.375), 1/2" (0.5), 5/8" (0.625), 3/4" (0.75)
- Grades: A572 GR50 or A572 GR65 (also written as GR.50, Gr65, etc.)
- Pipe sizes may be written as NPS (nominal pipe size) like "6 inch" or actual ID like "6-5/8"
- "half sole" or "reg half sole" indicates a standard sleeve type
- GTW = Girth Weld bands/bags
- Milling (-M) and Painting (-P) are optional services

Wall thickness notation conversions:
- "1/4" or "14" -> 0.25
- "5/16" or "516" -> 0.3125
- "3/8" or "38" -> 0.375
- "1/2" or "12" -> 0.5
- "5/8" or "58" -> 0.625
- "3/4" or "34" -> 0.75

Return a JSON object with this structure:
{
    "customer_name": "Company name from email domain or signature",
    "contact_name": "Person name from signature",
    "contact_email": "Email address of requester",
    "contact_phone": "Phone if available",
    "ship_to": {
        "company": "Shipping destination company if different",
        "attention": "Person name if specified",
        "street": "Street address",
        "city": "City",
        "state": "State",
        "postal_code": "ZIP",
        "country": "Country if specified"
    },
    "po_number": "Customer purchase order number if provided",
    "items": [
        {
            "product_type": "sleeve|girth_weld|compression|bag|omegawrap|accessory|service",
            "quantity": 30,
            "diameter": "6.625",
            "wall_thickness": "0.25",
            "grade": "50",
            "length_ft": 10,
            "milling": false,
            "painting": false,
            "description": "Raw description from email",
            "notes": "Any special notes"
        }
    ],
    "urgency": "normal|rush",
    "notes": "Any general notes from the email",
    "confidence": 0.95
}

For diameter, convert common sizes:
- "6-5/8" or "6 5/8" -> "6.625"
- "8-5/8" -> "8.625"
- "10-3/4" -> "10.75"
- "12-3/4" -> "12.75"
- NPS sizes may differ from actual OD - use your judgment

For grade, extract just the number: "A572 GR50" -> "50", "Gr.65" -> "65"

The confidence score (0-1) should reflect how certain you are about the parsing.
Lower confidence if specifications are ambiguous or missing critical details."""


@dataclass
class ParsedItem:
    """A single parsed line item from an RFQ."""

    product_type: str
    quantity: int
    description: str
    diameter: float | None = None
    wall_thickness: float | None = None
    grade: int | None = None
    length_ft: float | None = None
    milling: bool = False
    painting: bool = False
    notes: str | None = None


@dataclass
class ShipTo:
    """Shipping address information."""

    company: str | None = None
    attention: str | None = None
    street: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str = "United States"


@dataclass
class ParsedRFQ:
    """Complete parsed RFQ data."""

    customer_name: str | None
    contact_name: str | None
    contact_email: str | None
    contact_phone: str | None
    ship_to: ShipTo | None
    po_number: str | None
    items: list[ParsedItem]
    urgency: str = "normal"
    notes: str | None = None
    confidence: float = 0.0

    # Original email metadata
    message_id: str | None = None
    subject: str | None = None
    raw_body: str | None = None


def extract_email_text(eml_path: Path) -> tuple[Message, str]:
    """Extract text content from an .eml file.

    Returns:
        Tuple of (email message object, plain text body)
    """
    with open(eml_path, "rb") as f:
        msg = email.message_from_binary_file(f)

    body = ""

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == "text/plain":
                charset = part.get_content_charset() or "utf-8"
                payload = part.get_payload(decode=True)
                if payload:
                    body = payload.decode(charset, errors="replace")
                    break
        # Fall back to HTML if no plain text
        if not body:
            for part in msg.walk():
                content_type = part.get_content_type()
                if content_type == "text/html":
                    charset = part.get_content_charset() or "utf-8"
                    payload = part.get_payload(decode=True)
                    if payload:
                        html = payload.decode(charset, errors="replace")
                        body = _strip_html(html)
                        break
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            body = payload.decode(charset, errors="replace")
            if msg.get_content_type() == "text/html":
                body = _strip_html(body)

    return msg, body


def _strip_html(html: str) -> str:
    """Strip HTML tags and decode entities."""
    # Remove script and style elements
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.I)
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.I)
    # Remove HTML tags
    html = re.sub(r"<[^>]+>", " ", html)
    # Decode common entities
    html = html.replace("&nbsp;", " ")
    html = html.replace("&quot;", '"')
    html = html.replace("&amp;", "&")
    html = html.replace("&lt;", "<")
    html = html.replace("&gt;", ">")
    # Collapse whitespace
    html = re.sub(r"\s+", " ", html)
    return html.strip()


def parse_rfq(eml_path: Path, provider: LLMProvider) -> ParsedRFQ:
    """Parse an RFQ email using an LLM provider.

    Args:
        eml_path: Path to the .eml file
        provider: LLM provider to use for parsing

    Returns:
        ParsedRFQ with extracted data
    """
    msg, body = extract_email_text(eml_path)

    # Build prompt with email context
    from_header = msg.get("From", "")
    subject = msg.get("Subject", "")

    prompt = f"""Parse this RFQ email and extract the structured quote request information.

From: {from_header}
Subject: {subject}

Email Body:
{body}

Return the parsed data as JSON."""

    # Call LLM
    result = provider.complete_json(prompt, system=PARSE_SYSTEM_PROMPT)
    po_number = result.get("po_number") or _extract_po_number(body)

    # Convert to dataclasses
    ship_to = None
    if result.get("ship_to"):
        st = result["ship_to"]
        ship_to = ShipTo(
            company=st.get("company"),
            attention=st.get("attention"),
            street=st.get("street"),
            city=st.get("city"),
            state=st.get("state"),
            postal_code=st.get("postal_code"),
            country=st.get("country", "United States"),
        )

    items = []
    for item_data in result.get("items", []):
        item = ParsedItem(
            product_type=item_data.get("product_type", "sleeve"),
            quantity=int(item_data.get("quantity", 1)),
            description=item_data.get("description", ""),
            diameter=_parse_float(item_data.get("diameter")),
            wall_thickness=_parse_float(item_data.get("wall_thickness")),
            grade=_parse_int(item_data.get("grade")),
            length_ft=_parse_float(item_data.get("length_ft")),
            milling=bool(item_data.get("milling", False)),
            painting=bool(item_data.get("painting", False)),
            notes=item_data.get("notes"),
        )
        items.append(item)

    return ParsedRFQ(
        customer_name=result.get("customer_name"),
        contact_name=result.get("contact_name"),
        contact_email=result.get("contact_email"),
        contact_phone=result.get("contact_phone"),
        ship_to=ship_to,
        po_number=po_number,
        items=items,
        urgency=result.get("urgency", "normal"),
        notes=result.get("notes"),
        confidence=float(result.get("confidence", 0.0)),
        message_id=msg.get("Message-ID"),
        subject=subject,
        raw_body=body,
    )


def _extract_po_number(body: str) -> str | None:
    """Extract PO number from email body when present."""
    patterns = [
        r"\bP(?:urchase)?\.?\s*O(?:rder)?\.?\s*(?:#|No\.?|Number)?\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9\-/_.]*)",
        r"\bPO\s*(?:#|No\.?|Number)?\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9\-/_.]*)",
    ]
    for pattern in patterns:
        match = re.search(pattern, body, re.IGNORECASE)
        if match:
            return match.group(1).rstrip(".,;")
    return None


def _parse_float(value: Any) -> float | None:
    """Safely parse a float value."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _parse_int(value: Any) -> int | None:
    """Safely parse an int value."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None
