"""Gmail client for inbox polling using Google OAuth2 refresh tokens."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from email.utils import parseaddr
from typing import Any

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
except ImportError:  # pragma: no cover - tested via integration/dependency install
    Request = None
    Credentials = None
    build = None

from .email_provider import EmailMessage, EmailProvider

GMAIL_API_NAME = "gmail"
GMAIL_API_VERSION = "v1"
DEFAULT_GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


class GmailClient(EmailProvider):
    """Thin Gmail API wrapper for inbox polling operations."""

    def __init__(
        self,
        *,
        email_address: str,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        scopes: list[str] | None = None,
    ):
        self.email_address = email_address
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.scopes = scopes or DEFAULT_GMAIL_SCOPES
        self._service: Any | None = None

    def _build_service(self) -> Any:
        if Credentials is None or Request is None or build is None:
            raise RuntimeError(
                "Gmail dependencies not installed. Install google-api-python-client and google-auth."
            )

        creds = Credentials(
            token=None,
            refresh_token=self.refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=self.client_id,
            client_secret=self.client_secret,
            scopes=self.scopes,
        )
        creds.refresh(Request())
        return build(GMAIL_API_NAME, GMAIL_API_VERSION, credentials=creds, cache_discovery=False)

    @property
    def _api(self) -> Any:
        if self._service is None:
            self._service = self._build_service()
        return self._service

    def fetch_messages(self, limit: int = 25, since: str | None = None) -> list[EmailMessage]:
        query_parts = ["in:inbox"]
        if since:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00")).astimezone(timezone.utc)
            query_parts.append(f"after:{int(since_dt.timestamp())}")
        query = " ".join(query_parts)

        resp = (
            self._api.users()
            .messages()
            .list(userId="me", q=query, maxResults=limit)
            .execute()
        )
        message_refs = resp.get("messages", [])
        messages: list[EmailMessage] = []

        for ref in message_refs:
            item = (
                self._api.users()
                .messages()
                .get(userId="me", id=ref["id"], format="full")
                .execute()
            )
            headers = _header_map(item.get("payload", {}).get("headers", []))
            from_value = headers.get("from", "")
            sender_name, sender_email = parseaddr(from_value)
            body_content, body_content_type = _extract_body(item.get("payload", {}))
            internal_ms = item.get("internalDate")
            received_datetime = None
            if internal_ms:
                try:
                    internal_dt = datetime.fromtimestamp(int(internal_ms) / 1000, tz=timezone.utc)
                    received_datetime = internal_dt.isoformat().replace("+00:00", "Z")
                except (TypeError, ValueError):
                    received_datetime = None

            has_attachments = _payload_has_attachments(item.get("payload", {}))
            messages.append(
                EmailMessage(
                    id=item.get("id", ""),
                    subject=headers.get("subject", ""),
                    sender_name=sender_name or None,
                    sender_email=sender_email or None,
                    body_preview=item.get("snippet", ""),
                    body_content=body_content,
                    body_content_type=body_content_type,
                    internet_message_id=headers.get("message-id"),
                    received_datetime=received_datetime,
                    has_attachments=has_attachments,
                )
            )

        messages.sort(key=lambda m: m.received_datetime or "")
        return messages

    def mark_read(self, message_id: str) -> None:
        (
            self._api.users()
            .messages()
            .modify(userId="me", id=message_id, body={"removeLabelIds": ["UNREAD"]})
            .execute()
        )


def _header_map(headers: list[dict[str, str]]) -> dict[str, str]:
    return {h.get("name", "").lower(): h.get("value", "") for h in headers}


def _payload_has_attachments(payload: dict[str, Any]) -> bool:
    filename = payload.get("filename") or ""
    body = payload.get("body") or {}
    if filename and body.get("attachmentId"):
        return True

    for part in payload.get("parts") or []:
        if _payload_has_attachments(part):
            return True
    return False


def _extract_body(payload: dict[str, Any]) -> tuple[str, str]:
    mime_type = payload.get("mimeType") or "text/plain"
    body = payload.get("body") or {}
    data = body.get("data")
    if data and mime_type.startswith("text/"):
        try:
            decoded = base64.urlsafe_b64decode(data + "===")
            return decoded.decode("utf-8", errors="replace"), mime_type
        except Exception:
            return "", mime_type

    for part in payload.get("parts") or []:
        text, text_type = _extract_body(part)
        if text:
            return text, text_type

    return "", mime_type
