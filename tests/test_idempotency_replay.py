"""CP-1 foundation gate: monitor idempotency under replay (task 396).

Proves the whole-message ProcessedInboundEmail ledger end-to-end through
InboxMonitor.run_once:

- Replaying an already-processed message id (even with a lost/fresh state file)
  does NOT create a duplicate quote — the claim raises
  InboundEmailAlreadyProcessed and the retry is a no-op.
- A crash mid-processing, before the claim commits, re-drives the message on
  the next run instead of dropping the RFQ (fail toward reprocessing): nothing
  is marked processed in the DB until the quote rows and claim commit together.
- Canary: with the claim deliberately disabled, the same replay DOES create a
  duplicate — proving these tests can distinguish "idempotent" from "guard
  silently absent".
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app import create_app
from app.config import Config
from app.extensions import db
from app.models import ProcessedInboundEmail, Quote as DBQuote
from allenedwards import db_writer
from allenedwards.monitor import InboxMonitor
from allenedwards.outlook import OutlookMessage


@pytest.fixture()
def app(tmp_path: Path):
    """Create Flask app with a fresh SQLite database per test."""
    db_path = tmp_path / "test.db"
    import os
    previous_database_url = os.environ.get("DATABASE_URL")
    previous_config_database_url = Config.SQLALCHEMY_DATABASE_URI
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path}"
    app = create_app()
    app.config["TESTING"] = True

    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()
    Config.SQLALCHEMY_DATABASE_URI = previous_config_database_url
    if previous_database_url is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = previous_database_url


class _SingleRFQProvider:
    """Stub LLM provider: classifies as RFQ and extracts one priced quote."""

    def complete_json(self, prompt: str, system: str = "") -> dict:
        if "Classify" in system or "classifier" in system:
            return {"is_rfq": True, "confidence": 0.95, "reason": "pipe products"}
        return {
            "customer_name": "Replay Test Corp",
            "contact_name": "Test User",
            "contact_email": "test@example.com",
            "contact_phone": None,
            "quote_number": None,
            "quotes": [
                {
                    "project_line": None,
                    "ship_to": {"company": "Replay Test Corp", "city": "Houston", "state": "TX"},
                    "po_number": None,
                    "items": [
                        {
                            "product_type": "sleeve",
                            "quantity": 10,
                            "diameter": "6.625",
                            "wall_thickness": "0.25",
                            "grade": "50",
                            "length_ft": 40,
                            "milling": False,
                            "painting": False,
                            "description": "6-5/8 x 0.25 GR50 sleeve",
                        }
                    ],
                }
            ],
            "urgency": "normal",
            "notes": None,
            "confidence": 0.9,
        }


MSG_ID = "AAMk-replay-test-1"


def _message() -> OutlookMessage:
    return OutlookMessage(
        id=MSG_ID,
        subject="RFQ - sleeves",
        sender_name="Test User",
        sender_email="test@example.com",
        body_preview="Please quote 10 sleeves",
        body_content="Please quote 10 pcs 6-5/8 x 0.25 GR50 sleeves",
        body_content_type="text",
        internet_message_id="<replay@example.com>",
        received_datetime="2026-08-20T12:00:00Z",
        has_attachments=False,
    )


def _monitor(app, state_path: Path, output_dir: Path) -> InboxMonitor:
    client = MagicMock()
    client.fetch_messages.return_value = [_message()]
    return InboxMonitor(
        outlook=client,
        provider=_SingleRFQProvider(),
        poll_interval_seconds=60,
        state_path=state_path,
        output_dir=output_dir,
        enable_db_writes=True,
        enable_outlook_drafts=False,
        flask_app=app,
    )


def _quote_count(app) -> int:
    with app.app_context():
        return DBQuote.query.filter_by(source_email_id=MSG_ID).count()


def _claim_count(app) -> int:
    with app.app_context():
        return ProcessedInboundEmail.query.filter_by(source_email_id=MSG_ID).count()


def test_replay_after_completed_processing_is_a_noop(app, tmp_path):
    """The same message id replayed after full processing creates no duplicate.

    The replay uses a FRESH state file (simulating a lost/rotated state file or
    a second monitor instance), so only the DB claim stands between the replay
    and a duplicate quote.
    """
    first = _monitor(app, tmp_path / "state1.json", tmp_path / "quotes")
    assert first.run_once() == 1
    assert _quote_count(app) == 1
    assert _claim_count(app) == 1

    replay = _monitor(app, tmp_path / "state2.json", tmp_path / "quotes")
    assert replay.run_once() == 0  # hit InboundEmailAlreadyProcessed → no-op
    assert _quote_count(app) == 1
    assert _claim_count(app) == 1

    # Replay with the SAME state file is also a no-op (state + claim agree).
    assert first.run_once() == 0
    assert _quote_count(app) == 1


def test_crash_before_claim_commit_redrives_message(app, tmp_path):
    """A crash mid-processing fails toward REPROCESSING, never dropping the RFQ.

    Nothing may be marked fully-processed in the DB until the quote rows and
    the claim commit together; the state-file entry alone must trigger a retry
    on the next run.
    """
    state_path = tmp_path / "state.json"
    crashing = _monitor(app, state_path, tmp_path / "quotes")

    with patch.object(
        db_writer, "write_quote_to_db", side_effect=RuntimeError("simulated crash mid-processing")
    ):
        assert crashing.run_once() == 0

    # The crash happened before the claim committed: no quote, no claim, and
    # therefore the message is NOT considered processed by the DB — only the
    # local state file recorded it.
    assert _quote_count(app) == 0
    assert _claim_count(app) == 0
    assert crashing.state.contains(MSG_ID)

    # Next run (same state file): the state-says-processed / no-DB-claim
    # mismatch re-drives the message instead of skipping it.
    retry = _monitor(app, state_path, tmp_path / "quotes")
    assert retry.run_once() == 1
    assert _quote_count(app) == 1
    assert _claim_count(app) == 1


def test_replay_with_claim_disabled_creates_duplicate(app, tmp_path):
    """Canary: with the claim guard absent, the replay DOES duplicate the quote.

    This proves the assertions in test_replay_after_completed_processing_is_a_noop
    are load-bearing: the duplicate-count observable distinguishes a working
    ledger from a silently absent one. If this test ever starts failing with
    only one quote written, the harness (not the guard) is broken.
    """
    real_write = db_writer.write_quote_to_db

    def write_without_claim(*args, **kwargs):
        kwargs["claim_source_email"] = False  # the guard, deliberately removed
        return real_write(*args, **kwargs)

    with patch.object(db_writer, "write_quote_to_db", side_effect=write_without_claim):
        first = _monitor(app, tmp_path / "state1.json", tmp_path / "quotes")
        assert first.run_once() == 1

        replay = _monitor(app, tmp_path / "state2.json", tmp_path / "quotes")
        assert replay.run_once() == 1  # guard absent → replay goes through

    assert _quote_count(app) == 2
    assert _claim_count(app) == 0


def test_startup_warns_when_drafts_are_sole_sink_without_db_writes(tmp_path, caplog):
    """DB-writes-off + drafts-on is the documented droppable-draft config (§12.1)."""
    import logging

    with caplog.at_level(logging.WARNING, logger="allenedwards.monitor"):
        InboxMonitor(
            outlook=MagicMock(),
            provider=MagicMock(),
            poll_interval_seconds=60,
            state_path=tmp_path / "state.json",
            output_dir=tmp_path / "quotes",
            enable_db_writes=False,
            enable_outlook_drafts=True,
        )
    assert any("ENABLE_DB_WRITES" in r.message for r in caplog.records)


def test_no_startup_warning_in_db_writes_config(app, tmp_path, caplog):
    """The prod config (DB writes on, drafts off) must not warn."""
    import logging

    with caplog.at_level(logging.WARNING, logger="allenedwards.monitor"):
        InboxMonitor(
            outlook=MagicMock(),
            provider=MagicMock(),
            poll_interval_seconds=60,
            state_path=tmp_path / "state.json",
            output_dir=tmp_path / "quotes",
            enable_db_writes=True,
            enable_outlook_drafts=False,
            flask_app=app,
        )
    assert not any("ENABLE_DB_WRITES" in r.message for r in caplog.records)
