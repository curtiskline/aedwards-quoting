"""Operator re-parse of a quote's inbound RFQ source text (T495).

Chip's recovery path when the parser misread an email: the quote detail page
shows the exact text the parser read (stored body plus text re-extracted from
the stored attachments), lets him correct it, and re-runs parse + price into
the SAME quote. The original QuoteSourceSnapshot is never modified; every
attempt is recorded as an append-only QuoteReparseAttempt row.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from email.message import EmailMessage as MimeEmailMessage

from .extensions import db
from .models import (
    AuditLog,
    FailedIntake,
    ProcessedInboundEmail,
    Quote,
    QuoteReparseAttempt,
    QuoteStatus,
    User,
)

logger = logging.getLogger(__name__)

# Parser self-reported confidence below this is surfaced as a warning banner on
# the quote page, pointing the reviewer at the original email panel.
LOW_PARSE_CONFIDENCE = 0.7

# Statuses whose lifecycle is complete; their content must not be rewritten.
_REPARSE_LOCKED_STATUSES = frozenset(
    {QuoteStatus.SENT, QuoteStatus.REPLACED, QuoteStatus.ARCHIVED}
)


@dataclass
class ReparseOutcome:
    ok: bool
    # Plain-language outcome shown to the operator on the quote page.
    message: str
    item_count: int = 0
    # Set when the attempt created a new quote (failed-intake retry, T505).
    quote_id: int | None = None


def quote_allows_reparse(quote: Quote) -> bool:
    """Whether this quote's source can still be re-read into it."""
    return (
        quote.source_snapshot is not None
        and quote.deleted_at is None
        and quote.status not in _REPARSE_LOCKED_STATUSES
    )


def _attachment_text_blocks(quote: Quote) -> list[str]:
    """Re-extract parser-visible text from the stored source attachments.

    Rebuilds each stored attachment as a MIME part and runs it through the same
    extractors intake used, so the panel shows (and a re-parse reads) the same
    attachment text the original parse saw. Extraction is deterministic, so
    this is reproducible from the immutable stored bytes.
    """
    from allenedwards.parser import _extract_message_text

    blocks: list[str] = []
    for attachment in quote.attachments:
        if not attachment.is_stored:
            blocks.append(
                f"--- Attachment: {attachment.filename} ---\n"
                "[File was too large to store, so its contents are not shown here.]\n"
                f"--- End Attachment: {attachment.filename} ---"
            )
            continue
        container = MimeEmailMessage()
        content_type = attachment.content_type or "application/octet-stream"
        maintype, _, subtype = content_type.partition("/")
        if not subtype:
            maintype, subtype = "application", "octet-stream"
        try:
            container.add_attachment(
                attachment.content_bytes,
                maintype=maintype,
                subtype=subtype,
                filename=attachment.filename,
            )
            part = container.get_payload()[0]
            text = _extract_message_text(part).strip()
        except Exception:
            logger.exception(
                "Could not re-extract text from attachment %s on quote %s",
                attachment.id,
                attachment.quote_id,
            )
            text = (
                f"--- Attachment: {attachment.filename} ---\n"
                "[Could not read this file.]\n"
                f"--- End Attachment: {attachment.filename} ---"
            )
        if text:
            blocks.append(text)
    return blocks


def parser_input_text(quote: Quote) -> str:
    """The full text the parser reads for this quote: body + attachment text."""
    snapshot = quote.source_snapshot
    if snapshot is None:
        return ""
    parts = [snapshot.normalized_body_text.strip()]
    parts.extend(_attachment_text_blocks(quote))
    return "\n\n".join(part for part in parts if part)


def latest_parse_confidence(quote: Quote) -> float | None:
    """Parser self-reported confidence of the quote's current interpretation."""
    for attempt in reversed(quote.reparse_attempts):
        if attempt.succeeded and attempt.parsed_rfq_json is not None:
            value = attempt.parsed_rfq_json.get("confidence")
            return float(value) if value is not None else None
    snapshot = quote.source_snapshot
    if snapshot is None:
        return None
    value = snapshot.parsed_rfq_json.get("confidence")
    return float(value) if value is not None else None


def source_panel_context(quote: Quote) -> dict | None:
    """Template context for the Original Email panel, or None to hide it."""
    if quote.source_snapshot is None:
        return None
    attempts = quote.reparse_attempts
    last_attempt = attempts[-1] if attempts else None
    # Prefill with the text of the last attempt so corrections survive a
    # failed try; otherwise show the regenerated original parser input.
    input_text = last_attempt.input_text if last_attempt else parser_input_text(quote)
    confidence = latest_parse_confidence(quote)
    return {
        "input_text": input_text,
        "low_confidence": confidence is not None and confidence < LOW_PARSE_CONFIDENCE,
        "last_attempt": last_attempt,
        "last_attempt_user": (
            db.session.get(User, last_attempt.created_by)
            if last_attempt and last_attempt.created_by
            else None
        ),
        "allows_reparse": quote_allows_reparse(quote),
    }


def _record_attempt(
    quote: Quote,
    input_text: str,
    input_edited: bool,
    user: User | None,
    *,
    succeeded: bool,
    parsed_rfq_json: dict | None = None,
    error: str | None = None,
) -> None:
    db.session.add(
        QuoteReparseAttempt(
            quote_id=quote.id,
            input_text=input_text,
            input_edited=input_edited,
            succeeded=succeeded,
            parsed_rfq_json=parsed_rfq_json,
            error=error,
            created_by=user.id if user else None,
        )
    )


def reparse_quote(
    quote: Quote,
    input_text: str,
    user: User | None,
    provider=None,
) -> ReparseOutcome:
    """Re-run parse + price on ``input_text`` and rewrite this quote in place.

    Replaces the quote's line items and parsed header fields with the fresh
    interpretation — exactly what intake would have produced had this text
    arrived in the original email. Commits on both success and failure so the
    attempt record survives either way. Never auto-sends: a re-parse is part
    of a human editing workflow (same rule as revise/duplicate).
    """
    from allenedwards.db_writer import (
        _bill_to_dict,
        _create_customer_from_rfq,
        _ensure_contact,
        _match_customer,
        _notes_internal_with_bill_to,
        _ship_to_dict,
        build_db_line_item,
    )
    from allenedwards.parser import parse_rfq_text
    from allenedwards.pricing import generate_quote

    if not quote_allows_reparse(quote):
        return ReparseOutcome(
            ok=False,
            message="This quote can no longer be re-read from the email.",
        )

    original_text = parser_input_text(quote)
    input_edited = input_text.strip() != original_text.strip()

    if provider is None:
        from allenedwards.cli import get_provider, load_environment

        load_environment()
        provider = get_provider()

    from_header = (
        f"{quote.sender_name} <{quote.sender_email}>"
        if quote.sender_name and quote.sender_email
        else (quote.sender_email or "")
    )

    try:
        rfqs = parse_rfq_text(
            input_text,
            provider,
            from_header=from_header,
            subject=quote.subject or "",
        )
        rfqs = [rfq for rfq in rfqs if rfq.items]
        if not rfqs:
            raise ValueError("no line items could be read from the text")
        rfq = rfqs[0]
        priced = generate_quote(rfq, quote.quote_number)
    except Exception as error:
        db.session.rollback()
        logger.exception("Re-parse failed for quote %s", quote.id)
        _record_attempt(
            quote, input_text, input_edited, user,
            succeeded=False, error=f"{type(error).__name__}: {error}",
        )
        db.session.commit()
        return ReparseOutcome(
            ok=False,
            message=(
                "The system could not read any quote items from this text. "
                "Nothing on the quote was changed. You can fix the text and try again."
            ),
        )

    parsed_rfq_json = asdict(rfq)
    parsed_rfq_json.pop("raw_body", None)

    # Header fields, mirroring intake's write_quote_to_db.
    quote.customer_name_raw = rfq.customer_name
    quote.contact_name = rfq.contact_name
    quote.contact_email = rfq.contact_email
    quote.contact_phone = rfq.contact_phone
    quote.po_number = rfq.po_number
    quote.ship_to_json = _ship_to_dict(rfq)
    quote.bill_to_json = _bill_to_dict(rfq)
    quote.notes_internal = _notes_internal_with_bill_to(rfq)
    quote.project_name = priced.project_line

    # Customer link, mirroring intake: re-match on the fresh interpretation;
    # keep the current link only when the new parse gives nothing better.
    customer = _match_customer(rfq)
    if customer is not None:
        _ensure_contact(customer, rfq)
        quote.customer_id = customer.id
    elif rfq.customer_name:
        quote.customer_id = _create_customer_from_rfq(rfq).id

    # Full line-item replacement (the confirm dialog warned about hand edits).
    quote.line_items.clear()
    db.session.flush()
    material_lines = [li for li in priced.line_items if not li.is_note]
    for li in material_lines:
        db.session.add(build_db_line_item(quote.id, li))

    # A re-read invalidates any prior human approval of the old content.
    quote.ready_override_at = None
    quote.ready_override_by = None
    quote.ready_override_fingerprint = None

    has_unpriced = any(li.unit_price <= 0 or li.total <= 0 for li in material_lines)
    if priced.subtotal == 0 or has_unpriced:
        quote.status = QuoteStatus.NEEDS_PRICING
    elif quote.status == QuoteStatus.NEEDS_PRICING or quote.status == QuoteStatus.READY:
        quote.status = QuoteStatus.IN_REVIEW

    _record_attempt(
        quote, input_text, input_edited, user,
        succeeded=True, parsed_rfq_json=parsed_rfq_json,
    )
    db.session.add(
        AuditLog(
            quote_id=quote.id,
            action="reparsed_from_source",
            user_id=user.id if user else None,
            details={
                "input_edited": input_edited,
                "item_count": len(material_lines),
                "rfqs_in_text": len(rfqs),
            },
        )
    )

    from .confidence import sync_quote_confidence

    db.session.flush()
    sync_quote_confidence(quote)
    db.session.commit()

    message = f"Done. The system read {len(material_lines)} item{'s' if len(material_lines) != 1 else ''} from the text."
    if len(rfqs) > 1:
        message += (
            f" The text looked like {len(rfqs)} separate quote requests; "
            "only the first one was put on this quote."
        )
    return ReparseOutcome(ok=True, message=message, item_count=len(material_lines))


# --- Failed-intake retry (T505) -------------------------------------------
#
# A FailedIntake is an email that never became a quote, so a retry cannot
# rewrite anything in place: a successful one creates a brand-new quote through
# the same db_writer path intake uses, complete with its own immutable
# QuoteSourceSnapshot. Attempts are recorded on the same append-only
# quote_reparse_attempt table, keyed by failed_intake_id.


class _IntakeSourceMessage:
    """Duck-typed stand-in for the provider message write_quote_to_db expects.

    Rebuilt from the fields FailedIntake persisted at quarantine time; only the
    attributes db_writer actually reads are provided.
    """

    def __init__(self, intake: FailedIntake):
        self.id = intake.source_email_id
        self.sender_name = intake.sender_name
        self.sender_email = intake.sender_email
        self.subject = intake.subject
        self.body_content = intake.original_body or intake.normalized_body_text or ""
        self.body_content_type = intake.original_body_content_type or "text"


def failed_intake_allows_reparse(intake: FailedIntake) -> bool:
    """Whether this quarantined email's text can still be retried into a quote.

    Rows from before T505 (and rare capture failures) stored no body text, so
    there is nothing to retry from; resolved rows are done — retrying one again
    (stale tab, double submit) would create duplicate quotes.
    """
    return intake.resolved_at is None and intake.normalized_body_text is not None


def failed_intake_panel_context(intake: FailedIntake) -> dict:
    """Template context for the failed-intake retry panel."""
    attempts = intake.reparse_attempts
    last_attempt = attempts[-1] if attempts else None
    # Prefill with the text of the last attempt so corrections survive a
    # failed try; otherwise show the stored normalized body.
    input_text = (
        last_attempt.input_text if last_attempt else (intake.normalized_body_text or "")
    )
    return {
        "input_text": input_text,
        "last_attempt": last_attempt,
        "last_attempt_user": (
            db.session.get(User, last_attempt.created_by)
            if last_attempt and last_attempt.created_by
            else None
        ),
        "allows_reparse": failed_intake_allows_reparse(intake),
    }


def _record_intake_attempt(
    intake: FailedIntake,
    input_text: str,
    input_edited: bool,
    user: User | None,
    *,
    succeeded: bool,
    quote_id: int | None = None,
    parsed_rfq_json: dict | None = None,
    error: str | None = None,
) -> None:
    db.session.add(
        QuoteReparseAttempt(
            quote_id=quote_id,
            failed_intake_id=intake.id,
            input_text=input_text,
            input_edited=input_edited,
            succeeded=succeeded,
            parsed_rfq_json=parsed_rfq_json,
            error=error,
            created_by=user.id if user else None,
        )
    )


def reparse_failed_intake(
    intake: FailedIntake,
    input_text: str,
    user: User | None,
    provider=None,
) -> ReparseOutcome:
    """Parse ``input_text`` and, on success, create a new quote from it.

    The new quote is written through the same db_writer path intake uses, so it
    gets its own QuoteSourceSnapshot (original provider body from the intake
    row, normalized text = exactly what was parsed here) and a message-level
    idempotency claim, which stops the monitor from ever re-processing the same
    email into a second quote. The intake row is marked resolved on success and
    left outstanding on failure. Never auto-sends.
    """
    from allenedwards.db_writer import write_quote_to_db
    from allenedwards.parser import parse_rfq_text
    from allenedwards.pricing import generate_quote

    from .quote_numbers import generate_quote_number

    if not failed_intake_allows_reparse(intake):
        return ReparseOutcome(
            ok=False,
            message=(
                "This email cannot be retried: it was already handled, or it "
                "arrived before the system started keeping the email text."
            ),
        )

    original_text = (intake.normalized_body_text or "").strip()
    input_edited = input_text.strip() != original_text

    if provider is None:
        from allenedwards.cli import get_provider, load_environment

        load_environment()
        provider = get_provider()

    from_header = (
        f"{intake.sender_name} <{intake.sender_email}>"
        if intake.sender_name and intake.sender_email
        else (intake.sender_email or "")
    )

    try:
        rfqs = parse_rfq_text(
            input_text,
            provider,
            from_header=from_header,
            subject=intake.subject or "",
        )
        rfqs = [rfq for rfq in rfqs if rfq.items]
        if not rfqs:
            raise ValueError("no line items could be read from the text")
        rfq = rfqs[0]
        quote_number = generate_quote_number()
        priced = generate_quote(rfq, quote_number)
    except Exception as error:
        db.session.rollback()
        logger.exception("Retry parse failed for failed intake %s", intake.id)
        _record_intake_attempt(
            intake, input_text, input_edited, user,
            succeeded=False, error=f"{type(error).__name__}: {error}",
        )
        db.session.commit()
        return ReparseOutcome(
            ok=False,
            message=(
                "The system could not read any quote items from this text. "
                "No quote was made. You can fix the text and try again."
            ),
        )

    quote = write_quote_to_db(
        _IntakeSourceMessage(intake),
        rfq,
        priced,
        quote_number,
        attachments=None,
        source_body_text=input_text,
        claim_source_email=False,
        commit=False,
    )

    # Claim the source email if intake never managed to (transient failures
    # leave no claim), so a later monitor retry cannot create a second quote.
    if intake.source_email_id and not db.session.query(
        ProcessedInboundEmail.query.filter_by(
            source_email_id=intake.source_email_id
        ).exists()
    ).scalar():
        db.session.add(ProcessedInboundEmail(source_email_id=intake.source_email_id))

    parsed_rfq_json = asdict(rfq)
    parsed_rfq_json.pop("raw_body", None)
    _record_intake_attempt(
        intake, input_text, input_edited, user,
        succeeded=True, quote_id=quote.id, parsed_rfq_json=parsed_rfq_json,
    )
    db.session.add(
        AuditLog(
            quote_id=quote.id,
            action="created_from_failed_intake",
            user_id=user.id if user else None,
            details={
                "failed_intake_id": intake.id,
                "input_edited": input_edited,
                "item_count": len([li for li in priced.line_items if not li.is_note]),
                "rfqs_in_text": len(rfqs),
            },
        )
    )
    intake.resolved_at = datetime.utcnow()
    db.session.commit()

    material_count = len([li for li in priced.line_items if not li.is_note])
    message = (
        f"Done. Quote {quote.quote_number} was created with "
        f"{material_count} item{'s' if material_count != 1 else ''}."
    )
    if len(rfqs) > 1:
        message += (
            f" The text looked like {len(rfqs)} separate quote requests; "
            "only the first one was turned into a quote."
        )
    return ReparseOutcome(
        ok=True, message=message, item_count=material_count, quote_id=quote.id
    )
