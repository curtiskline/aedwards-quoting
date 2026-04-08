"""Write parsed/priced RFQ data into the web-app database.

This module bridges the monitor pipeline (which produces pricing.Quote dataclass
instances) into the Flask/SQLAlchemy database (app.models.Quote ORM rows).
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal

from flask import Flask
from sqlalchemy import func

import re

from app.extensions import db
from app.models import (
    AuditLog,
    Contact,
    Customer,
    Quote as DBQuote,
    QuoteLineItem as DBQuoteLineItem,
    QuoteStatus,
    ShipToAddress,
)
from .email_provider import EmailMessage
from .parser import ParsedRFQ
from .pricing import Quote as PricingQuote

logger = logging.getLogger(__name__)

# Legal suffixes to strip during company name normalization
_LEGAL_SUFFIXES = re.compile(
    r"\b(inc|incorporated|llc|l\.l\.c|corp|corporation|ltd|limited|co|company|lp|llp|pllc|plc|gmbh|sa|srl)\.?\s*$",
    re.IGNORECASE,
)


def _normalize_company_name(name: str) -> str:
    """Normalize a company name for dedup comparison.

    - Lowercase
    - Strip common legal suffixes (Inc, LLC, Corp, etc.) — repeated to catch stacked suffixes
    - Strip punctuation and collapse whitespace
    """
    s = name.lower().strip()
    # Repeatedly strip trailing legal suffixes (handles "Co, Inc." → strip Inc → strip Co)
    for _ in range(3):
        s = re.sub(r"[,.\s]+$", "", s)  # trailing punctuation
        prev = s
        s = _LEGAL_SUFFIXES.sub("", s).strip()
        if s == prev:
            break
    # Remove remaining punctuation (except hyphens inside words)
    s = re.sub(r"[^\w\s-]", "", s)
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _generate_fiscal_quote_number() -> str:
    """Generate sequential quote number with fiscal-year prefix (e.g., 126-001).

    Fiscal year prefix: last digit of century + two-digit year (2026 → 126).
    Sequence resets each fiscal year.
    """
    now = datetime.utcnow()
    year = now.year
    prefix = f"1{year % 100}"  # 2026 → "126"

    # Find the highest existing quote number with this prefix
    pattern = f"{prefix}-%"
    result = db.session.query(func.max(DBQuote.quote_number)).filter(
        DBQuote.quote_number.like(pattern)
    ).scalar()

    if result:
        try:
            seq = int(result.split("-")[-1]) + 1
        except (ValueError, IndexError):
            seq = 1
    else:
        seq = 1

    return f"{prefix}-{seq:03d}"


def _match_customer(rfq: ParsedRFQ) -> Customer | None:
    """Attempt to auto-match an existing Customer by normalized company name or contact email."""
    if rfq.customer_name:
        norm_name = _normalize_company_name(rfq.customer_name)
        # Check all customers with normalized comparison
        for customer in Customer.query.all():
            if _normalize_company_name(customer.company_name) == norm_name:
                logger.info("Matched customer %s by normalized company name %r", customer.id, rfq.customer_name)
                return customer

    if rfq.contact_email:
        contact = Contact.query.filter(
            func.lower(Contact.email) == rfq.contact_email.lower()
        ).first()
        if contact:
            logger.info("Matched customer %s via contact email %r", contact.customer_id, rfq.contact_email)
            return contact.customer

    return None


def _ensure_contact(customer: Customer, rfq: ParsedRFQ) -> None:
    """Add a contact to an existing customer if the email is new."""
    if not rfq.contact_email:
        return
    existing = Contact.query.filter(
        Contact.customer_id == customer.id,
        func.lower(Contact.email) == rfq.contact_email.lower(),
    ).first()
    if existing:
        return
    contact = Contact(
        customer_id=customer.id,
        name=rfq.contact_name or rfq.contact_email,
        email=rfq.contact_email,
        phone=rfq.contact_phone,
    )
    db.session.add(contact)
    logger.info("Added new contact %r to customer %s", rfq.contact_email, customer.id)


def _create_customer_from_rfq(rfq: ParsedRFQ) -> Customer:
    """Create a new Customer (with optional Contact and Address) from RFQ data."""
    customer = Customer(
        company_name=rfq.customer_name,
        discount_pct=0,
    )
    db.session.add(customer)
    db.session.flush()  # get customer.id

    if rfq.contact_name or rfq.contact_email:
        contact = Contact(
            customer_id=customer.id,
            name=rfq.contact_name or rfq.contact_email,
            email=rfq.contact_email or "",
            phone=rfq.contact_phone,
        )
        db.session.add(contact)

    if rfq.ship_to and rfq.ship_to.city and rfq.ship_to.state:
        addr = ShipToAddress(
            customer_id=customer.id,
            address_line1=rfq.ship_to.street or "",
            city=rfq.ship_to.city,
            state=rfq.ship_to.state,
            postal_code=rfq.ship_to.postal_code or "",
            is_default=True,
        )
        db.session.add(addr)

    logger.info("Created new customer %s (%r) from RFQ", customer.id, rfq.customer_name)
    return customer


def _ship_to_dict(rfq: ParsedRFQ) -> dict | None:
    """Serialize ShipTo dataclass to JSON-safe dict."""
    if not rfq.ship_to:
        return None
    return asdict(rfq.ship_to)


def write_quote_to_db(
    msg: EmailMessage,
    rfq: ParsedRFQ,
    priced_quote: PricingQuote,
    quote_number: str,
) -> DBQuote:
    """Write a priced quote into the database.

    Returns the created DBQuote ORM instance (already committed).
    """
    customer = _match_customer(rfq)

    if customer:
        # Existing customer — add any new contact info
        _ensure_contact(customer, rfq)
    elif rfq.customer_name:
        # No match but we have a name — create a new customer
        customer = _create_customer_from_rfq(rfq)

    status = QuoteStatus.NEW
    if priced_quote.subtotal == 0:
        status = QuoteStatus.NEEDS_PRICING

    db_quote = DBQuote(
        quote_number=quote_number,
        customer_id=customer.id if customer else None,
        status=status,
        project_name=priced_quote.project_line,
        source_email_id=msg.id,
        sender_email=msg.sender_email,
        sender_name=msg.sender_name,
        subject=msg.subject,
        customer_name_raw=rfq.customer_name,
        contact_name=rfq.contact_name,
        contact_email=rfq.contact_email,
        contact_phone=rfq.contact_phone,
        po_number=rfq.po_number,
        ship_to_json=_ship_to_dict(rfq),
        notes_internal=rfq.notes,
    )
    db.session.add(db_quote)
    db.session.flush()  # get db_quote.id

    for li in priced_quote.line_items:
        if li.is_note:
            continue  # skip note-only rows

        specs = {}
        if li.weight_per_ft is not None:
            specs["weight_per_ft"] = str(li.weight_per_ft)
        if li.price_per_lb is not None:
            specs["price_per_lb"] = str(li.price_per_lb)
        if li.notes:
            specs["notes"] = li.notes

        db_line = DBQuoteLineItem(
            quote_id=db_quote.id,
            product_type=li.product_type,
            description=li.description,
            quantity=float(li.quantity),
            unit_price=float(li.unit_price),
            line_total=float(li.total),
            specs_json=specs or None,
            part_number=li.part_number or None,
            sort_order=li.sort_order,
        )
        db.session.add(db_line)

    audit = AuditLog(
        quote_id=db_quote.id,
        action="created_from_email",
        details={
            "source_email_id": msg.id,
            "sender": msg.sender_email,
            "subject": msg.subject,
            "customer_id": customer.id if customer else None,
            "customer_matched": customer is not None,
        },
    )
    db.session.add(audit)

    db.session.commit()
    logger.info(
        "Wrote quote %s (id=%s, status=%s, customer_id=%s) with %d line items",
        quote_number,
        db_quote.id,
        status.value,
        db_quote.customer_id,
        len(db_quote.line_items),
    )
    return db_quote
