"""Safety checks for environments that must not send external email."""

from __future__ import annotations

import pytest

from app.email_service import EmailDeliveryError, email_delivery_enabled, send_magic_link_email


def test_email_delivery_gate_blocks_magic_links_before_credentials(monkeypatch):
    monkeypatch.setenv("EMAIL_DELIVERY_ENABLED", "false")
    monkeypatch.setenv("O365_EMAIL", "responder@allanedwards.com")
    monkeypatch.setenv("O365_PASSWORD", "live-password-must-not-be-used")

    assert email_delivery_enabled() is False
    with pytest.raises(EmailDeliveryError, match="disabled in this environment"):
        send_magic_link_email(to_email="customer@example.com", magic_link="https://example.com/login")
