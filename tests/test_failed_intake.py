"""Tests for the never-drop-an-RFQ failed-intake path (task 376).

A message that cannot be processed must leave a visible record instead of
disappearing silently: a FailedIntake row (and, when explicitly enabled, an
acknowledgment back to the sender).
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

from app import create_app
from app.config import Config
from app.extensions import db
from app.models import FailedIntake, User
from allenedwards.monitor import (
    InboxMonitor,
    _classify_failure_stage,
    _received_at_or_now,
)
from allenedwards.outlook import OutlookClient, OutlookMessage
from allenedwards.providers.base import LLMResponseTruncated


@pytest.fixture()
def app(tmp_path: Path):
    db_path = tmp_path / "test.db"
    previous_database_url = os.environ.get("DATABASE_URL")
    previous_config_database_url = Config.SQLALCHEMY_DATABASE_URI
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path}"
    application = create_app()
    application.config["TESTING"] = True
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()
    Config.SQLALCHEMY_DATABASE_URI = previous_config_database_url
    if previous_database_url is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = previous_database_url


def _make_msg(**overrides) -> OutlookMessage:
    defaults = dict(
        id="msg-fail-001",
        subject="RFQ - Sleeves",
        sender_name="Buyer Person",
        sender_email="buyer@customer.com",
        body_preview="Please quote 10 pcs",
        body_content="Please quote 10 pcs 6-5/8 x 0.25 GR50 sleeves",
        body_content_type="text",
        internet_message_id="<abc@customer.com>",
        received_datetime="2026-08-13T12:00:00Z",
        has_attachments=False,
    )
    defaults.update(overrides)
    return OutlookMessage(**defaults)


def _monitor(app, tmp_path, **kwargs):
    outlook = kwargs.pop("outlook", None) or MagicMock(spec=OutlookClient)
    return InboxMonitor(
        outlook=outlook,
        provider=MagicMock(),
        poll_interval_seconds=60,
        state_path=tmp_path / "state.json",
        output_dir=tmp_path / "quotes",
        enable_db_writes=True,
        enable_outlook_drafts=False,
        flask_app=app,
        **kwargs,
    )


# ---------- stage classification ----------


class TestClassifyFailureStage:
    def test_truncation(self):
        assert _classify_failure_stage(LLMResponseTruncated("cut off")) == "parse_truncated"

    def test_integrity_error(self):
        err = IntegrityError("stmt", {}, Exception("UNIQUE quote.quote_number"))
        assert _classify_failure_stage(err) == "db_write"

    def test_generic(self):
        assert _classify_failure_stage(RuntimeError("boom")) == "processing"


class TestReceivedAtOrNow:
    def test_missing_falls_back_to_now(self):
        assert _received_at_or_now(None) is not None

    def test_invalid_falls_back_to_now(self):
        assert _received_at_or_now("not-a-date") is not None

    def test_valid_is_parsed(self):
        parsed = _received_at_or_now("2026-08-13T12:00:00Z")
        assert parsed.year == 2026 and parsed.hour == 12


# ---------- recording ----------


class TestFailedIntakeRecording:
    def test_quarantine_writes_failed_intake_row(self, app, tmp_path):
        outlook = MagicMock(spec=OutlookClient)
        outlook.fetch_messages.return_value = [_make_msg()]
        monitor = _monitor(app, tmp_path, outlook=outlook)

        with patch.object(monitor, "_process_message", side_effect=LLMResponseTruncated("cut off")):
            processed = monitor.run_once()

        assert processed == 0
        with app.app_context():
            rows = FailedIntake.query.all()
            assert len(rows) == 1
            row = rows[0]
            assert row.source_email_id == "msg-fail-001"
            assert row.sender_email == "buyer@customer.com"
            assert row.subject == "RFQ - Sleeves"
            assert row.failure_stage == "parse_truncated"
            assert row.error_type == "LLMResponseTruncated"
            assert "cut off" in (row.error_detail or "")
            assert row.acknowledged is False
            assert row.resolved_at is None

    def test_recording_disabled_without_db_writes(self, app, tmp_path):
        outlook = MagicMock(spec=OutlookClient)
        outlook.fetch_messages.return_value = [_make_msg()]
        monitor = InboxMonitor(
            outlook=outlook,
            provider=MagicMock(),
            poll_interval_seconds=60,
            state_path=tmp_path / "state.json",
            output_dir=tmp_path / "quotes",
            enable_db_writes=False,
            flask_app=app,
        )
        with patch.object(monitor, "_process_message", side_effect=RuntimeError("boom")):
            monitor.run_once()
        with app.app_context():
            assert FailedIntake.query.count() == 0

    def test_recording_failure_does_not_crash_loop(self, app, tmp_path):
        """A bookkeeping failure must never crash the polling loop."""
        outlook = MagicMock(spec=OutlookClient)
        outlook.fetch_messages.return_value = [_make_msg()]
        monitor = _monitor(app, tmp_path, outlook=outlook)

        with patch.object(monitor, "_process_message", side_effect=RuntimeError("boom")):
            with patch("app.extensions.db.session.commit", side_effect=RuntimeError("db down")):
                # Must not raise even though recording the row fails.
                processed = monitor.run_once()

        assert processed == 0
        assert monitor.state.contains("msg-fail-001")


# ---------- acknowledgment ----------


class TestFailureAck:
    def test_ack_off_by_default(self, app, tmp_path):
        outlook = MagicMock(spec=OutlookClient)
        outlook.fetch_messages.return_value = [_make_msg()]
        monitor = _monitor(app, tmp_path, outlook=outlook)  # enable_failure_ack defaults False
        with patch.object(monitor, "_process_message", side_effect=RuntimeError("boom")):
            monitor.run_once()
        outlook.send_mail.assert_not_called()
        with app.app_context():
            assert FailedIntake.query.one().acknowledged is False

    def test_ack_sent_when_enabled(self, app, tmp_path):
        outlook = MagicMock(spec=OutlookClient)
        outlook.fetch_messages.return_value = [_make_msg()]
        monitor = _monitor(app, tmp_path, outlook=outlook, enable_failure_ack=True)
        with patch.object(monitor, "_process_message", side_effect=RuntimeError("boom")):
            monitor.run_once()
        outlook.send_mail.assert_called_once()
        _, kwargs = outlook.send_mail.call_args
        assert kwargs["to_email"] == "buyer@customer.com"
        with app.app_context():
            assert FailedIntake.query.one().acknowledged is True

    def test_ack_skips_own_mailbox(self, app, tmp_path):
        outlook = MagicMock(spec=OutlookClient)
        outlook.fetch_messages.return_value = [_make_msg(sender_email="ops@allanedwards.com")]
        monitor = _monitor(
            app, tmp_path, outlook=outlook,
            enable_failure_ack=True, mailbox_address="ops@allanedwards.com",
        )
        with patch.object(monitor, "_process_message", side_effect=RuntimeError("boom")):
            monitor.run_once()
        outlook.send_mail.assert_not_called()

    def test_ack_skips_configured_internal_domain(self, app, tmp_path):
        outlook = MagicMock(spec=OutlookClient)
        outlook.fetch_messages.return_value = [_make_msg(sender_email="dev@internal.test")]
        monitor = _monitor(
            app, tmp_path, outlook=outlook,
            enable_failure_ack=True, ack_skip_domains=["internal.test"],
        )
        with patch.object(monitor, "_process_message", side_effect=RuntimeError("boom")):
            monitor.run_once()
        outlook.send_mail.assert_not_called()

    def test_ack_skips_noreply_sender(self, app, tmp_path):
        outlook = MagicMock(spec=OutlookClient)
        outlook.fetch_messages.return_value = [_make_msg(sender_email="no-reply@customer.com")]
        monitor = _monitor(app, tmp_path, outlook=outlook, enable_failure_ack=True)
        with patch.object(monitor, "_process_message", side_effect=RuntimeError("boom")):
            monitor.run_once()
        outlook.send_mail.assert_not_called()

    def test_ack_skips_missing_sender(self, app, tmp_path):
        outlook = MagicMock(spec=OutlookClient)
        outlook.fetch_messages.return_value = [_make_msg(sender_email=None)]
        monitor = _monitor(app, tmp_path, outlook=outlook, enable_failure_ack=True)
        with patch.object(monitor, "_process_message", side_effect=RuntimeError("boom")):
            monitor.run_once()
        outlook.send_mail.assert_not_called()


# ---------- admin UI ----------


class TestFailedIntakeAdmin:
    @pytest.fixture()
    def client(self, app):
        with app.app_context():
            user = User(email="staff@allanedwards.com", name="Staff", password_hash="")
            user.set_password("pw")
            db.session.add(user)
            db.session.commit()
        client = app.test_client()
        client.post(
            "/auth/password",
            data={"email": "staff@allanedwards.com", "password": "pw"},
            follow_redirects=True,
        )
        return client

    def _add_row(self, app, **overrides):
        from datetime import datetime

        with app.app_context():
            row = FailedIntake(
                received_at=datetime(2026, 8, 13, 12, 0, 0),
                source_email_id=overrides.get("source_email_id", "msg-x"),
                sender_email=overrides.get("sender_email", "buyer@customer.com"),
                subject=overrides.get("subject", "RFQ - Sleeves"),
                failure_stage=overrides.get("failure_stage", "processing"),
                error_type="RuntimeError",
                error_detail="boom",
                acknowledged=False,
                resolved_at=overrides.get("resolved_at"),
            )
            db.session.add(row)
            db.session.commit()
            return row.id

    def test_page_lists_outstanding(self, app, client):
        self._add_row(app, subject="Needs manual quote")
        resp = client.get("/admin/failed-intakes")
        assert resp.status_code == 200
        assert b"Needs manual quote" in resp.data

    def test_resolve_marks_handled(self, app, client):
        intake_id = self._add_row(app)
        resp = client.post(f"/admin/failed-intakes/{intake_id}/resolve", follow_redirects=True)
        assert resp.status_code == 200
        with app.app_context():
            assert db.session.get(FailedIntake, intake_id).resolved_at is not None

    def test_resolved_hidden_by_default(self, app, client):
        from datetime import datetime

        self._add_row(app, subject="Already handled", resolved_at=datetime(2026, 8, 13, 13, 0, 0))
        default_resp = client.get("/admin/failed-intakes")
        assert b"Already handled" not in default_resp.data
        all_resp = client.get("/admin/failed-intakes?resolved=1")
        assert b"Already handled" in all_resp.data
