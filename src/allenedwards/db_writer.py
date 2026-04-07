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

from app.extensions import db
from app.models import (
    AuditLog,
    Customer,
    Quote as DBQuote,
    QuoteLineItem as DBQuoteLineItem,
    QuoteStatus,
)
from .email_provider import EmailMessage
from .parser import ParsedRFQ
from .pricing import Quote as PricingQuote

logger = logging.getLogger(__name__)


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
    """Attempt to auto-match an existing Customer by company name or contact email."""
    if rfq.customer_name:
        customer = Customer.query.filter(
            func.lower(Customer.company_name) == rfq.customer_name.lower()
        ).first()
        if customer:
            logger.info("Matched customer %s by company name %r", customer.id, rfq.customer_name)
            return customer

    if rfq.contact_email:
        from app.models import Contact
        contact = Contact.query.filter(
            func.lower(Contact.email) == rfq.contact_email.lower()
        ).first()
        if contact:
            logger.info("Matched customer %s via contact email %r", contact.customer_id, rfq.contact_email)
            return contact.customer

    return None


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
