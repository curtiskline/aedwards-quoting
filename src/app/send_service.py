"""Shared quote-send machinery + Tier-2 auto-send (CP-2c).

The human send route (routes.quote_send) and the Tier-2 auto-send path both
run through the functions here: the same gates (EMAIL_DELIVERY_ENABLED,
needs-pricing, SEND_EMAIL_ALLOWLIST), the same sender resolution, the same
PDF archive / immutable QuoteVersion / audit record. The two paths differ
only in ordering: auto-send commits an AutoSendClaim atomically with the
QuoteVersion BEFORE the external send (hazard §12.1), because an unattended
send that crashes mid-flight must fail closed — a replay finds the claim and
never sends twice. The human path keeps its pre-CP-2c behavior exactly.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

from flask import current_app
from sqlalchemy.exc import IntegrityError

from .confidence import (
    AUTO_SEND_TIER,
    active_trust_tier,
    auto_send_evaluation,
    quote_has_tbd_items,
    quote_has_unpriced_items,
)
from .extensions import db
from .models import AuditLog, AutoSendClaim, Quote, QuoteStatus, QuoteVersion

logger = logging.getLogger(__name__)


class SendBlocked(Exception):
    """A send gate refused this quote before any external attempt."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


# ---------------------------------------------------------------------------
# Gates. Every send path — human or auto — must run these; the messages are
# user-facing (the send form renders them verbatim).
# ---------------------------------------------------------------------------


def quote_needs_pricing(quote: Quote) -> bool:
    """Whether a quote is unsafe to send to a customer (routes' rule)."""
    return (
        quote.status == QuoteStatus.NEEDS_PRICING
        or quote_has_unpriced_items(quote)
        or quote_has_tbd_items(quote)
    )


def ensure_delivery_enabled() -> None:
    from .email_service import email_delivery_enabled

    if not email_delivery_enabled():
        raise SendBlocked("Email delivery is disabled in this environment.")


def ensure_quote_priced(quote: Quote) -> None:
    if quote_needs_pricing(quote):
        raise SendBlocked("This quote needs pricing before it can be sent.")


def ensure_recipient_allowed(to_email: str) -> None:
    allowlist_raw = os.getenv("SEND_EMAIL_ALLOWLIST", "").strip()
    if not allowlist_raw:
        return
    allowed = {e.strip().lower() for e in allowlist_raw.split(",") if e.strip()}
    if to_email.lower() not in allowed:
        raise SendBlocked(
            f"Recipient '{to_email}' is not in the allowed send list. "
            f"Allowed: {', '.join(sorted(allowed))}"
        )


def check_send_gates(quote: Quote, to_email: str) -> None:
    """All gates in the human route's order. Raises SendBlocked on refusal."""
    ensure_delivery_enabled()
    ensure_quote_priced(quote)
    if not to_email:
        raise SendBlocked("Recipient email is required.")
    ensure_recipient_allowed(to_email)


# ---------------------------------------------------------------------------
# Sender resolution + message construction.
# ---------------------------------------------------------------------------


def resolve_sender_client(user_email: str | None):
    """Build the Graph client for an outbound quote. Raises SendBlocked when
    O365 credentials are absent. Returns (client, send_from)."""
    from allenedwards.outlook import OutlookClient

    from .email_service import resolve_quote_sender

    sender_email = os.getenv("O365_EMAIL")
    sender_password = os.getenv("O365_PASSWORD")
    client_id = os.getenv("O365_CLIENT_ID")
    client_secret = os.getenv("O365_CLIENT_SECRET")
    tenant_id = os.getenv("O365_TENANT_ID")
    scopes_raw = os.getenv("O365_SCOPES", "")

    if not sender_email or (not sender_password and not client_secret):
        raise SendBlocked(
            "O365 credentials are not configured. "
            "Set O365_EMAIL and O365_PASSWORD or O365_CLIENT_SECRET."
        )

    send_from = resolve_quote_sender(user_email, sender_email, client_secret)
    scopes = [s.strip() for s in scopes_raw.split(",") if s.strip()] or None
    client = OutlookClient(
        email_address=send_from,
        password=sender_password,
        client_id=client_id,
        scopes=scopes,
        client_secret=client_secret,
        tenant_id=tenant_id,
    )
    return client, send_from


def build_quote_email_body(quote: Quote) -> str:
    return (
        f"Please find attached quote {quote.quote_number} from Allan Edwards, Inc.\n\n"
        f"If you have any questions, please don't hesitate to contact us.\n\n"
        f"Thank you,\nAllan Edwards, Inc.\n(918) 583-7184\nwww.allanedwards.com"
    )


def default_quote_subject(quote: Quote) -> str:
    return f"Quote {quote.quote_number} — Allan Edwards, Inc."


def outlook_drafts_enabled() -> bool:
    return os.environ.get("ENABLE_OUTLOOK_DRAFTS", "true").lower() not in ("0", "false", "no")


# ---------------------------------------------------------------------------
# Immutable send-time records.
# ---------------------------------------------------------------------------


def quote_line_items_snapshot(quote: Quote) -> list[dict[str, object]]:
    """Return JSON-safe, point-in-time copies of every priced line item."""
    import copy

    return [
        {
            "id": item.id,
            "product_type": item.product_type,
            "description": item.description,
            "quantity": str(item.quantity),
            "unit_price": str(item.unit_price),
            "line_total": str(item.line_total),
            # Includes all original dimensions and pricing basis fields, not
            # only the subset currently rendered by the quote editor.
            "specs_json": copy.deepcopy(item.specs_json),
            "part_number": item.part_number,
            "sort_order": item.sort_order,
        }
        for item in quote.line_items
    ]


def archive_sent_quote_pdf(quote: Quote, version_number: int, pdf_bytes: bytes) -> str:
    """Persist a send-time PDF outside the deploy-replaced source tree.

    The hard-link publish makes the finished archive file exclusive: a
    duplicate version number cannot silently overwrite an existing record.
    """
    artifact_dir = Path(current_app.config["QUOTE_ARTIFACT_DIR"]).expanduser().resolve()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    safe_quote_number = re.sub(r"[^A-Za-z0-9._-]+", "_", quote.quote_number).strip("._")
    archive_path = artifact_dir / f"quote-{safe_quote_number}-v{version_number}.pdf"

    with tempfile.NamedTemporaryFile(
        mode="wb", suffix=".pdf", prefix=".quote-version-", dir=artifact_dir, delete=False
    ) as tmp:
        tmp.write(pdf_bytes)
        tmp.flush()
        os.fsync(tmp.fileno())
        temporary_path = Path(tmp.name)

    try:
        # link() fails if archive_path already exists, preserving the first
        # immutable record rather than replacing it.
        os.link(temporary_path, archive_path)
        archive_path.chmod(0o444)
    finally:
        temporary_path.unlink(missing_ok=True)

    return str(archive_path)


def remove_archived_pdf(archive_path: str) -> None:
    path = Path(archive_path)
    try:
        path.chmod(0o644)
    except FileNotFoundError:
        return
    path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Tier-2 auto-send.
# ---------------------------------------------------------------------------


def get_auto_send_claim(quote: Quote) -> AutoSendClaim | None:
    if quote.id is None:
        return None
    return db.session.query(AutoSendClaim).filter_by(quote_id=quote.id).first()


def maybe_auto_send(quote: Quote) -> dict | None:
    """Attempt Tier-2 auto-send; never raises into the calling flow.

    Trigger sites (post-monitor write, post-recompute commits) call this
    AFTER their own transaction has committed — a broken auto-send attempt
    must never take a quote write down with it.
    """
    try:
        return auto_send_quote(quote)
    except Exception:
        logger.exception("Auto-send attempt failed unexpectedly for quote %s", quote.id)
        db.session.rollback()
        return None


def auto_send_quote(quote: Quote) -> dict | None:
    """Run the full Tier-2 auto-send flow for one quote.

    Sequence (the ordering is the idempotency guarantee — see AutoSendClaim):
      1. Tier read live (kill-switch) + existing-claim short-circuit.
      2. auto_send_evaluation: every guardrail; any miss → no attempt at all.
      3. Send gates + sender resolution; a refusal writes a "blocked" claim
         so recompute loops never spin on a gated quote.
      4. Claim + immutable QuoteVersion + archived PDF committed together,
         BEFORE the external send.
      5. External send. Failure: version+archive released, claim "failed".
      6. Success: quote SENT, claim "sent", full-basis audit row.
    """
    if quote.id is None:
        return None
    if active_trust_tier() != AUTO_SEND_TIER:
        return None

    existing = get_auto_send_claim(quote)
    if existing is not None:
        return {"attempted": False, "already_claimed": True, "claim_status": existing.status}

    evaluation = auto_send_evaluation(quote)
    if not evaluation["eligible"]:
        return {"attempted": False, "eligible": False, "reasons": evaluation["reasons"]}

    to_email = (quote.contact_email or "").strip()
    subject = default_quote_subject(quote)

    try:
        check_send_gates(quote, to_email)
        client, send_from = resolve_sender_client(None)
    except SendBlocked as exc:
        return _record_terminal_claim(quote, "blocked", exc.message, evaluation)

    # PDF generation reuses the human path's generator (lazy import: routes
    # imports this module at startup).
    from .routes import _generate_pdf_bytes

    pdf_bytes, filename = _generate_pdf_bytes(quote)
    version_number = len(quote.versions) + 1
    snapshot = quote_line_items_snapshot(quote)
    try:
        archive_path = archive_sent_quote_pdf(quote, version_number, pdf_bytes)
    except FileExistsError:
        # A concurrent sender already published this version number.
        return {"attempted": False, "reasons": ["archive already exists for this version"]}

    now = datetime.utcnow()
    claim = AutoSendClaim(quote_id=quote.id, status="claimed")
    version = QuoteVersion(
        quote_id=quote.id,
        version_number=version_number,
        pdf_path=archive_path,
        artifact_status="retained",
        line_items_snapshot=snapshot,
        sent_at=now,
        sent_by=None,
        sent_to=to_email,
    )
    db.session.add_all([claim, version])
    try:
        db.session.flush()
        claim.quote_version_id = version.id
        # THE claim commit: from here on, no replay/crash can double-send.
        db.session.commit()
    except IntegrityError:
        # Another process claimed this quote between our check and commit.
        db.session.rollback()
        remove_archived_pdf(archive_path)
        return {"attempted": False, "already_claimed": True}

    try:
        client.send_mail(
            to_email=to_email,
            subject=subject,
            body_text=build_quote_email_body(quote),
            attachments=[(filename, pdf_bytes)],
            cc_email=None,
        )
    except Exception as exc:
        # Release the version record + archive; the claim stays as the
        # tombstone that blocks auto-retry. The human path (which never
        # reads claims) remains available for this quote.
        db.session.delete(version)
        claim.quote_version_id = None
        claim.status = "failed"
        claim.error = str(exc)
        db.session.add(
            AuditLog(
                quote_id=quote.id,
                action="auto_send_failed",
                details={**_audit_basis(quote, evaluation), "error": str(exc)},
            )
        )
        db.session.commit()
        remove_archived_pdf(archive_path)
        logger.error("Auto-send failed for quote %s: %s", quote.quote_number, exc)
        return {"attempted": True, "sent": False, "error": str(exc)}

    # Courtesy copy in Drafts — same semantics as the human path: failure to
    # create the copy never erases the fact that the customer email went out.
    if outlook_drafts_enabled():
        try:
            client.create_draft(
                to_email=to_email,
                subject=subject,
                body_text=build_quote_email_body(quote),
                attachments=[(filename, pdf_bytes)],
                cc_email=None,
            )
        except Exception:
            pass

    quote.status = QuoteStatus.SENT
    quote.updated_at = now
    claim.status = "sent"
    db.session.add(
        AuditLog(
            quote_id=quote.id,
            action="auto_sent",
            details={
                **_audit_basis(quote, evaluation),
                "to": to_email,
                "from": send_from,
                "subject": subject,
                "version_number": version_number,
            },
        )
    )
    db.session.commit()
    logger.info(
        "Auto-sent quote %s (id=%s) to %s at Tier %s (score=%s)",
        quote.quote_number,
        quote.id,
        to_email,
        evaluation["tier"],
        evaluation["score"],
    )
    return {"attempted": True, "sent": True, "version_number": version_number}


def _audit_basis(quote: Quote, evaluation: dict) -> dict:
    """The full auto-send decision snapshot required by design §4: tier,
    thresholds, every signal, and the source-email lineage."""
    confidence = quote.confidence
    return {
        "tier": evaluation["tier"],
        "auto_send_threshold": evaluation["auto_send_threshold"],
        "recommend_threshold": evaluation["recommend_threshold"],
        "score": evaluation["score"],
        "dollar_ceiling": evaluation["dollar_ceiling"],
        "quote_total": evaluation["quote_total"],
        "price_tolerance_pct": evaluation["price_tolerance_pct"],
        "signals": evaluation["signals"],
        "components": confidence.components_json if confidence is not None else None,
        "source_email_id": quote.source_email_id,
    }


def _record_terminal_claim(quote: Quote, status: str, error: str, evaluation: dict) -> dict:
    """Consume the quote's auto-send chance without sending (gate refusal).

    Committed with an audit row so staging can prove the delivery gate held;
    the unique claim keeps recompute loops from re-attempting forever.
    """
    claim = AutoSendClaim(quote_id=quote.id, status=status, error=error)
    db.session.add(claim)
    db.session.add(
        AuditLog(
            quote_id=quote.id,
            action="auto_send_blocked",
            details={**_audit_basis(quote, evaluation), "reason": error},
        )
    )
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return {"attempted": False, "already_claimed": True}
    logger.warning("Auto-send blocked for quote %s: %s", quote.quote_number, error)
    return {"attempted": True, "sent": False, "blocked": True, "error": error}
