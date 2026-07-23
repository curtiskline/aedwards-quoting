"""Tests for sending quotes as the logged-in user's O365 address (O365_SEND_AS_USER)."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from app import create_app
from app.config import Config
from app.email_service import resolve_quote_sender
from app.extensions import db
from app.models import AuditLog, Quote, QuoteLineItem, QuoteStatus, User

DEFAULT_SENDER = "AEResponder@allanedwards.com"


# --- resolve_quote_sender unit tests ---


def test_flag_off_uses_default_sender(monkeypatch):
    monkeypatch.delenv("O365_SEND_AS_USER", raising=False)
    assert resolve_quote_sender("chip@allanedwards.com", DEFAULT_SENDER, "secret") == DEFAULT_SENDER


def test_flag_off_explicit_false(monkeypatch):
    monkeypatch.setenv("O365_SEND_AS_USER", "false")
    assert resolve_quote_sender("chip@allanedwards.com", DEFAULT_SENDER, "secret") == DEFAULT_SENDER


def test_flag_on_sends_as_user(monkeypatch):
    monkeypatch.setenv("O365_SEND_AS_USER", "true")
    assert (
        resolve_quote_sender("chip@allanedwards.com", DEFAULT_SENDER, "secret")
        == "chip@allanedwards.com"
    )


def test_flag_on_without_client_secret_falls_back(monkeypatch):
    """ROPC password auth can only authenticate the shared mailbox itself."""
    monkeypatch.setenv("O365_SEND_AS_USER", "true")
    assert resolve_quote_sender("chip@allanedwards.com", DEFAULT_SENDER, None) == DEFAULT_SENDER


def test_flag_on_without_user_email_falls_back(monkeypatch):
    monkeypatch.setenv("O365_SEND_AS_USER", "true")
    assert resolve_quote_sender(None, DEFAULT_SENDER, "secret") == DEFAULT_SENDER
    assert resolve_quote_sender("", DEFAULT_SENDER, "secret") == DEFAULT_SENDER
    assert resolve_quote_sender("   ", DEFAULT_SENDER, "secret") == DEFAULT_SENDER


def test_flag_on_with_external_domain_falls_back(monkeypatch):
    """Tenant permission only covers company mailboxes."""
    monkeypatch.setenv("O365_SEND_AS_USER", "true")
    assert resolve_quote_sender("someone@gmail.com", DEFAULT_SENDER, "secret") == DEFAULT_SENDER


def test_flag_on_domain_match_is_case_insensitive(monkeypatch):
    monkeypatch.setenv("O365_SEND_AS_USER", "true")
    assert (
        resolve_quote_sender("Chip@AllanEdwards.com", DEFAULT_SENDER, "secret")
        == "Chip@AllanEdwards.com"
    )


def test_flag_on_invalid_email_falls_back(monkeypatch):
    monkeypatch.setenv("O365_SEND_AS_USER", "true")
    assert resolve_quote_sender("not-an-email", DEFAULT_SENDER, "secret") == DEFAULT_SENDER


# --- Send route integration tests (mocked Graph client, no real sends) ---


def _make_app(tmp_path):
    db_path = tmp_path / "send-as-user.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path}"
    app = create_app()
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
    app.config["TESTING"] = True
    return app


def _login(client, user_id: int) -> None:
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def _seed_quote(app, user_email: str):
    with app.app_context():
        db.create_all()
        user = User(email=user_email, name="Chip Edwards", password_hash="x")
        db.session.add(user)
        quote = Quote(
            quote_number="126-400",
            status=QuoteStatus.READY,
            customer_name_raw="Test Customer",
            contact_name="John Doe",
            contact_email="john@example.com",
        )
        db.session.add(quote)
        db.session.flush()
        db.session.add(
            QuoteLineItem(
                quote_id=quote.id,
                product_type="sleeve",
                description='12" x 0.250 GR3 10ft Sleeve',
                quantity=1,
                unit_price=150.00,
                line_total=150.00,
                part_number="HM120253G10",
                sort_order=1,
            )
        )
        db.session.commit()
        return quote.id, user.id


def _o365_env(monkeypatch, *, send_as_user: str | None):
    monkeypatch.setenv("O365_EMAIL", DEFAULT_SENDER)
    monkeypatch.setenv("O365_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("O365_TENANT_ID", "test-tenant")
    monkeypatch.delenv("O365_PASSWORD", raising=False)
    monkeypatch.setenv("ENABLE_OUTLOOK_DRAFTS", "false")
    if send_as_user is None:
        monkeypatch.delenv("O365_SEND_AS_USER", raising=False)
    else:
        monkeypatch.setenv("O365_SEND_AS_USER", send_as_user)


def _post_send(app, quote_id, user_id):
    with app.test_client() as client:
        _login(client, user_id)
        return client.post(
            f"/quotes/{quote_id}/send",
            data={"to_email": "customer@example.com", "subject": "Quote 126-400"},
        )


@patch("allenedwards.outlook.OutlookClient")
def test_send_flag_off_uses_shared_mailbox(mock_outlook_class, tmp_path, monkeypatch):
    app = _make_app(tmp_path)
    quote_id, user_id = _seed_quote(app, "chip@allanedwards.com")
    mock_outlook_class.return_value = MagicMock()
    _o365_env(monkeypatch, send_as_user=None)

    resp = _post_send(app, quote_id, user_id)

    assert resp.status_code == 200
    assert "Quote Sent" in resp.data.decode()
    assert mock_outlook_class.call_args.kwargs["email_address"] == DEFAULT_SENDER


@patch("allenedwards.outlook.OutlookClient")
def test_send_flag_on_uses_logged_in_users_mailbox(mock_outlook_class, tmp_path, monkeypatch):
    app = _make_app(tmp_path)
    quote_id, user_id = _seed_quote(app, "chip@allanedwards.com")
    mock_client = MagicMock()
    mock_outlook_class.return_value = mock_client
    _o365_env(monkeypatch, send_as_user="true")

    resp = _post_send(app, quote_id, user_id)

    assert resp.status_code == 200
    assert "Quote Sent" in resp.data.decode()
    assert mock_outlook_class.call_args.kwargs["email_address"] == "chip@allanedwards.com"
    mock_client.send_mail.assert_called_once()

    # Audit log records the actual from address
    with app.app_context():
        log = db.session.query(AuditLog).filter_by(quote_id=quote_id, action="sent").one()
        assert log.details["from"] == "chip@allanedwards.com"


@patch("allenedwards.outlook.OutlookClient")
def test_send_flag_on_uses_session_user_not_first_database_user(
    mock_outlook_class, tmp_path, monkeypatch
):
    app = _make_app(tmp_path)
    with app.app_context():
        db.create_all()
        db.session.add(User(email="devin@918.software", name="Devin", password_hash="x"))
        db.session.commit()
    quote_id, user_id = _seed_quote(app, "chip@allanedwards.com")
    mock_outlook_class.return_value = MagicMock()
    _o365_env(monkeypatch, send_as_user="true")

    resp = _post_send(app, quote_id, user_id)

    assert resp.status_code == 200
    assert "Quote Sent" in resp.data.decode()
    assert mock_outlook_class.call_args.kwargs["email_address"] == "chip@allanedwards.com"
    with app.app_context():
        log = db.session.query(AuditLog).filter_by(quote_id=quote_id, action="sent").one()
        assert log.user_id == user_id
        assert log.details["from"] == "chip@allanedwards.com"


@patch("allenedwards.outlook.OutlookClient")
def test_send_flag_on_external_user_falls_back_to_shared_mailbox(
    mock_outlook_class, tmp_path, monkeypatch
):
    app = _make_app(tmp_path)
    quote_id, user_id = _seed_quote(app, "devin@918.software")
    mock_outlook_class.return_value = MagicMock()
    _o365_env(monkeypatch, send_as_user="true")

    resp = _post_send(app, quote_id, user_id)

    assert resp.status_code == 200
    assert "Quote Sent" in resp.data.decode()
    assert mock_outlook_class.call_args.kwargs["email_address"] == DEFAULT_SENDER

    with app.app_context():
        log = db.session.query(AuditLog).filter_by(quote_id=quote_id, action="sent").one()
        assert log.details["from"] == DEFAULT_SENDER


@patch("allenedwards.outlook.OutlookClient")
def test_send_flag_on_password_auth_falls_back_to_shared_mailbox(
    mock_outlook_class, tmp_path, monkeypatch
):
    app = _make_app(tmp_path)
    quote_id, user_id = _seed_quote(app, "chip@allanedwards.com")
    mock_outlook_class.return_value = MagicMock()
    _o365_env(monkeypatch, send_as_user="true")
    monkeypatch.delenv("O365_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("O365_PASSWORD", "testpass")

    resp = _post_send(app, quote_id, user_id)

    assert resp.status_code == 200
    assert "Quote Sent" in resp.data.decode()
    assert mock_outlook_class.call_args.kwargs["email_address"] == DEFAULT_SENDER
