"""Operator re-parse of a quote's stored inbound source (T495).

Covers both scoped retry modes: re-running the parser on the stored original
input, and re-parsing operator-corrected text — always into the SAME quote,
with the original snapshot untouched and every attempt recorded append-only.
"""

from __future__ import annotations

import pytest

from allenedwards.providers.mock import MockProvider
from app import create_app
from app.extensions import db
from app.models import (
    AuditLog,
    Quote,
    QuoteAttachment,
    QuoteLineItem,
    QuoteReparseAttempt,
    QuoteSourceSnapshot,
    QuoteStatus,
    User,
)
from app.reparse import (
    latest_parse_confidence,
    parser_input_text,
    quote_allows_reparse,
    reparse_quote,
    source_panel_context,
)

REPARSE_RESPONSE = {
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
    "notes": "Corrected re-read",
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


def _seed_email_quote(*, confidence: float = 0.4, with_attachment: bool = True) -> int:
    """A parser-created quote with its immutable source snapshot (and one CSV)."""
    quote = Quote(
        quote_number="126-900",
        status=QuoteStatus.IN_REVIEW,
        customer_name_raw="Sentinel (misread)",
        contact_name="Wrong Contact",
        contact_email="wrong@example.com",
        sender_email="cedwards@troyco.com",
        sender_name="Chris Edwards",
        subject="RFQ Sentinel 42in Jones Creek",
        po_number="WRONG-PO",
    )
    db.session.add(quote)
    db.session.flush()
    db.session.add(
        QuoteSourceSnapshot(
            quote_id=quote.id,
            original_body="<html><body>Please quote 12 sleeves 42in. Ship to Jones Creek, TX.</body></html>",
            original_body_content_type="html",
            normalized_body_text="Please quote 12 sleeves 42in. Ship to Jones Creek, TX.",
            parsed_rfq_json={"confidence": confidence, "items": [{"quantity": 99}]},
            parser_schema_version=1,
        )
    )
    if with_attachment:
        db.session.add(
            QuoteAttachment(
                quote_id=quote.id,
                filename="rfq.csv",
                content_type="text/csv",
                size_bytes=20,
                is_stored=True,
                content_bytes=b"qty,size\n12,42 inch\n",
            )
        )
    db.session.add(
        QuoteLineItem(
            quote_id=quote.id,
            product_type="sleeve",
            description="Misread 36in sleeve",
            quantity=99,
            unit_price=1.00,
            line_total=99.00,
            sort_order=1,
        )
    )
    db.session.commit()
    return quote.id


def test_parser_input_text_includes_body_and_attachment_text(app):
    quote_id = _seed_email_quote()
    quote = db.session.get(Quote, quote_id)
    text = parser_input_text(quote)
    assert "Please quote 12 sleeves 42in. Ship to Jones Creek, TX." in text
    assert "--- Attachment: rfq.csv ---" in text
    assert "qty | size" in text
    assert "12 | 42 inch" in text
    # The provider-format original (HTML) is not what the parser reads.
    assert "<html>" not in text


def test_reparse_replaces_quote_content_in_place(app):
    quote_id = _seed_email_quote()
    quote = db.session.get(Quote, quote_id)
    original_input = parser_input_text(quote)

    outcome = reparse_quote(quote, original_input, None, provider=MockProvider(REPARSE_RESPONSE))

    assert outcome.ok, outcome.message
    quote = db.session.get(Quote, quote_id)
    # Same quote row, fresh interpretation.
    assert quote.quote_number == "126-900"
    assert quote.customer_name_raw == "Troy Companies"
    assert quote.contact_email == "cedwards@troyco.com"
    assert quote.po_number == "PO-42"
    assert quote.ship_to_json["city"] == "Jones Creek"
    descriptions = [li.description for li in quote.line_items]
    assert not any("Misread" in d for d in descriptions)
    material = [li for li in quote.line_items if li.product_type == "sleeve"]
    assert material and float(material[0].quantity) == 12

    # Unedited original input → recorded as a plain re-run, not an edit.
    attempts = db.session.query(QuoteReparseAttempt).filter_by(quote_id=quote_id).all()
    assert len(attempts) == 1
    assert attempts[0].succeeded is True
    assert attempts[0].input_edited is False
    assert attempts[0].parsed_rfq_json["customer_name"] == "Troy Companies"

    audit_actions = [a.action for a in db.session.query(AuditLog).filter_by(quote_id=quote_id)]
    assert "reparsed_from_source" in audit_actions

    # The original inbound record is untouched.
    snapshot = quote.source_snapshot
    assert snapshot.normalized_body_text == "Please quote 12 sleeves 42in. Ship to Jones Creek, TX."
    assert snapshot.parsed_rfq_json["confidence"] == 0.4


def test_reparse_with_edited_text_flags_edit_and_clears_ready_override(app):
    quote_id = _seed_email_quote()
    quote = db.session.get(Quote, quote_id)
    quote.ready_override_fingerprint = "stale"
    quote.ready_override_by = 1
    db.session.commit()

    outcome = reparse_quote(
        quote,
        "Corrected: 12 sleeves, 42 inch OD, Jones Creek TX",
        None,
        provider=MockProvider(REPARSE_RESPONSE),
    )

    assert outcome.ok
    quote = db.session.get(Quote, quote_id)
    attempt = db.session.query(QuoteReparseAttempt).filter_by(quote_id=quote_id).one()
    assert attempt.input_edited is True
    assert quote.ready_override_fingerprint is None
    assert quote.ready_override_by is None


def test_failed_reparse_changes_nothing_and_records_attempt(app):
    quote_id = _seed_email_quote()
    quote = db.session.get(Quote, quote_id)

    outcome = reparse_quote(quote, "whatever", None, provider=ExplodingProvider())

    assert not outcome.ok
    assert "Nothing on the quote was changed" in outcome.message
    quote = db.session.get(Quote, quote_id)
    assert quote.customer_name_raw == "Sentinel (misread)"
    assert [li.description for li in quote.line_items] == ["Misread 36in sleeve"]
    attempt = db.session.query(QuoteReparseAttempt).filter_by(quote_id=quote_id).one()
    assert attempt.succeeded is False
    assert "RuntimeError" in attempt.error


def test_parse_with_no_items_is_a_recorded_failure(app):
    quote_id = _seed_email_quote()
    quote = db.session.get(Quote, quote_id)
    empty = dict(REPARSE_RESPONSE, items=[])

    outcome = reparse_quote(quote, "not an rfq at all", None, provider=MockProvider(empty))

    assert not outcome.ok
    quote = db.session.get(Quote, quote_id)
    assert quote.customer_name_raw == "Sentinel (misread)"
    attempt = db.session.query(QuoteReparseAttempt).filter_by(quote_id=quote_id).one()
    assert attempt.succeeded is False


def test_lifecycle_locked_and_snapshotless_quotes_cannot_reparse(app):
    quote_id = _seed_email_quote()
    quote = db.session.get(Quote, quote_id)
    quote.status = QuoteStatus.SENT
    db.session.commit()
    assert not quote_allows_reparse(quote)
    outcome = reparse_quote(quote, "text", None, provider=MockProvider(REPARSE_RESPONSE))
    assert not outcome.ok

    manual = Quote(quote_number="126-901", status=QuoteStatus.NEW)
    db.session.add(manual)
    db.session.commit()
    assert not quote_allows_reparse(manual)
    assert source_panel_context(manual) is None


def test_low_confidence_banner_follows_latest_interpretation(app):
    quote_id = _seed_email_quote(confidence=0.4)
    quote = db.session.get(Quote, quote_id)
    assert latest_parse_confidence(quote) == 0.4
    panel = source_panel_context(quote)
    assert panel["low_confidence"] is True

    reparse_quote(
        quote, parser_input_text(quote), None, provider=MockProvider(REPARSE_RESPONSE)
    )
    quote = db.session.get(Quote, quote_id)
    assert latest_parse_confidence(quote) == 0.93
    assert source_panel_context(quote)["low_confidence"] is False


def test_reparse_route_reparses_and_redirects(client, app, monkeypatch):
    import allenedwards.cli as cli

    monkeypatch.setattr(cli, "get_provider", lambda: MockProvider(REPARSE_RESPONSE))
    monkeypatch.setattr(cli, "load_environment", lambda: None)
    quote_id = _seed_email_quote()

    response = client.post(
        f"/quotes/{quote_id}/reparse",
        data={"input_text": "Corrected: 12 sleeves 42in Jones Creek"},
    )
    assert response.status_code == 302

    quote = db.session.get(Quote, quote_id)
    assert quote.customer_name_raw == "Troy Companies"
    detail = client.get(f"/quotes/{quote_id}")
    assert b"Original Email" in detail.data


def test_reparse_route_rejects_sent_quotes_and_empty_text(client, app):
    quote_id = _seed_email_quote()
    quote = db.session.get(Quote, quote_id)
    quote.status = QuoteStatus.SENT
    db.session.commit()
    assert client.post(f"/quotes/{quote_id}/reparse", data={"input_text": "x"}).status_code == 409

    quote.status = QuoteStatus.IN_REVIEW
    db.session.commit()
    empty = client.post(f"/quotes/{quote_id}/reparse", data={"input_text": "  "})
    assert empty.status_code == 302
    assert db.session.query(QuoteReparseAttempt).count() == 0


def test_reparse_attempts_are_append_only(app):
    quote_id = _seed_email_quote()
    quote = db.session.get(Quote, quote_id)
    reparse_quote(quote, "text", None, provider=MockProvider(REPARSE_RESPONSE))
    attempt = db.session.query(QuoteReparseAttempt).filter_by(quote_id=quote_id).one()
    attempt.error = "tampered"
    with pytest.raises(ValueError):
        db.session.commit()
    db.session.rollback()
