"""Tests for PDF preview and send quote workflow."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from app import create_app
from app.config import Config
from app.extensions import db
from app.models import AuditLog, Quote, QuoteLineItem, QuoteStatus, QuoteVersion, User


def _make_app(tmp_path):
    db_path = tmp_path / "pdf-preview-send.db"
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


def _seed_quote(app):
    """Create a user and a quote with line items."""
    with app.app_context():
        db.create_all()
        user = User(email="test@example.com", name="Test User", password_hash="x")
        db.session.add(user)
        quote = Quote(
            quote_number="126-300",
            status=QuoteStatus.READY,
            customer_name_raw="Test Customer",
            contact_name="John Doe",
            contact_email="john@example.com",
            contact_phone="555-1234",
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
                product_type="sleeve",
                description="12\" x 0.250 GR3 10ft Sleeve",
                quantity=10,
                unit_price=150.00,
                line_total=1500.00,
                part_number="HM120253G10",
                specs_json={"diameter": "12", "wall_thickness": "0.250", "grade": "3", "length_ft": "10"},
                sort_order=1,
            )
        )
        db.session.add(
            QuoteLineItem(
                quote_id=quote.id,
                product_type="bag",
                description="GTW Bag 12\"",
                quantity=10,
                unit_price=5.00,
                line_total=50.00,
                part_number="BAG-12",
                specs_json={"diameter": "12"},
                sort_order=2,
            )
        )
        db.session.commit()
        return quote.id, user.id


def test_preview_pdf_returns_pdf(tmp_path):
    app = _make_app(tmp_path)
    quote_id, user_id = _seed_quote(app)

    with app.test_client() as client:
        _login(client, user_id)
        resp = client.get(f"/quotes/{quote_id}/preview-pdf")
        assert resp.status_code == 200
        assert resp.content_type == "application/pdf"
        assert resp.data[:4] == b"%PDF"
        assert b"inline" in resp.headers.get("Content-Disposition", "").encode()


def test_preview_pdf_regenerates_on_each_call(tmp_path):
    app = _make_app(tmp_path)
    quote_id, user_id = _seed_quote(app)

    with app.test_client() as client:
        _login(client, user_id)
        resp1 = client.get(f"/quotes/{quote_id}/preview-pdf")
        resp2 = client.get(f"/quotes/{quote_id}/preview-pdf")
        assert resp1.status_code == 200
        assert resp2.status_code == 200
        # Both should be valid PDFs
        assert resp1.data[:4] == b"%PDF"
        assert resp2.data[:4] == b"%PDF"


def test_send_form_returns_html(tmp_path):
    app = _make_app(tmp_path)
    quote_id, user_id = _seed_quote(app)

    with app.test_client() as client:
        _login(client, user_id)
        resp = client.get(f"/quotes/{quote_id}/send-form")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "send-modal" in html
        assert "john@example.com" in html
        assert "126-300" in html


def test_send_without_o365_creds_shows_error(tmp_path):
    app = _make_app(tmp_path)
    quote_id, user_id = _seed_quote(app)

    with app.test_client() as client:
        _login(client, user_id)
        # Ensure O365 env vars are not set
        os.environ.pop("O365_EMAIL", None)
        os.environ.pop("O365_PASSWORD", None)
        resp = client.post(
            f"/quotes/{quote_id}/send",
            data={"to_email": "customer@example.com", "subject": "Test Quote"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "O365 credentials are not configured" in html


def test_send_without_to_email_returns_400(tmp_path):
    app = _make_app(tmp_path)
    quote_id, user_id = _seed_quote(app)

    with app.test_client() as client:
        _login(client, user_id)
        resp = client.post(
            f"/quotes/{quote_id}/send",
            data={"to_email": "", "subject": "Test Quote"},
        )
        assert resp.status_code == 400


@patch("allenedwards.outlook.OutlookClient")
def test_send_quote_success(mock_outlook_class, tmp_path):
    app = _make_app(tmp_path)
    quote_id, user_id = _seed_quote(app)

    mock_client = MagicMock()
    mock_client.create_draft.return_value = "draft-id-123"
    mock_outlook_class.return_value = mock_client

    with app.test_client() as client:
        _login(client, user_id)
        os.environ["O365_EMAIL"] = "test@allanedwards.com"
        os.environ["O365_PASSWORD"] = "testpass"
        os.environ["ENABLE_OUTLOOK_DRAFTS"] = "false"
        try:
            resp = client.post(
                f"/quotes/{quote_id}/send",
                data={
                    "to_email": "customer@example.com",
                    "subject": "Quote 126-300",
                    "cc_email": "cc@example.com",
                },
            )
        finally:
            os.environ.pop("O365_EMAIL", None)
            os.environ.pop("O365_PASSWORD", None)
            os.environ.pop("ENABLE_OUTLOOK_DRAFTS", None)

        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Quote Sent" in html
        assert "customer@example.com" in html

        # Verify OutlookClient.send_mail was called with PDF attachment
        mock_client.send_mail.assert_called_once()
        call_kwargs = mock_client.send_mail.call_args
        assert call_kwargs.kwargs["to_email"] == "customer@example.com"
        assert call_kwargs.kwargs["cc_email"] == "cc@example.com"
        attachments = call_kwargs.kwargs["attachments"]
        assert len(attachments) == 1
        assert attachments[0][0].endswith(".pdf")
        assert attachments[0][1][:4] == b"%PDF"
        mock_client.create_draft.assert_not_called()

    # Verify quote status changed to Sent
    with app.app_context():
        quote = db.session.get(Quote, quote_id)
        assert quote.status == QuoteStatus.SENT

        # Verify QuoteVersion was created
        versions = db.session.query(QuoteVersion).filter_by(quote_id=quote_id).all()
        assert len(versions) == 1
        assert versions[0].sent_to == "customer@example.com"
        assert versions[0].sent_at is not None

        # Verify AuditLog was created
        logs = db.session.query(AuditLog).filter_by(quote_id=quote_id, action="sent").all()
        assert len(logs) == 1
        assert logs[0].details["to"] == "customer@example.com"
        assert logs[0].details["cc"] == "cc@example.com"


@patch("allenedwards.outlook.OutlookClient")
def test_send_quote_also_creates_draft_when_enabled(mock_outlook_class, tmp_path):
    app = _make_app(tmp_path)
    quote_id, user_id = _seed_quote(app)

    mock_client = MagicMock()
    mock_client.create_draft.return_value = "draft-id-123"
    mock_outlook_class.return_value = mock_client

    with app.test_client() as client:
        _login(client, user_id)
        os.environ["O365_EMAIL"] = "test@allanedwards.com"
        os.environ["O365_PASSWORD"] = "testpass"
        os.environ["ENABLE_OUTLOOK_DRAFTS"] = "true"
        try:
            resp = client.post(
                f"/quotes/{quote_id}/send",
                data={
                    "to_email": "customer@example.com",
                    "subject": "Quote 126-300",
                    "cc_email": "cc@example.com",
                },
            )
        finally:
            os.environ.pop("O365_EMAIL", None)
            os.environ.pop("O365_PASSWORD", None)
            os.environ.pop("ENABLE_OUTLOOK_DRAFTS", None)

    assert resp.status_code == 200
    mock_client.send_mail.assert_called_once()
    mock_client.create_draft.assert_called_once()


def test_editor_has_preview_and_send_buttons(tmp_path):
    app = _make_app(tmp_path)
    quote_id, user_id = _seed_quote(app)

    with app.test_client() as client:
        _login(client, user_id)
        resp = client.get(f"/quotes/{quote_id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Preview PDF" in html
        assert "Send Quote" in html
        assert f"/quotes/{quote_id}/preview-pdf" in html
        assert f"/quotes/{quote_id}/send-form" in html
