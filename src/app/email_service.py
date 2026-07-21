"""Email delivery helpers for authentication flows."""

from __future__ import annotations

import os

from allenedwards.outlook import OutlookAuthError, OutlookClient


class EmailDeliveryError(RuntimeError):
    """Raised when magic-link email delivery fails."""


def send_as_user_enabled() -> bool:
    """Whether the O365_SEND_AS_USER feature flag is on (default off)."""
    return os.getenv("O365_SEND_AS_USER", "").strip().lower() in ("1", "true", "yes")


def resolve_quote_sender(
    user_email: str | None,
    default_sender: str,
    client_secret: str | None,
) -> str:
    """Pick the Graph mailbox address that an outbound quote sends from.

    When O365_SEND_AS_USER is on, quotes go out as the logged-in user so the
    customer's reply lands in their real inbox. Falls back to the shared
    default sender (AEResponder) unless all of these hold:
    - client-credentials auth is configured (ROPC password auth can only
      authenticate the shared mailbox itself, not other users), and
    - the user has an email address, and
    - it is in the same domain as the default sender (the tenant permission
      granted to the app only covers company mailboxes).
    """
    if not send_as_user_enabled():
        return default_sender
    if not client_secret:
        return default_sender
    candidate = (user_email or "").strip()
    if not candidate or "@" not in candidate:
        return default_sender
    default_domain = default_sender.rsplit("@", 1)[-1].lower()
    candidate_domain = candidate.rsplit("@", 1)[-1].lower()
    if candidate_domain != default_domain:
        return default_sender
    return candidate


def send_magic_link_email(*, to_email: str, magic_link: str) -> None:
    """Send a sign-in link through O365 Graph."""
    sender_email = os.getenv("O365_EMAIL")
    sender_password = os.getenv("O365_PASSWORD")
    client_id = os.getenv("O365_CLIENT_ID")
    client_secret = os.getenv("O365_CLIENT_SECRET")
    tenant_id = os.getenv("O365_TENANT_ID")
    scopes_raw = os.getenv("O365_SCOPES", "")

    if not sender_email or (not sender_password and not client_secret):
        raise EmailDeliveryError("O365 credentials are not configured")

    scopes = [scope.strip() for scope in scopes_raw.split(",") if scope.strip()] or None

    client = OutlookClient(
        email_address=sender_email,
        password=sender_password,
        client_id=client_id,
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
