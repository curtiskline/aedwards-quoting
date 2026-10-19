"""Quote lifecycle guards (T519, audit T515 H1/H2).

H1: the status endpoint must enforce QUOTE_STATUS_TRANSITIONS — SENT and
REPLACED record completed lifecycle events and cannot be demoted; ARCHIVED
can only go back to review. H2: the send endpoints must refuse REPLACED and
ARCHIVED quotes (deliberate re-send of a SENT quote stays allowed). Auto-send
must never touch a quote that already has a QuoteVersion.
"""

from __future__ import annotations

import os
from decimal import Decimal
from unittest.mock import patch

import pytest

from app import create_app
from app.config import Config
from app.confidence import auto_send_evaluation
from app.extensions import db
from app.models import Quote, QuoteLineItem, QuoteStatus, QuoteVersion, User


def _make_app(db_url, tmp_path):
    os.environ["DATABASE_URL"] = db_url
    Config.SQLALCHEMY_DATABASE_URI = db_url
    app = create_app()
    app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    app.config["TESTING"] = True
    app.config["QUOTE_ARTIFACT_DIR"] = str(tmp_path / "instance" / "quote_versions")
    return app


def _login(client, user_id: int) -> None:
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def _seed_quote(app, status: QuoteStatus, *, with_version: bool = False):
    """A priced, structurally valid quote in the given status."""
    with app.app_context():
        db.create_all()
        user = db.session.query(User).first()
        if user is None:
            user = User(email="test@example.com", name="Test User", password_hash="x")
            db.session.add(user)
            db.session.flush()
        quote = Quote(
            quote_number=f"126-{status.value[:3]}-{db.session.query(Quote).count() + 1}",
            status=status,
            customer_name_raw="Test Customer",
            contact_name="John Doe",
            contact_email="john@example.com",
            ship_to_json={
                "address_line1": "123 Main St",
                "city": "Tulsa",
                "state": "OK",
                "postal_code": "74117",
                "country": "US",
            },
        )
        db.session.add(quote)
        db.session.flush()
        db.session.add(
            QuoteLineItem(
                quote_id=quote.id,
                product_type="pipe",
                description="Test pipe",
                quantity=Decimal("10"),
                unit_price=Decimal("25.00"),
                line_total=Decimal("250.00"),
                sort_order=0,
            )
        )
        if with_version:
            db.session.add(
                QuoteVersion(
                    quote_id=quote.id,
                    version_number=1,
                    pdf_path="/dev/null",
                    artifact_status="retained",
                    sent_to="john@example.com",
                )
            )
        db.session.commit()
        return quote.id, user.id


def _status_of(app, quote_id: int) -> QuoteStatus:
    with app.app_context():
        return db.session.get(Quote, quote_id).status


SEND_ENV = {
    "EMAIL_DELIVERY_ENABLED": "true",
    "O365_EMAIL": "sender@example.com",
    "O365_PASSWORD": "test",
}


def _enable_delivery(monkeypatch):
    for key, value in SEND_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("SEND_EMAIL_ALLOWLIST", raising=False)
    monkeypatch.delenv("ENABLE_OUTLOOK_DRAFTS", raising=False)


# --- H1: status transition table -------------------------------------------


@pytest.mark.parametrize("target", ["in_review", "archived"])
def test_sent_quote_cannot_be_demoted(db_url, tmp_path, target):
    app = _make_app(db_url, tmp_path)
    quote_id, user_id = _seed_quote(app, QuoteStatus.SENT, with_version=True)
    with app.test_client() as client:
        _login(client, user_id)
        resp = client.post(f"/quotes/{quote_id}/status", data={"status": target})
    assert resp.status_code == 409
    assert _status_of(app, quote_id) == QuoteStatus.SENT


@pytest.mark.parametrize("target", ["in_review", "ready", "archived"])
def test_replaced_quote_rejects_every_status_change(db_url, tmp_path, target):
    app = _make_app(db_url, tmp_path)
    quote_id, user_id = _seed_quote(app, QuoteStatus.REPLACED)
    with app.test_client() as client:
        _login(client, user_id)
        resp = client.post(f"/quotes/{quote_id}/status", data={"status": target})
    assert resp.status_code == 409
    assert _status_of(app, quote_id) == QuoteStatus.REPLACED


def test_archived_quote_can_be_unarchived_to_review_only(db_url, tmp_path):
    app = _make_app(db_url, tmp_path)
    quote_id, user_id = _seed_quote(app, QuoteStatus.ARCHIVED)
    with app.test_client() as client:
        _login(client, user_id)
        assert (
            client.post(f"/quotes/{quote_id}/status", data={"status": "ready"}).status_code
            == 409
        )
        assert _status_of(app, quote_id) == QuoteStatus.ARCHIVED
        resp = client.post(f"/quotes/{quote_id}/status", data={"status": "in_review"})
    assert resp.status_code == 200
    assert _status_of(app, quote_id) == QuoteStatus.IN_REVIEW


def test_in_review_quote_keeps_normal_transitions(db_url, tmp_path):
    app = _make_app(db_url, tmp_path)
    quote_id, user_id = _seed_quote(app, QuoteStatus.IN_REVIEW)
    with app.test_client() as client:
        _login(client, user_id)
        assert (
            client.post(f"/quotes/{quote_id}/status", data={"status": "ready"}).status_code
            == 200
        )
        assert _status_of(app, quote_id) == QuoteStatus.READY
        assert (
            client.post(
                f"/quotes/{quote_id}/status", data={"status": "archived"}
            ).status_code
            == 200
        )
    assert _status_of(app, quote_id) == QuoteStatus.ARCHIVED


# --- H2: send endpoints refuse REPLACED/ARCHIVED ----------------------------


@pytest.mark.parametrize("status", [QuoteStatus.REPLACED, QuoteStatus.ARCHIVED])
@patch("allenedwards.outlook.OutlookClient")
def test_send_refuses_replaced_and_archived(
    mock_outlook, db_url, tmp_path, monkeypatch, status
):
    app = _make_app(db_url, tmp_path)
    quote_id, user_id = _seed_quote(app, status)
    _enable_delivery(monkeypatch)
    with app.test_client() as client:
        _login(client, user_id)
        resp = client.post(
            f"/quotes/{quote_id}/send",
            data={"to_email": "devin@918.software", "subject": "s"},
        )
    assert resp.status_code == 409
    mock_outlook.return_value.send_mail.assert_not_called()
    assert _status_of(app, quote_id) == status
    with app.app_context():
        assert db.session.query(QuoteVersion).filter_by(quote_id=quote_id).count() == 0


@pytest.mark.parametrize("status", [QuoteStatus.REPLACED, QuoteStatus.ARCHIVED])
def test_send_form_refuses_replaced_and_archived(db_url, tmp_path, status):
    app = _make_app(db_url, tmp_path)
    quote_id, user_id = _seed_quote(app, status)
    with app.test_client() as client:
        _login(client, user_id)
        resp = client.get(f"/quotes/{quote_id}/send-form")
    html = resp.get_data(as_text=True)
    assert "Send Failed" in html
    assert 'name="to_email"' not in html


@patch("allenedwards.outlook.OutlookClient")
def test_deliberate_resend_of_sent_quote_stays_allowed(
    mock_outlook, db_url, tmp_path, monkeypatch
):
    app = _make_app(db_url, tmp_path)
    quote_id, user_id = _seed_quote(app, QuoteStatus.SENT, with_version=True)
    _enable_delivery(monkeypatch)
    with app.test_client() as client:
        _login(client, user_id)
        resp = client.post(
            f"/quotes/{quote_id}/send",
            data={"to_email": "devin@918.software", "subject": "Re-send"},
        )
    assert "Quote Sent" in resp.get_data(as_text=True)
    mock_outlook.return_value.send_mail.assert_called_once()
    assert _status_of(app, quote_id) == QuoteStatus.SENT
    with app.app_context():
        numbers = [
            v.version_number
            for v in db.session.query(QuoteVersion).filter_by(quote_id=quote_id)
        ]
        assert sorted(numbers) == [1, 2]


# --- auto-send must never re-email a quote with a send record ---------------


def test_auto_send_evaluation_blocks_quote_with_version(db_url, tmp_path):
    app = _make_app(db_url, tmp_path)
    quote_id, _ = _seed_quote(app, QuoteStatus.IN_REVIEW, with_version=True)
    with app.app_context():
        evaluation = auto_send_evaluation(db.session.get(Quote, quote_id))
    assert evaluation["eligible"] is False
    assert any("already has a sent version" in reason for reason in evaluation["reasons"])


def test_edit_routes_skip_auto_send_when_quote_has_version(db_url, tmp_path):
    app = _make_app(db_url, tmp_path)
    quote_id, user_id = _seed_quote(app, QuoteStatus.IN_REVIEW, with_version=True)
    calls = []
    with patch("app.send_service.maybe_auto_send", side_effect=lambda q: calls.append(q.id)):
        with app.test_client() as client:
            _login(client, user_id)
            resp = client.post(
                f"/quotes/{quote_id}/totals", data={"tax_amount": "12.50"}
            )
            assert resp.status_code == 200
    assert calls == []


def test_edit_routes_still_reach_auto_send_without_version(db_url, tmp_path):
    """Control: the guard keys on versions, not on the edit route itself."""
    app = _make_app(db_url, tmp_path)
    quote_id, user_id = _seed_quote(app, QuoteStatus.IN_REVIEW)
    calls = []
    with patch("app.send_service.maybe_auto_send", side_effect=lambda q: calls.append(q.id)):
        with app.test_client() as client:
            _login(client, user_id)
            resp = client.post(
                f"/quotes/{quote_id}/totals", data={"tax_amount": "12.50"}
            )
            assert resp.status_code == 200
    assert calls == [quote_id]


# --- template: demotion buttons hidden for lifecycle-locked statuses --------


def _status_bar_html(app, client, quote_id: int) -> str:
    return client.get(
        f"/quotes/{quote_id}", headers={"HX-Request": "true"}
    ).get_data(as_text=True)


def test_status_bar_hides_demotion_buttons_on_sent_quote(db_url, tmp_path):
    app = _make_app(db_url, tmp_path)
    quote_id, user_id = _seed_quote(app, QuoteStatus.SENT, with_version=True)
    with app.test_client() as client:
        _login(client, user_id)
        html = _status_bar_html(app, client, quote_id)
    assert "Mark In Review" not in html
    assert "Mark Ready" not in html
    assert ">Archive<" not in html
    assert "Re-send Quote" in html
    assert "Revise" in html


def test_status_bar_on_archived_quote_offers_unarchive_but_not_send(db_url, tmp_path):
    app = _make_app(db_url, tmp_path)
    quote_id, user_id = _seed_quote(app, QuoteStatus.ARCHIVED)
    with app.test_client() as client:
        _login(client, user_id)
        html = _status_bar_html(app, client, quote_id)
    assert "Unarchive" in html
    assert "Mark Ready" not in html
    assert "Send Quote" not in html
    assert f"/quotes/{quote_id}/send-form" not in html
