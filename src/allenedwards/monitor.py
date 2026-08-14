"""Polling monitor that turns inbox RFQs into quote drafts."""

from __future__ import annotations

import json
import logging
import os
import signal
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.exc import IntegrityError

from .email_provider import EmailMessage, EmailProvider
from .outlook import OutlookAttachment, OutlookClient, OutlookMessage
from .parser import ParsedRFQ, classify_rfq, parse_rfq_multi
from .pdf_generator import generate_quote_pdf
from .pricing import Quote, generate_quote
from .providers.base import LLMProvider, LLMResponseTruncated

logger = logging.getLogger(__name__)


class ProcessedState:
    """Tracks processed message IDs and a high-water mark on disk."""

    def __init__(self, path: Path):
        self.path = path
        self._ids: set[str] = set()
        self.last_seen_datetime: str | None = None
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return

        try:
            data = json.loads(self.path.read_text())
        except Exception:
            logger.warning("Could not parse state file %s; starting fresh", self.path)
            return

        if isinstance(data, dict):
            if isinstance(data.get("processed_ids"), list):
                self._ids = {str(x) for x in data["processed_ids"]}
            self.last_seen_datetime = data.get("last_seen_datetime")

    def contains(self, message_id: str) -> bool:
        return message_id in self._ids

    def add(self, message_id: str) -> None:
        self._ids.add(message_id)
        self.save()

    def advance_watermark(self, received_datetime: str) -> None:
        """Move the high-water mark forward if *received_datetime* is newer."""
        received_at = _parse_datetime(received_datetime)
        if received_at is None:
            logger.warning("Ignoring invalid receivedDateTime %r", received_datetime)
            return

        if not self.last_seen_datetime:
            self.last_seen_datetime = _format_datetime(received_at)
            return

        last_seen_at = _parse_datetime(self.last_seen_datetime)
        if last_seen_at is None:
            logger.warning(
                "State file %s has invalid last_seen_datetime %r; replacing it",
                self.path,
                self.last_seen_datetime,
            )
            self.last_seen_datetime = _format_datetime(received_at)
            return

        if received_at > last_seen_at:
            self.last_seen_datetime = _format_datetime(received_at)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {"processed_ids": sorted(self._ids)}
        if self.last_seen_datetime:
            payload["last_seen_datetime"] = self.last_seen_datetime
        # A monitor can be terminated at any point.  Write and fsync a temporary
        # file before replacing the old state, so a SIGKILL cannot leave an
        # unreadable partial JSON file that would cause the inbox to replay.
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            delete=False,
        ) as state_file:
            temporary_path = Path(state_file.name)
            state_file.write(json.dumps(payload, indent=2))
            state_file.flush()
            os.fsync(state_file.fileno())
        os.replace(temporary_path, self.path)


class InboxMonitor:
    """Monitors unread inbox messages and creates quote drafts for RFQs."""

    def __init__(
        self,
        *,
        email_client: EmailProvider | None = None,
        outlook: OutlookClient | None = None,
        provider: LLMProvider,
        poll_interval_seconds: int,
        state_path: Path,
        output_dir: Path,
        quote_email_cc: str | None = None,
        processed_folder_name: str | None = None,
        enable_db_writes: bool = False,
        enable_outlook_drafts: bool = True,
        enable_failure_ack: bool = False,
        mailbox_address: str | None = None,
        ack_skip_domains: list[str] | None = None,
        flask_app: Any | None = None,
    ):
        if email_client is None and outlook is None:
            raise ValueError("InboxMonitor requires email_client (or legacy outlook parameter).")

        self.email_client = email_client or outlook
        self.provider = provider
        self.poll_interval_seconds = poll_interval_seconds
        self.state = ProcessedState(state_path)
        self.output_dir = output_dir
        self.quote_email_cc = quote_email_cc
        self.processed_folder_name = processed_folder_name
        self._processed_folder_id: str | None = None
        self._running = True
        self._shutdown_event = threading.Event()
        self.enable_db_writes = enable_db_writes
        self.enable_outlook_drafts = enable_outlook_drafts
        # Sending an acknowledgment back to the sender is opt-in and off by
        # default: it is an outbound email to an external party and must not be
        # enabled without deliberate configuration (loop and internal/test
        # sender guards live in _acknowledge_failed_intake).
        self.enable_failure_ack = enable_failure_ack
        self._mailbox_address = (mailbox_address or "").strip().lower() or None
        self._ack_skip_domains = {d.strip().lower() for d in (ack_skip_domains or []) if d.strip()}
        self._flask_app = flask_app

    def _shutdown_signal(self, signum: int, _frame: Any) -> None:
        logger.info("Received signal %s; shutting down", signum)
        self._running = False
        self._shutdown_event.set()

    def install_signal_handlers(self) -> None:
        signal.signal(signal.SIGINT, self._shutdown_signal)
        signal.signal(signal.SIGTERM, self._shutdown_signal)

    def run_forever(self) -> None:
        self.install_signal_handlers()
        logger.info("Starting monitor loop with %ss interval", self.poll_interval_seconds)

        while self._running:
            self.run_once()
            if not self._running:
                break
            self._shutdown_event.wait(self.poll_interval_seconds)

    def run_once(self) -> int:
        messages = self.email_client.fetch_messages(
            since=self.state.last_seen_datetime
        )
        logger.info("Fetched %s inbox messages (since=%s)", len(messages), self.state.last_seen_datetime)
        processed_count = 0

        for msg in messages:
            message_id = getattr(msg, "id", "<missing-id>") or "<missing-id>"
            try:
                if msg.received_datetime:
                    self.state.advance_watermark(msg.received_datetime)

                if self.state.contains(message_id):
                    if self._has_source_email_claim(message_id):
                        logger.debug("Skipping already processed message %s", message_id)
                        continue
                    logger.warning(
                        "Retrying message %s: state file says processed but its DB claim is absent",
                        message_id,
                    )

                # Persist the local state before downstream side effects.  DB
                # writes additionally use a claim committed atomically with
                # their quote rows; a state-only entry is retried above.
                self.state.add(message_id)

                handled = self._process_message(msg)
                if handled:
                    processed_count += 1
            except Exception as error:
                logger.exception(
                    "Failed processing message %s; quarantining to avoid retry loop "
                    "(subject=%r, sender=%r, received=%r)",
                    message_id,
                    getattr(msg, "subject", None),
                    getattr(msg, "sender_email", None),
                    getattr(msg, "received_datetime", None),
                )
                # An RFQ must never disappear silently. Record a visible
                # failed-intake row (and optionally acknowledge the sender) so
                # staff can pick it up manually. Recording must not itself raise
                # — a bookkeeping failure cannot be allowed to crash the loop.
                self._record_failed_intake(msg, error)
        if messages:
            self.state.save()

        return processed_count

    def _process_message(self, msg: EmailMessage) -> bool:
        if self._flask_app:
            with self._flask_app.app_context():
                return self._process_message_inner(msg)
        return self._process_message_inner(msg)

    def _process_message_inner(self, msg: EmailMessage) -> bool:
        body_text = _normalize_body(msg.body_content, msg.body_preview)
        is_rfq, reason = classify_rfq(msg.subject, body_text, self.provider)
        if not is_rfq:
            logger.info("Message %s classified as non-RFQ: %s", msg.id, reason)
            if self.enable_db_writes:
                self._write_rejected_email(msg, reason)
            self._finalize_message(msg.id)
            return False

        attachments: list[OutlookAttachment] = []
        if msg.has_attachments and hasattr(self.email_client, "get_attachments"):
            try:
                attachments = self.email_client.get_attachments(msg.id)
                logger.info("Fetched %d attachments for message %s", len(attachments), msg.id)
            except Exception:
                logger.exception("Failed fetching attachments for message %s", msg.id)

        rfqs = _parse_message_to_rfqs(msg, body_text, self.provider, attachments)
        rfqs = [rfq for rfq in rfqs if rfq.items]
        if not rfqs:
            logger.info("Message %s parsed but produced no line items; skipping", msg.id)
            self._finalize_message(msg.id)
            return False

        if self.enable_db_writes:
            base_quote_number = self._generate_db_quote_number()
        else:
            base_quote_number = _generate_quote_number()

        quotes = []
        for idx, rfq in enumerate(rfqs):
            quote_number = base_quote_number if len(rfqs) == 1 else f"{base_quote_number}-{idx + 1:02d}"
            quotes.append((rfq, generate_quote(rfq, quote_number), quote_number))

        # --- DB write path ---
        if self.enable_db_writes:
            from .db_writer import InboundEmailAlreadyProcessed

            try:
                self._write_to_db(msg, quotes, attachments)
            except InboundEmailAlreadyProcessed:
                logger.warning("Skipping replayed message %s with an existing DB claim", msg.id)
                self._finalize_message(msg.id)
                return False

        for rfq, quote, _quote_number in quotes:
            # --- Outlook draft path ---
            if self.enable_outlook_drafts:
                if not hasattr(self.email_client, "create_draft"):
                    logger.warning(
                        "Draft creation is enabled but provider %s does not support drafts; skipping",
                        type(self.email_client).__name__,
                    )
                    continue

                to_email = rfq.contact_email or msg.sender_email
                if not to_email:
                    raise RuntimeError(f"No recipient email available for message {msg.id}")

                if quote.subtotal == 0:
                    draft_id = self._create_review_draft(msg, rfq, quote, to_email)
                    logger.warning(
                        "Created REVIEW draft %s for message %s ($0 quote — needs manual pricing)",
                        draft_id or "<unknown>", msg.id,
                    )
                else:
                    pdf_name, pdf_bytes = self._build_quote_pdf(quote)
                    subject = f"Quote {quote.quote_number}"
                    if quote.project_line:
                        subject = f"{subject} - {quote.project_line}"

                    body = _build_draft_body(quote)
                    draft_id = self.email_client.create_draft(
                        to_email=to_email,
                        subject=subject,
                        body_text=body,
                        attachments=[(pdf_name, pdf_bytes)],
                        cc_email=self.quote_email_cc,
                    )
                    logger.info("Created draft %s for message %s", draft_id or "<unknown>", msg.id)

        self._finalize_message(msg.id)
        return True

    def _write_to_db(
        self,
        msg: EmailMessage,
        quotes: list[tuple[ParsedRFQ, Quote, str]],
        attachments: list[OutlookAttachment],
    ) -> None:
        """Atomically claim one message and write all of its quotes."""
        from app.extensions import db
        from .db_writer import write_quote_to_db

        if not self._flask_app:
            raise RuntimeError("DB writes enabled but no Flask app provided")

        with self._flask_app.app_context():
            try:
                for index, (rfq, quote, quote_number) in enumerate(quotes):
                    write_quote_to_db(
                        msg,
                        rfq,
                        quote,
                        quote_number,
                        attachments=attachments,
                        claim_source_email=index == 0 and bool(msg.id),
                        commit=False,
                    )
                db.session.commit()
            except IntegrityError as error:
                db.session.rollback()
                # A quote_number UNIQUE violation means the generator handed out
                # a number that already exists (the 2026-08-13 outage). The
                # generator fix should prevent this, but surface it unambiguously
                # rather than as a bare IntegrityError so it is not mistaken for a
                # parsing/decoding failure. The batch is not retried: numbers are
                # assigned upstream (pricing/PDF), so a fresh email re-triggers.
                if "quote.quote_number" in str(error.orig):
                    numbers = [qn for _, _, qn in quotes]
                    logger.error(
                        "Quote-number collision writing message %s (numbers=%s): %s",
                        msg.id,
                        numbers,
                        error.orig,
                    )
                raise
            except Exception:
                db.session.rollback()
                raise

    def _has_source_email_claim(self, message_id: str) -> bool:
        """Whether a state-file entry has a matching committed DB claim."""
        if not self.enable_db_writes or not self._flask_app or message_id == "<missing-id>":
            return True

        from app.extensions import db
        from app.models import ProcessedInboundEmail

        with self._flask_app.app_context():
            return db.session.query(ProcessedInboundEmail.id).filter_by(source_email_id=message_id).first() is not None

    def _write_rejected_email(self, msg: EmailMessage, reason: str | None) -> None:
        """Write a rejected email record to the database."""
        from datetime import datetime

        from app.extensions import db
        from app.models import RejectedEmail

        if not self._flask_app:
            return

        with self._flask_app.app_context():
            received_at = None
            if msg.received_datetime:
                try:
                    received_at = datetime.fromisoformat(msg.received_datetime.replace("Z", "+00:00"))
                except ValueError:
                    received_at = datetime.utcnow()
            else:
                received_at = datetime.utcnow()

            record = RejectedEmail(
                received_at=received_at,
                sender_name=msg.sender_name,
                sender_email=msg.sender_email,
                subject=msg.subject,
                classifier_reason=reason,
            )
            db.session.add(record)
            db.session.commit()

    def _record_failed_intake(self, msg: EmailMessage, error: Exception) -> None:
        """Persist a visible record of an RFQ that could not be processed.

        Guarded so a bookkeeping failure never crashes the polling loop: any
        exception here is logged and swallowed. When DB writes are disabled the
        quarantine log line remains the only record, but the loud ``logger``
        line above still fires either way.
        """
        stage = _classify_failure_stage(error)
        acknowledged = False
        try:
            acknowledged = self._acknowledge_failed_intake(msg, stage)
        except Exception:
            logger.exception(
                "Failed to acknowledge sender for quarantined message %s",
                getattr(msg, "id", None),
            )

        if not self.enable_db_writes or not self._flask_app:
            return

        try:
            from datetime import datetime

            from app.extensions import db
            from app.models import FailedIntake

            received_at = _received_at_or_now(getattr(msg, "received_datetime", None))
            detail = str(error) or repr(error)
            record = FailedIntake(
                received_at=received_at,
                source_email_id=getattr(msg, "id", None),
                sender_name=getattr(msg, "sender_name", None),
                sender_email=getattr(msg, "sender_email", None),
                subject=getattr(msg, "subject", None),
                failure_stage=stage,
                error_type=type(error).__name__,
                error_detail=detail[:2000],
                acknowledged=acknowledged,
            )
            with self._flask_app.app_context():
                db.session.add(record)
                db.session.commit()
        except Exception:
            logger.exception(
                "Failed to record failed-intake row for message %s",
                getattr(msg, "id", None),
            )

    def _acknowledge_failed_intake(self, msg: EmailMessage, stage: str) -> bool:
        """Optionally email the sender that their RFQ needs manual handling.

        Returns True only if an acknowledgment was actually sent. Off by default
        (``enable_failure_ack``); when on, it declines to send to internal/test
        senders, our own mailbox, or automated addresses (loop avoidance).
        """
        if not self.enable_failure_ack:
            return False
        if not hasattr(self.email_client, "send_mail"):
            logger.warning(
                "Failure ack enabled but provider %s cannot send mail; skipping",
                type(self.email_client).__name__,
            )
            return False

        sender_email = (getattr(msg, "sender_email", None) or "").strip()
        if not self._is_ackable_sender(sender_email):
            logger.info("Not acknowledging quarantined message from %r", sender_email or "<none>")
            return False

        subject = "We received your request — manual review needed"
        original_subject = getattr(msg, "subject", None)
        if original_subject:
            subject = f"Re: {original_subject}"
        body = (
            "Thank you for your request. It reached us, but our system could "
            "not process it automatically, so a member of our team will handle "
            "it manually and follow up with you.\n\n"
            "No action is needed on your part.\n\n"
            "Allan Edwards, Inc."
        )
        self.email_client.send_mail(
            to_email=sender_email,
            subject=subject,
            body_text=body,
            cc_email=self.quote_email_cc,
        )
        logger.info("Acknowledged quarantined RFQ to %s (stage=%s)", sender_email, stage)
        return True

    def _is_ackable_sender(self, sender_email: str) -> bool:
        """Whether an automatic acknowledgment may be sent to *sender_email*."""
        email = (sender_email or "").strip().lower()
        if not email or "@" not in email:
            return False
        if self._mailbox_address and email == self._mailbox_address:
            return False  # never acknowledge our own mailbox — that is a loop
        local_part, _, domain = email.partition("@")
        if domain in self._ack_skip_domains:
            return False
        # Automated / non-human addresses: replying invites mail loops.
        noreply_markers = ("noreply", "no-reply", "donotreply", "do-not-reply",
                           "mailer-daemon", "postmaster", "bounce")
        if any(marker in local_part for marker in noreply_markers):
            return False
        return True

    def _generate_db_quote_number(self) -> str:
        """Generate a sequential quote number from the database."""
        from .db_writer import _generate_fiscal_quote_number

        if not self._flask_app:
            return _generate_quote_number()

        with self._flask_app.app_context():
            return _generate_fiscal_quote_number()

    def _build_quote_pdf(self, quote: Quote) -> tuple[str, bytes]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        filename = _quote_pdf_name(quote)
        path = self.output_dir / filename
        generate_quote_pdf(quote, path)
        content = path.read_bytes()
        return filename, content

    def _create_review_draft(
        self,
        msg: OutlookMessage,
        rfq: ParsedRFQ,
        quote: Quote,
        to_email: str,
    ) -> str:
        """Create an informational draft when auto-pricing fails ($0 quote)."""
        subject = f"[NEEDS PRICING] RFQ from {msg.sender_name or msg.sender_email or 'unknown'}"
        if quote.project_line:
            subject += f" - {quote.project_line}"

        body = _build_review_body(msg, rfq, quote)
        return self.email_client.create_draft(
            to_email=to_email,
            subject=subject,
            body_text=body,
            attachments=[],
            cc_email=self.quote_email_cc,
        )

    def _finalize_message(self, message_id: str) -> None:
        if (
            self.processed_folder_name
            and hasattr(self.email_client, "get_or_create_folder")
            and hasattr(self.email_client, "move_message")
        ):
            if not self._processed_folder_id:
                self._processed_folder_id = self.email_client.get_or_create_folder(self.processed_folder_name)
            self.email_client.move_message(message_id, self._processed_folder_id)
        else:
            self.email_client.mark_read(message_id)

        self.state.add(message_id)


def _classify_failure_stage(error: Exception) -> str:
    """Map an exception to a coarse failure-stage label for the intake record."""
    if isinstance(error, LLMResponseTruncated):
        return "parse_truncated"
    if isinstance(error, IntegrityError):
        return "db_write"
    return "processing"


def _received_at_or_now(received_datetime: str | None) -> datetime:
    """Parse an inbox timestamp, falling back to now when it is missing/invalid."""
    if received_datetime:
        parsed = _parse_datetime(received_datetime)
        if parsed is not None:
            return parsed
    return datetime.now(timezone.utc)


def _normalize_body(content: str, preview: str) -> str:
    body = (content or "").strip()
    if body:
        return body
    return (preview or "").strip()


def _parse_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _generate_quote_number() -> str:
    # Keep existing quote-number shape for compatibility with current tooling.
    stamp = int(time.time()) % 1000
    return f"126-{stamp:03d}"


def _quote_pdf_name(quote: Quote) -> str:
    if quote.project_line:
        safe = quote.project_line.replace(" ", "-").replace("/", "-")
        return f"quote-{quote.quote_number}-{safe}.pdf"
    return f"quote-{quote.quote_number}.pdf"


def _build_draft_body(quote: Quote) -> str:
    lines = [
        "Attached is your requested quote PDF.",
        "",
        f"Quote Number: {quote.quote_number}",
    ]
    if quote.project_line:
        lines.append(f"Project: {quote.project_line}")
    lines.append("")
    lines.append("Thank you,")
    lines.append("Allan Edwards, Inc.")
    return "\n".join(lines)


def _build_review_body(msg: EmailMessage, rfq: ParsedRFQ, quote: Quote) -> str:
    """Build the body for a review draft when auto-pricing produces $0."""
    lines = [
        "*** AUTO-PRICING COULD NOT COMPLETE THIS QUOTE — MANUAL PRICING NEEDED ***",
        "",
        "An RFQ was received but the system could not price one or more items.",
        "Please review the details below and prepare the quote manually.",
        "",
        "--- Original Email ---",
        f"From: {msg.sender_name or ''} <{msg.sender_email or 'unknown'}>",
        f"Subject: {msg.subject}",
        "",
    ]

    if rfq.customer_name:
        lines.append(f"Customer: {rfq.customer_name}")
    if rfq.contact_name:
        lines.append(f"Contact: {rfq.contact_name}")
    if rfq.contact_email:
        lines.append(f"Email: {rfq.contact_email}")
    if rfq.contact_phone:
        lines.append(f"Phone: {rfq.contact_phone}")

    if rfq.ship_to:
        ship_parts = []
        for attr in ("company", "city", "state"):
            val = getattr(rfq.ship_to, attr, None)
            if val:
                ship_parts.append(val)
        if ship_parts:
            lines.append(f"Ship To: {', '.join(ship_parts)}")

    lines.append("")
    lines.append("--- Requested Items ---")
    for i, item in enumerate(rfq.items, start=1):
        desc = item.description or item.product_type
        qty = item.quantity
        dia = f', {item.diameter}" OD' if item.diameter else ""
        wt = f", {item.wall_thickness} w/t" if item.wall_thickness else ""
        lines.append(f"  {i}. {desc} (qty {qty}{dia}{wt})")

    # Show which items we could vs couldn't price
    priced = [li for li in quote.line_items if not li.is_note and li.unit_price > 0]
    unpriced = [li for li in quote.line_items if not li.is_note and li.unit_price == 0]
    if unpriced:
        lines.append("")
        lines.append("--- Items Needing Manual Pricing ---")
        for li in unpriced:
            lines.append(f"  - {li.description}")
    if priced:
        lines.append("")
        lines.append("--- Items Successfully Priced ---")
        for li in priced:
            lines.append(f"  - {li.description}: ${li.unit_price} x {li.quantity} = ${li.total}")

    if rfq.notes:
        lines.append("")
        lines.append(f"Notes: {rfq.notes}")

    lines.append("")
    lines.append("--- Original Email Body ---")
    body_preview = msg.body_preview or msg.body_content or ""
    if len(body_preview) > 1000:
        body_preview = body_preview[:1000] + "..."
    lines.append(body_preview)

    return "\n".join(lines)


def _parse_message_to_rfqs(
    msg: OutlookMessage,
    body_text: str,
    provider: LLMProvider,
    attachments: list[OutlookAttachment] | None = None,
):
    """Bridge Graph message fields into the existing .eml parser pipeline."""
    import email as email_mod
    from email.message import EmailMessage

    eml = EmailMessage()
    if msg.sender_email:
        if msg.sender_name:
            eml["From"] = f"{msg.sender_name} <{msg.sender_email}>"
        else:
            eml["From"] = msg.sender_email
    eml["Subject"] = msg.subject
    if msg.internet_message_id:
        eml["Message-ID"] = msg.internet_message_id
    eml.set_content(body_text)

    # Attach fetched Outlook attachments as MIME parts so the parser sees them.
    for att in attachments or []:
        if att.content_type == "message/rfc822":
            # Embedded email: parse into a Message and attach as message/rfc822
            try:
                embedded = email_mod.message_from_bytes(att.content_bytes)
                eml.add_attachment(
                    embedded,
                    maintype="message",
                    subtype="rfc822",
                    filename=att.filename,
                )
            except Exception:
                logger.warning("Could not parse message/rfc822 attachment %s", att.filename)
        else:
            maintype, _, subtype = att.content_type.partition("/")
            if not subtype:
                maintype, subtype = "application", "octet-stream"
            eml.add_attachment(
                att.content_bytes,
                maintype=maintype,
                subtype=subtype,
                filename=att.filename,
            )

    with tempfile.NamedTemporaryFile(suffix=".eml", delete=False) as f:
        temp_path = Path(f.name)
        temp_path.write_bytes(eml.as_bytes())

    try:
        return parse_rfq_multi(temp_path, provider)
    finally:
        temp_path.unlink(missing_ok=True)
