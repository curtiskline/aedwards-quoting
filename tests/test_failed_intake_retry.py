"""Fix-and-retry of a hard-crashed parse from its FailedIntake row (T505).

The monitor persists the inbound body (provider original + normalized parser
input) on every FailedIntake it writes; the admin detail page then lets an
operator correct that text and retry it into a brand-new quote through the
same db_writer path intake uses, with its own QuoteSourceSnapshot and
message-level idempotency claim. Attempts are recorded append-only on
quote_reparse_attempt via failed_intake_id.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from allenedwards.monitor import InboxMonitor
from allenedwards.outlook import OutlookClient, OutlookMessage
from allenedwards.providers.base import LLMResponseTruncated
from allenedwards.providers.mock import MockProvider
from app import create_app
from app.extensions import db
from app.models import (
    AuditLog,
    FailedIntake,
    ProcessedInboundEmail,
    Quote,
    QuoteReparseAttempt,
    QuoteSourceSnapshot,
    User,
)
from app.reparse import (
    failed_intake_allows_reparse,
    failed_intake_panel_context,
    reparse_failed_intake,
)

RETRY_RESPONSE = {
    "customer_name": "Troy Companies",
    "contact_name": "Chris Edwards",
    "contact_email": "cedwards@troyco.com",
    "contact_phone": "918-555-0100",
    "ship_to": {
        "company": "Jones Creek Site",
        "attention": None,
        "street": "100 Pipeline Rd",
        "city": "Jones Creek",
        "state": "TX",
        "postal_code": "77541",
        "country": "United States",
    },
    "po_number": "PO-42",
    "items": [
        {
            "product_type": "sleeve",
            "quantity": 12,
            "diameter": "42",
            "wall_thickness": "0.5",
            "grade": "50",
            "length_ft": 10,
            "milling": False,
            "painting": False,
            "description": '42" Full Encirclement Sleeve, 1/2" w/t, GR50, 10 ft',
            "notes": None,
        }
    ],
    "urgency": "normal",
    "notes": "Retried from failed intake",
    "confidence": 0.93,
}


class ExplodingProvider(MockProvider):
    def complete_json(self, prompt, system=None):
        raise RuntimeError("LLM unavailable")


@pytest.fixture()
def app(db_url, monkeypatch):
    from app.config import Config

    monkeypatch.setattr(Config, "SQLALCHEMY_DATABASE_URI", db_url)
    monkeypatch.setattr(Config, "TESTING", True, raising=False)
    application = create_app()

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


def _seed_intake(**overrides) -> int:
    defaults = dict(
        received_at=datetime(2026, 10, 1, 12, 0),
        source_email_id="msg-crash-001",
        sender_name="Chris Edwards",
        sender_email="cedwards@troyco.com",
        subject="RFQ Sentinel 42in Jones Creek",
        failure_stage="parse_truncated",
        error_type="LLMResponseTruncated",
        error_detail="cut off",
        original_body="<html><body>Please quote 12 sleeves 42in.</body></html>",
        original_body_content_type="html",
        normalized_body_text="Please quote 12 sleeves 42in.",
    )
    defaults.update(overrides)
    intake = FailedIntake(**defaults)
    db.session.add(intake)
    db.session.commit()
    return intake.id


# ---------- monitor persists the body at quarantine time ----------


class TestBodyPersistedOnQuarantine:
    def _run_quarantine(self, app, tmp_path, msg):
        outlook = MagicMock(spec=OutlookClient)
        outlook.fetch_messages.return_value = [msg]
        monitor = InboxMonitor(
            outlook=outlook,
            provider=MagicMock(),
            poll_interval_seconds=60,
            state_path=tmp_path / "state.json",
            output_dir=tmp_path / "quotes",
            enable_db_writes=True,
            enable_outlook_drafts=False,
            flask_app=app,
        )
        with patch.object(
            monitor, "_process_message", side_effect=LLMResponseTruncated("cut off")
        ):
            monitor.run_once()

    def test_html_body_is_stored_original_and_normalized(self, app, tmp_path):
        msg = OutlookMessage(
            id="msg-html-1",
            subject="RFQ",
            sender_name="Buyer",
            sender_email="buyer@customer.com",
            body_preview="Please quote",
            body_content="<html><body><p>Please quote 10 pcs</p></body></html>",
            body_content_type="html",
            internet_message_id="<x@customer.com>",
            received_datetime="2026-10-01T12:00:00Z",
            has_attachments=False,
        )
        self._run_quarantine(app, tmp_path, msg)
        with app.app_context():
            row = FailedIntake.query.one()
            assert row.original_body == "<html><body><p>Please quote 10 pcs</p></body></html>"
            assert row.original_body_content_type == "html"
            assert "Please quote 10 pcs" in row.normalized_body_text
            assert "<html>" not in row.normalized_body_text

    def test_normalization_failure_still_writes_row(self, app, tmp_path):
        msg = OutlookMessage(
            id="msg-norm-fail",
            subject="RFQ",
            sender_name="Buyer",
            sender_email="buyer@customer.com",
            body_preview="preview",
            body_content="body text",
            body_content_type="text",
            internet_message_id="<y@customer.com>",
            received_datetime="2026-10-01T12:00:00Z",
            has_attachments=False,
        )
        with patch(
            "allenedwards.monitor._normalize_body", side_effect=RuntimeError("boom")
        ):
            self._run_quarantine(app, tmp_path, msg)
        with app.app_context():
            row = FailedIntake.query.one()
            assert row.original_body == "body text"
            assert row.normalized_body_text is None


# ---------- retry guards ----------


class TestRetryGuards:
    def test_resolved_intake_cannot_retry(self, app):
        intake_id = _seed_intake(resolved_at=datetime.utcnow())
        intake = db.session.get(FailedIntake, intake_id)
        assert not failed_intake_allows_reparse(intake)
        outcome = reparse_failed_intake(
            intake, "text", None, provider=MockProvider(RETRY_RESPONSE)
        )
        assert not outcome.ok
        assert Quote.query.count() == 0

    def test_legacy_intake_without_body_cannot_retry(self, app):
        intake_id = _seed_intake(
            original_body=None,
            original_body_content_type=None,
            normalized_body_text=None,
        )
        intake = db.session.get(FailedIntake, intake_id)
        assert not failed_intake_allows_reparse(intake)
        outcome = reparse_failed_intake(
            intake, "text", None, provider=MockProvider(RETRY_RESPONSE)
        )
        assert not outcome.ok
        assert Quote.query.count() == 0


# ---------- retry outcomes ----------


class TestRetrySuccess:
    def test_creates_quote_with_snapshot_claim_and_resolves_intake(self, app):
        intake_id = _seed_intake()
        intake = db.session.get(FailedIntake, intake_id)
        corrected = "Please quote 12 sleeves 42in, 1/2 wall, GR50, 10 ft."

        outcome = reparse_failed_intake(
            intake, corrected, None, provider=MockProvider(RETRY_RESPONSE)
        )

        assert outcome.ok
        quote = db.session.get(Quote, outcome.quote_id)
        assert quote is not None
        assert quote.sender_email == "cedwards@troyco.com"
        assert quote.source_email_id == "msg-crash-001"
        assert len(quote.line_items) == 1

        # The new quote carries its own immutable snapshot: provider original
        # from the intake row, normalized text = exactly what was parsed here.
        snapshot = QuoteSourceSnapshot.query.filter_by(quote_id=quote.id).one()
        assert snapshot.original_body == intake.original_body
        assert snapshot.original_body_content_type == "html"
        assert snapshot.normalized_body_text == corrected

        # The source email is claimed so the monitor can never re-process it.
        assert (
            ProcessedInboundEmail.query.filter_by(
                source_email_id="msg-crash-001"
            ).count()
            == 1
        )

        attempt = QuoteReparseAttempt.query.one()
        assert attempt.failed_intake_id == intake.id
        assert attempt.quote_id == quote.id
        assert attempt.succeeded
        assert attempt.input_edited  # corrected text differs from stored body

        assert intake.resolved_at is not None
        audit = AuditLog.query.filter_by(action="created_from_failed_intake").one()
        assert audit.quote_id == quote.id
        assert audit.details["failed_intake_id"] == intake.id

    def test_existing_claim_is_not_duplicated(self, app):
        intake_id = _seed_intake()
        db.session.add(ProcessedInboundEmail(source_email_id="msg-crash-001"))
        db.session.commit()
        intake = db.session.get(FailedIntake, intake_id)

        outcome = reparse_failed_intake(
            intake,
            intake.normalized_body_text,
            None,
            provider=MockProvider(RETRY_RESPONSE),
        )

        assert outcome.ok
        assert (
            ProcessedInboundEmail.query.filter_by(
                source_email_id="msg-crash-001"
            ).count()
            == 1
        )
        attempt = QuoteReparseAttempt.query.one()
        assert not attempt.input_edited  # stored text was retried unchanged


class TestRetryFailure:
    def test_failed_parse_records_attempt_and_leaves_intake_outstanding(self, app):
        intake_id = _seed_intake()
        intake = db.session.get(FailedIntake, intake_id)

        outcome = reparse_failed_intake(
            intake, "still broken text", None, provider=ExplodingProvider({})
        )

        assert not outcome.ok
        assert Quote.query.count() == 0
        attempt = QuoteReparseAttempt.query.one()
        assert attempt.failed_intake_id == intake.id
        assert attempt.quote_id is None
        assert not attempt.succeeded
        assert "RuntimeError" in attempt.error
        intake = db.session.get(FailedIntake, intake_id)
        assert intake.resolved_at is None

    def test_no_items_is_a_recorded_failure(self, app):
        intake_id = _seed_intake()
        intake = db.session.get(FailedIntake, intake_id)
        empty = dict(RETRY_RESPONSE, items=[])

        outcome = reparse_failed_intake(
            intake, "not an rfq", None, provider=MockProvider(empty)
        )

        assert not outcome.ok
        assert Quote.query.count() == 0
        assert QuoteReparseAttempt.query.count() == 1
        # The failed text prefills the panel so the correction survives.
        context = failed_intake_panel_context(intake)
        assert context["input_text"] == "not an rfq"
        assert context["allows_reparse"]


# ---------- routes ----------


class TestRetryRoutes:
    def test_detail_page_shows_stored_text(self, app, client):
        with app.app_context():
            intake_id = _seed_intake()
        page = client.get(f"/admin/failed-intakes/{intake_id}")
        assert page.status_code == 200
        assert b"Please quote 12 sleeves 42in." in page.data
        assert b"Try Making a Quote" in page.data

    def test_detail_page_explains_legacy_rows(self, app, client):
        with app.app_context():
            intake_id = _seed_intake(
                original_body=None,
                original_body_content_type=None,
                normalized_body_text=None,
            )
        page = client.get(f"/admin/failed-intakes/{intake_id}")
        assert page.status_code == 200
        assert b"No email text was stored" in page.data
        assert b"Try Making a Quote" not in page.data

    def test_retry_route_creates_quote_and_redirects(self, app, client, monkeypatch):
        from allenedwards import cli

        monkeypatch.setattr(cli, "load_environment", lambda: None)
        monkeypatch.setattr(cli, "get_provider", lambda: MockProvider(RETRY_RESPONSE))
        with app.app_context():
            intake_id = _seed_intake()
        response = client.post(
            f"/admin/failed-intakes/{intake_id}/reparse",
            data={"input_text": "Please quote 12 sleeves 42in."},
        )
        assert response.status_code == 302
        with app.app_context():
            quote = Quote.query.one()
            assert response.headers["Location"].endswith(f"/quotes/{quote.id}")

    def test_retry_route_rejects_resolved_intake_with_409(self, app, client):
        with app.app_context():
            intake_id = _seed_intake(resolved_at=datetime.utcnow())
        response = client.post(
            f"/admin/failed-intakes/{intake_id}/reparse",
            data={"input_text": "text"},
        )
        assert response.status_code == 409

    def test_retry_route_rejects_empty_text(self, app, client):
        with app.app_context():
            intake_id = _seed_intake()
        response = client.post(
            f"/admin/failed-intakes/{intake_id}/reparse",
            data={"input_text": "   "},
        )
        assert response.status_code == 302
        assert f"/admin/failed-intakes/{intake_id}" in response.headers["Location"]
        with app.app_context():
            assert Quote.query.count() == 0

    def test_list_page_links_retryable_rows(self, app, client):
        with app.app_context():
            _seed_intake()
        page = client.get("/admin/failed-intakes")
        assert page.status_code == 200
        assert b"Fix &amp; retry" in page.data
