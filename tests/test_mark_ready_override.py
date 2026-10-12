"""Explicit Mark Ready approval for unusual quotes.

The approval is deliberately narrow: it lets a human-reviewed quote bypass
parser/pricing-completeness warnings in the manual send workflow, while hard
quote shape, lifecycle, authentication, and unattended auto-send gates remain
closed.  The approval is bound to a deterministic customer-content hash.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app import create_app
from app.confidence import quote_has_current_ready_override, sync_quote_confidence
from app.extensions import db
from app.models import (
    AuditLog,
    Quote,
    QuoteLineItem,
    QuoteStatus,
    TrustRampConfig,
    User,
)
from app.send_service import maybe_auto_send


@pytest.fixture()
def app(db_url, tmp_path, monkeypatch):
    from app.config import Config

    monkeypatch.setattr(Config, "SQLALCHEMY_DATABASE_URI", db_url)
    monkeypatch.setattr(Config, "TESTING", True, raising=False)
    application = create_app()
    application.config["QUOTE_ARTIFACT_DIR"] = str(tmp_path / "quote_versions")

    with application.app_context():
        db.create_all()
        owner = User(email="owner@example.com", name="Owner", password_hash="")
        owner.set_password("secret123")
        db.session.add(owner)
        db.session.commit()
        yield application
        db.session.remove()


@pytest.fixture()
def client(app):
    browser = app.test_client()
    browser.post(
        "/auth/password",
        data={"email": "owner@example.com", "password": "secret123"},
        follow_redirects=True,
    )
    return browser


def _unusual_quote() -> tuple[int, int]:
    quote = Quote(
        quote_number="126-502",
        status=QuoteStatus.NEEDS_PRICING,
        customer_name_raw="Unusual Field Services",
        contact_name="Pat Buyer",
        contact_email="pat@example.com",
        project_name="Emergency day-rate work",
    )
    db.session.add(quote)
    db.session.flush()
    item = QuoteLineItem(
        quote_id=quote.id,
        product_type="service",
        part_number="TBD",
        description="Field service day rate TBD",
        quantity=1,
        unit_price=0,
        line_total=0,
        sort_order=1,
    )
    db.session.add(item)
    db.session.commit()
    return quote.id, item.id


def _mark_ready(client, quote_id: int):
    response = client.post(f"/quotes/{quote_id}/status", data={"status": "ready"})
    assert response.status_code == 200
    return response


def test_mark_ready_approval_survives_reload_and_opens_send_form(client, app):
    with app.app_context():
        quote_id, _ = _unusual_quote()

    response = _mark_ready(client, quote_id)
    assert b"READY BY HUMAN REVIEW" in response.data

    with app.app_context():
        quote = db.session.get(Quote, quote_id)
        assert quote.status == QuoteStatus.READY
        assert quote_has_current_ready_override(quote)
        assert quote.ready_override_at is not None
        assert quote.ready_override_by is not None
        audit = db.session.query(AuditLog).filter_by(
            quote_id=quote_id, action="ready_override_approved"
        ).one()
        assert audit.user_id == quote.ready_override_by
        assert audit.details["fingerprint"] == quote.ready_override_fingerprint
        assert len(audit.details["pricing_block_reasons"]) == 3

    detail = client.get(f"/quotes/{quote_id}")
    assert detail.status_code == 200
    assert b"READY BY HUMAN REVIEW" in detail.data
    send_form = client.get(f"/quotes/{quote_id}/send-form")
    assert send_form.status_code == 200
    assert b"needs pricing before it can be sent" not in send_form.data.lower()

    with app.app_context():
        assert quote_has_current_ready_override(db.session.get(Quote, quote_id))


def test_customer_visible_edit_revokes_approval_before_send(client, app):
    with app.app_context():
        quote_id, _ = _unusual_quote()
    _mark_ready(client, quote_id)

    saved = client.post(
        f"/quotes/{quote_id}/meta",
        data={
            "quote_number": "126-502",
            "project_name": "Different customer-visible project",
            "notes_customer": "Changed scope",
            "notes_internal": "Internal note does not matter",
        },
    )
    assert saved.status_code == 200

    with app.app_context():
        quote = db.session.get(Quote, quote_id)
        assert quote.status == QuoteStatus.NEEDS_PRICING
        assert not quote_has_current_ready_override(quote)
        assert quote.ready_override_at is None
        revoked = db.session.query(AuditLog).filter_by(
            quote_id=quote_id, action="ready_override_revoked"
        ).one()
        assert revoked.details["reason"] == "customer-facing quote content changed"

    # Reload and send validation independently reach the same persisted state.
    assert client.get(f"/quotes/{quote_id}").status_code == 200
    blocked = client.get(f"/quotes/{quote_id}/send-form")
    assert b"needs pricing before it can be sent" in blocked.data.lower()


def test_internal_note_edit_does_not_invalidate_customer_content_approval(client, app):
    with app.app_context():
        quote_id, _ = _unusual_quote()
    _mark_ready(client, quote_id)

    response = client.post(
        f"/quotes/{quote_id}/meta",
        data={
            "quote_number": "126-502",
            "project_name": "Emergency day-rate work",
            "notes_customer": "",
            "notes_internal": "Call Chip before scheduling",
        },
    )
    assert response.status_code == 200
    with app.app_context():
        quote = db.session.get(Quote, quote_id)
        assert quote.status == QuoteStatus.READY
        assert quote_has_current_ready_override(quote)


def test_unapproved_flagged_quote_stays_blocked(client, app):
    with app.app_context():
        quote_id, _ = _unusual_quote()
        quote = db.session.get(Quote, quote_id)
        quote.status = QuoteStatus.READY  # READY alone is not an approval.
        db.session.commit()

    blocked = client.get(f"/quotes/{quote_id}/send-form")
    assert b"needs pricing before it can be sent" in blocked.data.lower()
    with app.app_context():
        quote = db.session.get(Quote, quote_id)
        assert quote.status == QuoteStatus.READY
        assert not quote_has_current_ready_override(quote)


@patch("app.routes._attempt_auto_send")
def test_mark_ready_never_invokes_auto_send(mock_attempt, client, app):
    with app.app_context():
        quote_id, _ = _unusual_quote()
    _mark_ready(client, quote_id)
    mock_attempt.assert_not_called()


@patch("allenedwards.outlook.OutlookClient")
def test_manual_send_honors_current_approval(mock_outlook, client, app, monkeypatch):
    with app.app_context():
        quote_id, _ = _unusual_quote()
    _mark_ready(client, quote_id)

    monkeypatch.setenv("EMAIL_DELIVERY_ENABLED", "true")
    monkeypatch.setenv("O365_EMAIL", "sender@example.com")
    monkeypatch.setenv("O365_PASSWORD", "test")
    monkeypatch.setenv("ENABLE_OUTLOOK_DRAFTS", "false")
    monkeypatch.delenv("SEND_EMAIL_ALLOWLIST", raising=False)
    response = client.post(
        f"/quotes/{quote_id}/send",
        data={"to_email": "pat@example.com", "subject": "Approved unusual quote"},
    )

    assert b"Quote Sent" in response.data
    mock_outlook.return_value.send_mail.assert_called_once()
    with app.app_context():
        assert db.session.get(Quote, quote_id).status == QuoteStatus.SENT


@patch("allenedwards.outlook.OutlookClient")
def test_ready_override_cannot_make_flagged_quote_auto_send(
    mock_outlook, client, app, monkeypatch
):
    with app.app_context():
        quote_id, _ = _unusual_quote()
        db.session.add(TrustRampConfig(active_tier=2))
        db.session.commit()
    _mark_ready(client, quote_id)

    monkeypatch.setenv("EMAIL_DELIVERY_ENABLED", "true")
    with app.app_context():
        quote = db.session.get(Quote, quote_id)
        sync_quote_confidence(quote)
        db.session.commit()

        result = maybe_auto_send(quote)

        assert result["attempted"] is False
        assert result["eligible"] is False
        assert any("All lines priced" in reason for reason in result["reasons"])
        assert quote.status == QuoteStatus.READY
    mock_outlook.return_value.send_mail.assert_not_called()


@patch("allenedwards.outlook.OutlookClient")
def test_sent_override_quote_survives_detail_reload(mock_outlook, client, app, monkeypatch):
    """T502 review regression: a detail GET after sending must not let the
    still-present pricing flags demote SENT back to NEEDS_PRICING, and the
    immutable send record must come through the reload untouched."""
    with app.app_context():
        quote_id, item_id = _unusual_quote()
    _mark_ready(client, quote_id)

    monkeypatch.setenv("EMAIL_DELIVERY_ENABLED", "true")
    monkeypatch.setenv("O365_EMAIL", "sender@example.com")
    monkeypatch.setenv("O365_PASSWORD", "test")
    monkeypatch.setenv("ENABLE_OUTLOOK_DRAFTS", "false")
    monkeypatch.delenv("SEND_EMAIL_ALLOWLIST", raising=False)
    sent = client.post(
        f"/quotes/{quote_id}/send",
        data={"to_email": "pat@example.com", "subject": "Approved unusual quote"},
    )
    assert b"Quote Sent" in sent.data

    with app.app_context():
        quote = db.session.get(Quote, quote_id)
        assert quote.status == QuoteStatus.SENT
        version = quote.versions[0]
        before = (
            version.version_number,
            version.pdf_path,
            version.artifact_status,
            version.line_items_snapshot,
            version.sent_at,
            version.sent_to,
        )
        assert version.artifact_status == "retained"
        assert Path(version.pdf_path).exists()

    detail = client.get(f"/quotes/{quote_id}")
    assert detail.status_code == 200

    with app.app_context():
        quote = db.session.get(Quote, quote_id)
        assert quote.status == QuoteStatus.SENT
        # The send record is immutable: same single version, same artifact.
        assert len(quote.versions) == 1
        version = quote.versions[0]
        assert (
            version.version_number,
            version.pdf_path,
            version.artifact_status,
            version.line_items_snapshot,
            version.sent_at,
            version.sent_to,
        ) == before
        assert Path(version.pdf_path).exists()
        # The live line the customer was quoted is not rewritten by a view.
        item = db.session.get(QuoteLineItem, item_id)
        assert item.part_number == "TBD"
        # No revocation audit fired from a read-only page view.
        assert (
            db.session.query(AuditLog)
            .filter_by(quote_id=quote_id, action="ready_override_revoked")
            .count()
            == 0
        )


@pytest.mark.parametrize(
    "lifecycle_status",
    [QuoteStatus.SENT, QuoteStatus.REPLACED, QuoteStatus.ARCHIVED],
)
def test_lifecycle_status_not_demoted_by_detail_reload(client, app, lifecycle_status):
    """Pricing flags never rewrite a lifecycle state, approval or not."""
    with app.app_context():
        quote_id, _ = _unusual_quote()
        quote = db.session.get(Quote, quote_id)
        quote.status = lifecycle_status
        db.session.commit()

    assert client.get(f"/quotes/{quote_id}").status_code == 200
    with app.app_context():
        assert db.session.get(Quote, quote_id).status == lifecycle_status


def test_archiving_unusual_quote_sticks(client, app):
    """The explicit archive action must not bounce back to NEEDS_PRICING."""
    with app.app_context():
        quote_id, _ = _unusual_quote()
    _mark_ready(client, quote_id)

    response = client.post(f"/quotes/{quote_id}/status", data={"status": "archived"})
    assert response.status_code == 200
    with app.app_context():
        quote = db.session.get(Quote, quote_id)
        assert quote.status == QuoteStatus.ARCHIVED
        # Archiving withdraws the approval; it must not survive into a
        # later unarchive of possibly-changed content.
        assert not quote_has_current_ready_override(quote)
        assert quote.ready_override_at is None

    # A later reload keeps it archived.
    assert client.get(f"/quotes/{quote_id}").status_code == 200
    with app.app_context():
        assert db.session.get(Quote, quote_id).status == QuoteStatus.ARCHIVED


@pytest.mark.parametrize("terminal_status", [QuoteStatus.SENT, QuoteStatus.REPLACED])
def test_terminal_lifecycle_cannot_be_marked_ready(client, app, terminal_status):
    with app.app_context():
        quote_id, _ = _unusual_quote()
        quote = db.session.get(Quote, quote_id)
        quote.status = terminal_status
        db.session.commit()

    response = client.post(f"/quotes/{quote_id}/status", data={"status": "ready"})
    assert response.status_code == 409
    with app.app_context():
        assert db.session.get(Quote, quote_id).status == terminal_status


def test_empty_quote_is_structurally_blocked_from_mark_ready(client, app):
    with app.app_context():
        empty = Quote(quote_number="126-EMPTY", status=QuoteStatus.IN_REVIEW)
        db.session.add(empty)
        db.session.commit()
        quote_id = empty.id

    response = client.post(f"/quotes/{quote_id}/status", data={"status": "ready"})
    assert response.status_code == 422
    assert b"at least one customer quote line" in response.data


def test_mark_ready_still_requires_authentication(app):
    with app.app_context():
        quote_id, _ = _unusual_quote()
    response = app.test_client().post(
        f"/quotes/{quote_id}/status", data={"status": "ready"}
    )
    assert response.status_code == 302
