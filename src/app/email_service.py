"""Email delivery helpers for authentication flows."""

from __future__ import annotations

import os

from allenedwards.outlook import OutlookAuthError, OutlookClient


class EmailDeliveryError(RuntimeError):
    """Raised when magic-link email delivery fails."""


def send_magic_link_email(*, to_email: str, magic_link: str) -> None:
    """Send a sign-in link through O365 Graph."""
    sender_email = os.getenv("O365_EMAIL")
    sender_password = os.getenv("O365_PASSWORD")
    client_secret = os.getenv("O365_CLIENT_SECRET")
    tenant_id = os.getenv("O365_TENANT_ID")
    scopes_raw = os.getenv("O365_SCOPES", "")

    if not sender_email or (not sender_password and not client_secret):
        raise EmailDeliveryError("O365 credentials are not configured")

    scopes = [scope.strip() for scope in scopes_raw.split(",") if scope.strip()] or None

    client = OutlookClient(
        email_address=sender_email,
        password=sender_password,
        scopes=scopes,
        client_secret=client_secret,
        tenant_id=tenant_id,
    )

    subject = "Your Allan Edwards sign-in link"
    body = (
        "Use this link to sign in to Allan Edwards Quote Manager:\n\n"
        f"{magic_link}\n\n"
        "If you did not request this, you can ignore this email."
    )

    try:
        client.send_mail(to_email=to_email, subject=subject, body_text=body)
    except OutlookAuthError as exc:
        raise EmailDeliveryError(str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive for network/runtime issues
        raise EmailDeliveryError(f"Failed to send email: {exc}") from exc
