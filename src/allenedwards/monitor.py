"""Polling monitor that turns Outlook RFQs into quote drafts."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import signal
import tempfile
import time
from typing import Any

from .outlook import OutlookAttachment, OutlookClient, OutlookMessage
from .parser import ParsedRFQ, classify_rfq, parse_rfq_multi
from .pdf_generator import generate_quote_pdf
from .pricing import Quote, generate_quote
from .providers.base import LLMProvider

logger = logging.getLogger(__name__)


class ProcessedState:
    """Tracks processed Outlook message IDs and a high-water mark on disk."""

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
        if not self.last_seen_datetime or received_datetime > self.last_seen_datetime:
            self.last_seen_datetime = received_datetime

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {"processed_ids": sorted(self._ids)}
        if self.last_seen_datetime:
            payload["last_seen_datetime"] = self.last_seen_datetime
        self.path.write_text(json.dumps(payload, indent=2))


class InboxMonitor:
    """Monitors unread Outlook messages and creates quote drafts for RFQs."""

    def __init__(
        self,
        *,
        outlook: OutlookClient,
        provider: LLMProvider,
        poll_interval_seconds: int,
        state_path: Path,
        output_dir: Path,
        quote_email_cc: str | None = None,
        processed_folder_name: str | None = None,
    ):
        self.outlook = outlook
        self.provider = provider
        self.poll_interval_seconds = poll_interval_seconds
        self.state = ProcessedState(state_path)
        self.output_dir = output_dir
        self.quote_email_cc = quote_email_cc
        self.processed_folder_name = processed_folder_name
        self._processed_folder_id: str | None = None
        self._running = True

    def _shutdown_signal(self, signum: int, _frame: Any) -> None:
        logger.info("Received signal %s; shutting down", signum)
        self._running = False

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
            time.sleep(self.poll_interval_seconds)

    def run_once(self) -> int:
        messages = self.outlook.list_inbox_messages(
            since=self.state.last_seen_datetime
        )
        logger.info("Fetched %s inbox messages (since=%s)", len(messages), self.state.last_seen_datetime)
        processed_count = 0

        for msg in messages:
            try:
                if msg.received_datetime:
                    self.state.advance_watermark(msg.received_datetime)

                if self.state.contains(msg.id):
                    logger.debug("Skipping already processed message %s", msg.id)
                    continue

                handled = self._process_message(msg)
                if handled:
                    processed_count += 1
            except Exception:
                logger.exception("Failed processing message %s", msg.id)

        if messages:
            self.state.save()

        return processed_count

    def _process_message(self, msg: OutlookMessage) -> bool:
        body_text = _normalize_body(msg.body_content, msg.body_preview)
        if not classify_rfq(msg.subject, body_text, self.provider):
            logger.info("Message %s classified as non-RFQ", msg.id)
            self._finalize_message(msg.id)
            return False

        attachments: list[OutlookAttachment] = []
        if msg.has_attachments:
            try:
                attachments = self.outlook.get_attachments(msg.id)
                logger.info("Fetched %d attachments for message %s", len(attachments), msg.id)
            except Exception:
                logger.exception("Failed fetching attachments for message %s", msg.id)

        rfqs = _parse_message_to_rfqs(msg, body_text, self.provider, attachments)
        if not rfqs:
            logger.warning("Message %s produced no parsed RFQs", msg.id)
            self._finalize_message(msg.id)
            return False

        base_quote_number = _generate_quote_number()

        for idx, rfq in enumerate(rfqs):
            quote_number = base_quote_number if len(rfqs) == 1 else f"{base_quote_number}-{idx + 1:02d}"
            quote = generate_quote(rfq, quote_number)

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
                draft_id = self.outlook.create_draft(
                    to_email=to_email,
                    subject=subject,
                    body_text=body,
                    attachments=[(pdf_name, pdf_bytes)],
                    cc_email=self.quote_email_cc,
                )
                logger.info("Created draft %s for message %s", draft_id or "<unknown>", msg.id)

        self._finalize_message(msg.id)
        return True

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
        return self.outlook.create_draft(
            to_email=to_email,
            subject=subject,
            body_text=body,
            attachments=[],
            cc_email=self.quote_email_cc,
        )

    def _finalize_message(self, message_id: str) -> None:
        if self.processed_folder_name:
            if not self._processed_folder_id:
                self._processed_folder_id = self.outlook.get_or_create_folder(self.processed_folder_name)
            self.outlook.move_message(message_id, self._processed_folder_id)
        else:
            self.outlook.mark_as_read(message_id)

        self.state.add(message_id)


def _normalize_body(content: str, preview: str) -> str:
    body = (content or "").strip()
    if body:
        return body
    return (preview or "").strip()


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


def _build_review_body(msg: OutlookMessage, rfq: ParsedRFQ, quote: Quote) -> str:
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
