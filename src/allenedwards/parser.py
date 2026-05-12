"""RFQ email parser using LLM."""

import email
import re
from dataclasses import dataclass
from email.message import Message
from pathlib import Path
from typing import Any

from flask import has_app_context
from sqlalchemy import inspect

from .providers.base import LLMProvider

QUOTE_NUMBER_PATTERN = re.compile(r"\b(?:QUO|SO|INV)-\d+-\d+\b", re.IGNORECASE)
DEFAULT_RFQ_CLASSIFY_BODY_CHARS = 500

CLASSIFY_SYSTEM_PROMPT = """You are a strict binary classifier for Allan Edwards sales operations.
Classify whether an incoming message is likely an RFQ for pipe/sleeve/girth-weld products.

Return JSON:
{
  "is_rfq": true|false,
  "confidence": 0.0-1.0,
  "reason": "short explanation"
}

Classification guidance:
- Prefer false positives over false negatives.
- Messages asking for quote/pricing/lead time on steel pipe products are RFQs.
- Internal status updates, invoices, shipping updates, signatures-only, or unrelated topics are not RFQs.
- If uncertain, lean true.
"""

PARSE_SYSTEM_PROMPT = """You are an expert at parsing Request for Quote (RFQ) emails for Allan Edwards Inc.,
a pipeline products manufacturer specializing in sleeves, girth weld bands, and related products.

Your task is to extract structured information from RFQ emails and return it as JSON.

Key domain knowledge:
- Sleeves are identified by inner diameter (ID), wall thickness (w/t), grade (GR50 or GR65), and length
- Oversleeves (ovsz) fit OVER the outside of carrier pipe + standard sleeve - use product_type "oversleeve"
- Common wall thicknesses: 1/4" (0.25), 5/16" (0.3125), 3/8" (0.375), 1/2" (0.5), 5/8" (0.625), 3/4" (0.75)
- Grades: A572 GR50 or A572 GR65 (also written as GR.50, Gr65, etc.)
- Pipe sizes may be written as NPS (nominal pipe size) like "6 inch" or actual ID like "6-5/8"
- "half sole" or "reg half sole" indicates a standard sleeve type
- "ovsz" or "oversleeve" indicates an oversleeve (fits over pipe + existing sleeve)
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
    "quote_number": "Allan Edwards quote/document number if present in subject or body (e.g. QUO-126-048, SO-125-0348, INV-125-0428), null otherwise",
    "quotes": [
        {
            "project_line": "Project reference like 'XB403CL Line' if mentioned, null otherwise",
            "ship_to": {
                "company": "End customer/pipeline company receiving the goods (NEVER 'Allan Edwards' — use the pipeline, utility, or requesting company instead)",
                "attention": "Person name if specified",
                "street": "Street address if available",
                "city": "City (use contact's city from signature if no explicit ship-to)",
                "state": "State (use contact's state from signature if no explicit ship-to)",
                "postal_code": "ZIP if available",
                "country": "Country if specified"
            },
            "po_number": "Customer purchase order number for this quote if explicitly provided, otherwise null",
            "items": [
                {
                    "product_type": "sleeve|oversleeve|girth_weld|compression|bag|omegawrap|accessory|service",
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
            "notes": "Notes specific to this quote request"
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
API 5L grades must be mapped to A572 equivalents:
- API 5L GR B, X-42, X-46 -> grade "50" (A572 GR50)
- API 5L X-52, X-56, X-60, X-65, X-70 -> grade "65" (A572 GR65)
If no grade is specified at all, default to "50" (A572 GR50 is the most common).

IMPORTANT: grade and length_ft must ALWAYS be provided for each item — never return null.
- If grade is not explicitly stated, infer it from context or default to "50".
- If length is not stated, infer from context:
  - "bundle" of sleeves typically means standard lengths; use 10 for sleeves.
  - If a total footage is given (e.g., "10 FT"), that IS the length_ft.
  - If truly unknown, default to 10 for sleeves/oversleeves, 6 for girth welds.

The confidence score (0-1) should reflect how certain you are about the parsing.
Lower confidence if specifications are ambiguous or missing critical details.

IMPORTANT: Some emails contain MULTIPLE separate quote requests, each with:
- A different project line reference (e.g., "XB403CL Line", "HM999A3 Line")
- A different ship-to address
- Different items

If you detect multiple quote requests, return them as an array of quote objects in the "quotes" field.
If there's only one quote request, still return it in the "quotes" array (with one element).

SHIP-TO ADDRESS RULES:
- If an explicit ship-to address is provided (e.g., "Ship to:", "Deliver to:"), use that address.
- If no explicit ship-to is provided, use the contact's/customer's company name and location from their signature or email domain.
- CRITICAL: ship_to.company must NEVER be "Allan Edwards", "Allan Edwards Inc.", "AE", or any Allan Edwards variant. Allan Edwards is the seller/manufacturer of these products. The ship_to.company should be the end customer — typically the pipeline company, utility, or energy company that will receive the goods. Examples of correct ship_to.company values: "DTE Gas Company", "Kinder Morgan", "Energy Transfer", "Boardwalk Pipeline Partners", "Centerpoint Energy". If you cannot determine the end customer, use the requesting contact's company (the distributor or contractor sending the RFQ).
- When the email mentions a pipeline company, utility, or project owner (e.g., in project references, PO descriptions, or job site details), that entity is likely the ship_to.company — not Allan Edwards or the sales intermediary.
- Always try to populate at least company, city, and state for ship_to from available information.
- Extract postal_code/ZIP when available in the ship-to address or in the contact's signature.
- Only return ship_to as null if absolutely no location information can be determined.

PO number extraction rules:
- Only return po_number when the email explicitly provides a PO number value.
- If no explicit PO number is present, return null for po_number.
- Never infer po_number from contact names, company names, signatures, or random text fragments.

Quote number extraction rules:
- Look for Allan Edwards quote numbers in the email subject line and body.
- Quote/document numbers may follow patterns like QUO-NNN-NNN, SO-NNN-NNNN, or INV-NNN-NNNN.
- Return the quote_number exactly as it appears (e.g. "QUO-126-048", "SO-125-0348", "INV-125-0428").
- If no quote number is found, return null for quote_number.

Contact extraction rules:
- The contact is the CUSTOMER requesting the quote, not an Allan Edwards employee.
- In forwarded email chains (FW:/RE:), look for the ORIGINAL external requester, not the forwarder.
- If the email contains structured order details with "Ordered by" or "Requested by", use that person's contact info.
- For phone numbers: if a person lists both an office (O:) and cell/mobile (C:/M:) number, prefer the cell/mobile number.
- Ignore phone numbers and emails from @allanedwards.com addresses — those are internal.
- If multiple external contacts appear, prefer the one who initiated the request or is listed as the ordering contact."""


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
    sku: str | None = None


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
    quote_number: str | None
    items: list[ParsedItem]
    urgency: str = "normal"
    notes: str | None = None
    confidence: float = 0.0

    # Project line reference (e.g., "XB403CL Line") for multi-quote emails
    project_line: str | None = None

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

    body = _extract_message_text(msg)

    return msg, body


def _extract_message_text(msg: Message) -> str:
    """Recursively extract readable text from an email message tree."""
    content_type = msg.get_content_type()

    if content_type in {"text/plain", "text/html"}:
        payload = msg.get_payload(decode=True)
        if payload is None:
            raw = msg.get_payload()
            return _strip_html(raw) if content_type == "text/html" and isinstance(raw, str) else str(raw or "")

        charset = msg.get_content_charset() or "utf-8"
        text = payload.decode(charset, errors="replace")
        return _strip_html(text) if content_type == "text/html" else text

    if content_type == "message/rfc822":
        payload = msg.get_payload()
        extracted: list[str] = []

        if isinstance(payload, list):
            for embedded in payload:
                if isinstance(embedded, Message):
                    embedded_text = _extract_message_text(embedded).strip()
                    if embedded_text:
                        extracted.append(embedded_text)
        else:
            raw_bytes = msg.get_payload(decode=True)
            if raw_bytes:
                embedded = email.message_from_bytes(raw_bytes)
                embedded_text = _extract_message_text(embedded).strip()
                if embedded_text:
                    extracted.append(embedded_text)

        return "\n\n".join(extracted)

    if msg.is_multipart():
        parts = msg.get_payload()
        if not isinstance(parts, list):
            return ""

        subtype = msg.get_content_subtype()
        if subtype == "alternative":
            plain_parts: list[str] = []
            html_parts: list[str] = []
            for part in parts:
                part_text = _extract_message_text(part).strip()
                if not part_text:
                    continue
                if part.get_content_type() == "text/plain":
                    plain_parts.append(part_text)
                elif part.get_content_type() == "text/html":
                    html_parts.append(part_text)
                else:
                    plain_parts.append(part_text)

            preferred = plain_parts if plain_parts else html_parts
            return "\n\n".join(preferred)

        extracted = []
        for part in parts:
            part_text = _extract_message_text(part).strip()
            if part_text:
                extracted.append(part_text)
        return "\n\n".join(extracted)

    return ""


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


def _is_metadata_item(item_data: dict) -> bool:
    """Return True if this 'item' is really shipping/contact metadata, not a product."""
    desc = str(item_data.get("description", "")).strip().lower()
    if not desc:
        return False
    # Shipping instructions leaked as line items
    if re.match(r"^ship\b", desc, re.IGNORECASE):
        return True
    # RFQ contact info leaked as line items
    if re.match(r"^rfq\s*:", desc, re.IGNORECASE):
        return True
    # Generic shipping method strings
    shipping_keywords = {"ltl", "prepay", "flatbed", "freight", "ups", "fedex", "common carrier"}
    desc_words = set(desc.replace(",", " ").replace("&", " ").split())
    if desc_words and desc_words.issubset(shipping_keywords | {"ship", "and", "add", "prepay"}):
        return True
    return False


def _parse_items(items_data: list) -> list[ParsedItem]:
    """Parse item data from LLM response into ParsedItem objects."""
    items = []
    for item_data in items_data:
        # Filter out non-product metadata that the LLM incorrectly included
        if _is_metadata_item(item_data):
            continue
        item = ParsedItem(
            product_type=item_data.get("product_type", "sleeve"),
            quantity=int(item_data.get("quantity") or 1),
            description=item_data.get("description", ""),
            diameter=_parse_float(item_data.get("diameter")),
            wall_thickness=_parse_float(item_data.get("wall_thickness")),
            grade=_parse_int(item_data.get("grade")),
            length_ft=_parse_float(item_data.get("length_ft")),
            milling=bool(item_data.get("milling", False)),
            painting=bool(item_data.get("painting", False)),
            notes=item_data.get("notes"),
            sku=(str(item_data.get("sku")).strip() or None) if item_data.get("sku") is not None else None,
        )
        items.append(item)
    return items


def _load_active_sku_prompt_block() -> str:
    """Build prompt context for active catalog SKUs when app/DB context is available."""
    if not has_app_context():
        return ""
    try:
        from app.extensions import db
        from app.models import ProductCatalog

        if not inspect(db.engine).has_table("product_catalog"):
            return ""

        rows = (
            db.session.query(ProductCatalog)
            .filter(ProductCatalog.is_active.is_(True))
            .order_by(ProductCatalog.sku.asc())
            .all()
        )
        if not rows:
            return ""

        lines = "\n".join(f"- {row.sku}: {row.description}" for row in rows)
        return (
            "\n\nCANONICAL SKU LIST (active catalog entries):\n"
            f"{lines}\n"
            'If a line item clearly matches a SKU, include "sku": "<sku>" in its JSON item.\n'
            'If uncertain, set "sku" to null.'
        )
    except Exception:
        return ""


def _parse_system_prompt() -> str:
    return (
        PARSE_SYSTEM_PROMPT
        + '\n\nEach line item may include optional field "sku": string|null.'
        + _load_active_sku_prompt_block()
    )


def _parse_ship_to(ship_to_data: dict | None) -> ShipTo | None:
    """Parse ship_to data from LLM response into ShipTo object."""
    if not ship_to_data:
        return None
    return ShipTo(
        company=ship_to_data.get("company"),
        attention=ship_to_data.get("attention"),
        street=ship_to_data.get("street"),
        city=ship_to_data.get("city"),
        state=ship_to_data.get("state"),
        postal_code=ship_to_data.get("postal_code"),
        country=ship_to_data.get("country", "United States"),
    )


def parse_rfq(eml_path: Path, provider: LLMProvider) -> ParsedRFQ:
    """Parse an RFQ email using an LLM provider.

    Args:
        eml_path: Path to the .eml file
        provider: LLM provider to use for parsing

    Returns:
        ParsedRFQ with extracted data (first quote if multiple detected)

    Note:
        For emails with multiple quote requests, use parse_rfq_multi() instead.
    """
    rfqs = parse_rfq_multi(eml_path, provider)
    return rfqs[0] if rfqs else ParsedRFQ(
        customer_name=None,
        contact_name=None,
        contact_email=None,
        contact_phone=None,
        ship_to=None,
        po_number=None,
        quote_number=None,
        items=[],
    )


def parse_rfq_multi(eml_path: Path, provider: LLMProvider) -> list[ParsedRFQ]:
    """Parse an RFQ email that may contain multiple quote requests.

    Args:
        eml_path: Path to the .eml file
        provider: LLM provider to use for parsing

    Returns:
        List of ParsedRFQ objects, one for each quote request in the email.
        For single-quote emails, returns a list with one element.
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

Return the parsed data as JSON. If the email contains multiple separate quote requests
(with different ship-to addresses or project lines), include each as a separate object
in the "quotes" array."""

    # Call LLM
    result = provider.complete_json(prompt, system=_parse_system_prompt())

    # Extract common fields
    customer_name = result.get("customer_name")
    contact_name = result.get("contact_name")
    contact_email = result.get("contact_email")
    contact_phone = result.get("contact_phone")
    urgency = result.get("urgency", "normal")
    general_notes = result.get("notes")
    confidence = float(result.get("confidence", 0.0))
    message_id = msg.get("Message-ID")
    quote_number = _resolve_quote_number(result.get("quote_number"), subject, body)

    rfqs = []

    # Check for new multi-quote format (quotes array)
    if "quotes" in result and result["quotes"]:
        for quote_data in result["quotes"]:
            ship_to = _parse_ship_to(quote_data.get("ship_to"))
            items = _parse_items(quote_data.get("items", []))
            raw_po = quote_data.get("po_number")
            po_number = _resolve_po_number(raw_po, body)
            quote_notes = quote_data.get("notes")
            project_line = quote_data.get("project_line")

            # Combine general notes with quote-specific notes
            combined_notes = None
            if general_notes and quote_notes:
                combined_notes = f"{general_notes}\n{quote_notes}"
            else:
                combined_notes = general_notes or quote_notes

            rfq = ParsedRFQ(
                customer_name=customer_name,
                contact_name=contact_name,
                contact_email=contact_email,
                contact_phone=contact_phone,
                ship_to=ship_to,
                po_number=po_number,
                quote_number=quote_number,
                items=items,
                urgency=urgency,
                notes=combined_notes,
                confidence=confidence,
                project_line=project_line,
                message_id=message_id,
                subject=subject,
                raw_body=body,
            )
            rfqs.append(rfq)
    else:
        # Legacy format (single ship_to and items at top level)
        ship_to = _parse_ship_to(result.get("ship_to"))
        items = _parse_items(result.get("items", []))
        raw_po = result.get("po_number")
        po_number = _resolve_po_number(raw_po, body)

        rfq = ParsedRFQ(
            customer_name=customer_name,
            contact_name=contact_name,
            contact_email=contact_email,
            contact_phone=contact_phone,
            ship_to=ship_to,
            po_number=po_number,
            quote_number=quote_number,
            items=items,
            urgency=urgency,
            notes=general_notes,
            confidence=confidence,
            message_id=message_id,
            subject=subject,
            raw_body=body,
        )
        rfqs.append(rfq)

    return rfqs


def classify_rfq(subject: str, body: str, provider: LLMProvider) -> bool:
    """Classify if an email is likely an RFQ.

    False negatives are costlier than false positives, so uncertain outcomes
    intentionally bias to True.
    """
    snippet = (body or "")[:DEFAULT_RFQ_CLASSIFY_BODY_CHARS]
    prompt = (
        "Classify this email:\n\n"
        f"Subject: {subject or ''}\n"
        f"Body Snippet:\n{snippet}\n"
    )

    try:
        result = provider.complete_json(prompt, system=CLASSIFY_SYSTEM_PROMPT)
    except Exception:
        # Prefer false positives for MVP.
        return True

    is_rfq = bool(result.get("is_rfq", False))
    confidence = _parse_float(result.get("confidence")) or 0.0
    reason = str(result.get("reason", "")).lower()

    if is_rfq:
        return True

    # Uncertain "not RFQ" classifications get flipped to RFQ.
    if confidence < 0.75:
        return True
    if any(token in reason for token in ("uncertain", "maybe", "possibly", "unclear")):
        return True

    return False


def _is_type_leak(value: str | None) -> bool:
    """Check if a value looks like a type name leak from LLM schema confusion."""
    if value is None:
        return False
    # Common type names that LLMs might return instead of actual values
    type_leaks = {"int", "str", "string", "float", "bool", "boolean", "null", "none"}
    return value.lower().strip() in type_leaks


def _extract_quote_numbers(text: str) -> list[str]:
    """Extract candidate Allan Edwards quote/document numbers from text in order."""
    if not text:
        return []
    return [m.group(0).upper() for m in QUOTE_NUMBER_PATTERN.finditer(text)]


def _extract_quote_number(text: str) -> str | None:
    """Extract the best quote/document number from text."""
    matches = _extract_quote_numbers(text)
    return matches[-1] if matches else None


def _select_best_quote_number(candidates: list[str]) -> str | None:
    """Pick the most likely quote/document number from candidates."""
    if not candidates:
        return None

    prefix_priority = {"INV": 3, "SO": 2, "QUO": 1}
    best = max(
        enumerate(candidates),
        key=lambda item: (
            prefix_priority.get(item[1].split("-", 1)[0], 0),
            item[0],
        ),
    )
    return best[1]


def _resolve_quote_number(raw_qn: Any, subject: str, body: str) -> str | None:
    """Resolve quote number from LLM output with regex fallback."""
    if raw_qn and isinstance(raw_qn, str):
        raw_matches = _extract_quote_numbers(raw_qn.strip())
        best = _select_best_quote_number(raw_matches)
        if best:
            return best

    # Fallback: extract from subject/body and choose best candidate.
    candidates = _extract_quote_numbers(subject) + _extract_quote_numbers(body)
    return _select_best_quote_number(candidates)


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


def _normalize_po_candidate(value: Any) -> str | None:
    """Normalize and validate a PO candidate value."""
    if value is None:
        return None

    po_number = str(value).strip().rstrip(".,;")
    if not po_number or _is_type_leak(po_number):
        return None

    # Reject values that look like signature/name fragments.
    if not re.search(r"\d", po_number):
        return None

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9\-/_.]*", po_number):
        return None

    return po_number


def _resolve_po_number(raw_po: Any, body: str) -> str | None:
    """Resolve PO number from LLM output with safe fallback extraction."""
    po_from_llm = _normalize_po_candidate(raw_po)
    if po_from_llm:
        return po_from_llm

    return _normalize_po_candidate(_extract_po_number(body))


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
