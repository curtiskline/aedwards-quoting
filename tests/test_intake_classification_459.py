"""Intake regressions for task 459 (umbrella: T459 / T437 / T378 fold-in).

Three prod-observed failures on 2026-09-02 / 2026-08-25:

1. John Galt "FW: Checking in" forwards: classify_rfq snippeted the FIRST 500
   chars of the RAW Outlook HTML body — pure <style> CSS — so the classifier
   saw no content and rejected real prospect RFQs (rejected_email 14-23).
2. The reject path wrote no ProcessedInboundEmail claim, so ONE rejected email
   was re-classified every 5-minute poll (9 duplicate rejected rows).
3. The Sable RFQ parsed to zero line items and was skipped with no record
   anywhere (and retried forever) — an invisible intake miss (T437).
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app import create_app
from app.config import Config
from app.extensions import db
from app.models import FailedIntake, ProcessedInboundEmail, RejectedEmail
from allenedwards.monitor import (
    InboxMonitor,
    NoLineItemsExtracted,
    _classify_failure_stage,
    _normalize_body,
)
from allenedwards.outlook import OutlookClient, OutlookMessage
from allenedwards.parser import (
    DEFAULT_RFQ_CLASSIFY_BODY_CHARS,
    classify_rfq,
    html_to_text,
    looks_like_html,
)

# A realistic Outlook/Graph HTML body: over 500 chars of head/style CSS before
# any content, then a prospect reply above a quoted outreach thread.
OUTLOOK_HTML_BODY = """<html><head>
<meta http-equiv="Content-Type" content="text/html; charset=utf-8"><style type="text/css" style="display:none">
<!--
p { margin-top: 0; margin-bottom: 0; }
body { font-family: Calibri, Arial, Helvetica, sans-serif; font-size: 11pt; color: rgb(0,0,0); }
span.EmailStyle17 { font-family: Calibri, sans-serif; color: windowtext; }
.MsoChpDefault { mso-style-type: export-only; font-size: 10.0pt; }
@page WordSection1 { size: 8.5in 11.0in; margin: 1.0in 1.0in 1.0in 1.0in; }
div.WordSection1 { page: WordSection1; }
-->
</style></head>
<body dir="ltr">
<div class="elementToProof" style="font-family: Calibri; font-size: 11pt;">
Thanks for reaching out. Yes, we actually have a need right now &#8212; please
quote the following for the Griffith-Whiting ILI repairs:</div>
<div><br></div>
<div>Qty 4 &#8212; 24&quot; ID x 0.500&quot; w/t Gr 50 tight sleeves, 10 ft</div>
<div>Qty 2 &#8212; 20&quot; ID x 0.375&quot; w/t Gr 50 half soles</div>
<div><br></div>
<div>Jose Pedraza<br>BP Pipelines North America</div>
<hr>
<div id="divRplyFwdMsg"><font face="Calibri" color="#000000"><b>From:</b> John Galt
&lt;jgalt@allanedwards.com&gt;<br><b>Sent:</b> Tuesday, September 1, 2026<br>
<b>Subject:</b> Checking in</font><div>&nbsp;</div></div>
<div>Just checking in to see if you have any pipeline repair needs this quarter.</div>
</body></html>"""


class _CapturingProvider:
    """Stub provider that records the classify prompt and returns a canned verdict."""

    def __init__(self, result: dict):
        self.result = result
        self.prompts: list[str] = []

    def complete_json(self, prompt: str, system: str = "") -> dict:
        self.prompts.append(prompt)
        return self.result


# ---------- html_to_text ----------


class TestHtmlToText:
    def test_outlook_html_is_detected(self):
        assert looks_like_html(OUTLOOK_HTML_BODY)
        assert not looks_like_html("Please quote 10 pcs 6-5/8 x 0.25 GR50 sleeves")

    def test_css_is_removed_and_content_kept(self):
        text = html_to_text(OUTLOOK_HTML_BODY)
        assert "font-family" not in text
        assert "WordSection1" not in text
        assert "tight sleeves" in text
        assert "Jose Pedraza" in text

    def test_line_structure_survives(self):
        text = html_to_text(OUTLOOK_HTML_BODY)
        lines = [line for line in text.splitlines() if line.strip()]
        # The two spec lines must stay on separate lines, not run together.
        sleeve_lines = [l for l in lines if "Gr 50" in l]
        assert len(sleeve_lines) == 2


# ---------- classify_rfq body handling ----------


class TestClassifySnippet:
    def test_classifier_sees_text_not_css(self):
        provider = _CapturingProvider({"is_rfq": True, "confidence": 0.9, "reason": ""})
        classify_rfq("FW: Checking in", OUTLOOK_HTML_BODY, provider)
        prompt = provider.prompts[0]
        assert "font-family" not in prompt
        assert "tight sleeves" in prompt

    def test_snippet_window_is_wide_enough_for_forwarded_threads(self):
        # 500 chars was not enough to get past forwarded headers/greetings.
        assert DEFAULT_RFQ_CLASSIFY_BODY_CHARS >= 2000

    def test_plain_text_body_is_passed_through(self):
        provider = _CapturingProvider({"is_rfq": True, "confidence": 0.9, "reason": ""})
        classify_rfq("RFQ", "Please quote 10 pcs 6-5/8 sleeves", provider)
        assert "Please quote 10 pcs" in provider.prompts[0]


# ---------- monitor body normalization ----------


class TestNormalizeBody:
    def test_html_content_type_is_converted(self):
        text = _normalize_body(OUTLOOK_HTML_BODY, "preview", "html")
        assert "font-family" not in text
        assert "tight sleeves" in text

    def test_html_without_content_type_is_still_converted(self):
        text = _normalize_body(OUTLOOK_HTML_BODY, "preview", None)
        assert "font-family" not in text

    def test_plain_text_unchanged(self):
        assert _normalize_body("quote please", "", "text") == "quote please"

    def test_empty_content_falls_back_to_preview(self):
        assert _normalize_body("", "preview text", "html") == "preview text"


# ---------- reject / no-line-items idempotency ----------


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


MSG_ID = "AAMk-checking-in-forward-1"


def _make_msg(**overrides) -> OutlookMessage:
    defaults = dict(
        id=MSG_ID,
        subject="FW: Checking in",
        sender_name="John Galt",
        sender_email="jgalt@allanedwards.com",
        body_preview="Thanks for reaching out",
        body_content=OUTLOOK_HTML_BODY,
        body_content_type="html",
        internet_message_id="<galt@allanedwards.com>",
        received_datetime="2026-09-02T21:18:21Z",
        has_attachments=False,
    )
    defaults.update(overrides)
    return OutlookMessage(**defaults)


def _monitor(app, tmp_path: Path, provider, msg: OutlookMessage) -> InboxMonitor:
    outlook = MagicMock(spec=OutlookClient)
    outlook.fetch_messages.return_value = [msg]
    return InboxMonitor(
        outlook=outlook,
        provider=provider,
        poll_interval_seconds=60,
        state_path=tmp_path / "state.json",
        output_dir=tmp_path / "quotes",
        enable_db_writes=True,
        enable_outlook_drafts=False,
        flask_app=app,
    )


class TestRejectPathClaims:
    def test_rejected_email_is_not_reprocessed_every_poll(self, app, tmp_path):
        """One genuine non-RFQ produces ONE rejected row and ONE claim.

        Prod evidence: rejected_email rows 14-22 were the SAME email
        re-classified every 5 minutes because the reject path wrote no claim.
        """
        provider = _CapturingProvider(
            {"is_rfq": False, "confidence": 0.95, "reason": "newsletter"}
        )
        monitor = _monitor(app, tmp_path, provider, _make_msg())

        assert monitor.run_once() == 0
        assert monitor.run_once() == 0  # second poll must be a pure no-op

        with app.app_context():
            assert RejectedEmail.query.count() == 1
            assert (
                ProcessedInboundEmail.query.filter_by(source_email_id=MSG_ID).count() == 1
            )
        # Only one classify call ever reached the LLM.
        assert len(provider.prompts) == 1

    def test_reject_claim_is_reversible(self, app, tmp_path):
        """Deleting the claim (replay flow) lets the message process again."""
        provider = _CapturingProvider(
            {"is_rfq": False, "confidence": 0.95, "reason": "newsletter"}
        )
        monitor = _monitor(app, tmp_path, provider, _make_msg())
        monitor.run_once()

        with app.app_context():
            ProcessedInboundEmail.query.filter_by(source_email_id=MSG_ID).delete()
            db.session.commit()

        monitor.run_once()
        assert len(provider.prompts) == 2  # re-classified after claim removal


class _NoItemsProvider:
    """Classifies as RFQ but parses to zero line items (the Sable failure)."""

    def __init__(self):
        self.calls = 0

    def complete_json(self, prompt: str, system: str = "") -> dict:
        self.calls += 1
        if "classifier" in system or "Classify" in system:
            return {"is_rfq": True, "confidence": 0.9, "reason": ""}
        return {
            "customer_name": "Sable Offshore",
            "contact_name": None,
            "contact_email": "rgomez@sableoffshore.com",
            "quotes": [{"ship_to": None, "items": []}],
            "urgency": "normal",
            "confidence": 0.4,
        }


class TestNoLineItemsPath:
    def test_stage_mapping(self):
        assert _classify_failure_stage(NoLineItemsExtracted("none")) == "no_line_items"

    def test_no_line_items_records_failed_intake_and_claims(self, app, tmp_path):
        provider = _NoItemsProvider()
        monitor = _monitor(app, tmp_path, provider, _make_msg())

        assert monitor.run_once() == 0
        first_calls = provider.calls
        assert monitor.run_once() == 0

        with app.app_context():
            rows = FailedIntake.query.all()
            assert len(rows) == 1
            assert rows[0].failure_stage == "no_line_items"
            assert rows[0].source_email_id == MSG_ID
            assert rows[0].resolved_at is None
            assert (
                ProcessedInboundEmail.query.filter_by(source_email_id=MSG_ID).count() == 1
            )
        # Second poll made no further LLM calls.
        assert provider.calls == first_calls

    def test_transient_failure_still_retries(self, app, tmp_path):
        """OperationalError-style quarantines must NOT claim — retry is wanted."""

        class _ExplodingProvider:
            def complete_json(self, prompt: str, system: str = "") -> dict:
                raise RuntimeError("db went away")

        monitor = _monitor(app, tmp_path, _ExplodingProvider(), _make_msg())
        monitor.run_once()
        with app.app_context():
            assert FailedIntake.query.count() == 1
            assert (
                ProcessedInboundEmail.query.filter_by(source_email_id=MSG_ID).count() == 0
            )
