"""Microsoft Graph Outlook client for mailbox polling and draft creation."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import mimetypes
import re
from typing import Any

import httpx
import msal

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
DEFAULT_CLIENT_ID = "d3590ed6-52b3-4102-aeff-aad2292ab01c"  # MS Office public client
DEFAULT_TIMEOUT_SECONDS = 60.0


@dataclass
class OutlookMessage:
    """Simplified inbox message payload."""

    id: str
    subject: str
    sender_name: str | None
    sender_email: str | None
    body_preview: str
    body_content: str
    body_content_type: str
    internet_message_id: str | None


class OutlookAuthError(RuntimeError):
    """Raised when Microsoft auth fails."""


class OutlookClient:
    """Thin Graph REST wrapper for inbox operations."""

    def __init__(
        self,
        email_address: str,
        password: str,
        client_id: str = DEFAULT_CLIENT_ID,
        scopes: list[str] | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ):
        self.email_address = email_address
        self.password = password
        self.client_id = client_id
        self.scopes = scopes or ["User.Read", "Mail.Read", "Mail.ReadWrite", "Mail.Send"]
        self.timeout_seconds = timeout_seconds
        self._token: str | None = None

    @property
    def _domain(self) -> str:
        match = re.search(r"@([^@]+)$", self.email_address)
        if not match:
            raise OutlookAuthError(f"Invalid O365 email address: {self.email_address}")
        return match.group(1)

    def _discover_tenant(self) -> str:
        """Discover tenant segment from the mailbox domain with fallback."""
        url = f"https://login.microsoftonline.com/{self._domain}/v2.0/.well-known/openid-configuration"
        try:
            response = httpx.get(url, timeout=self.timeout_seconds)
            response.raise_for_status()
            token_endpoint = response.json().get("token_endpoint", "")
            # token_endpoint includes /{tenant-id}/oauth2/v2.0/token
            parts = token_endpoint.split("/")
            if len(parts) >= 4 and parts[3]:
                return parts[3]
        except Exception:
            pass
        # Fallback still works for many org accounts.
        return "organizations"

    def _acquire_token(self) -> str:
        tenant = self._discover_tenant()
        authority = f"https://login.microsoftonline.com/{tenant}"
        app = msal.PublicClientApplication(client_id=self.client_id, authority=authority)

        result = app.acquire_token_by_username_password(
            username=self.email_address,
            password=self.password,
            scopes=self.scopes,
        )
        token = result.get("access_token")
        if token:
            return token

        err = result.get("error_description") or result.get("error") or "unknown auth error"
        raise OutlookAuthError(f"O365 ROPC auth failed: {err}")

    def _auth_headers(self) -> dict[str, str]:
        if not self._token:
            self._token = self._acquire_token()
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = f"{GRAPH_BASE_URL}{path}"
        headers = self._auth_headers()
        custom_headers = kwargs.pop("headers", {})
        headers.update(custom_headers)

        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.request(method, url, headers=headers, **kwargs)

            # Retry once on stale token.
            if response.status_code == 401:
                self._token = self._acquire_token()
                headers = self._auth_headers()
                headers.update(custom_headers)
                response = client.request(method, url, headers=headers, **kwargs)

        response.raise_for_status()
        if not response.content:
            return {}
        return response.json()

    def list_unread_messages(self, limit: int = 25) -> list[OutlookMessage]:
        params = {
            "$filter": "isRead eq false",
            "$orderby": "receivedDateTime asc",
            "$top": str(limit),
            "$select": "id,subject,from,bodyPreview,body,internetMessageId",
        }
        data = self._request("GET", "/me/mailFolders/inbox/messages", params=params)
        messages: list[OutlookMessage] = []

        for item in data.get("value", []):
            sender = item.get("from", {}).get("emailAddress", {})
            body = item.get("body") or {}
            messages.append(
                OutlookMessage(
                    id=item["id"],
                    subject=item.get("subject") or "",
                    sender_name=sender.get("name"),
                    sender_email=sender.get("address"),
                    body_preview=item.get("bodyPreview") or "",
                    body_content=body.get("content") or "",
                    body_content_type=body.get("contentType") or "text",
                    internet_message_id=item.get("internetMessageId"),
                )
            )

        return messages

    def mark_as_read(self, message_id: str) -> None:
        self._request("PATCH", f"/me/messages/{message_id}", json={"isRead": True})

    def move_message(self, message_id: str, destination_folder_id: str) -> str:
        payload = {"destinationId": destination_folder_id}
        data = self._request("POST", f"/me/messages/{message_id}/move", json=payload)
        return data.get("id", "")

    def create_draft(
        self,
        *,
        to_email: str,
        subject: str,
        body_text: str,
        attachments: list[tuple[str, bytes]],
        cc_email: str | None = None,
    ) -> str:
        graph_attachments: list[dict[str, Any]] = []
        for filename, content_bytes in attachments:
            mime_type, _ = mimetypes.guess_type(filename)
            graph_attachments.append(
                {
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "name": filename,
                    "contentType": mime_type or "application/octet-stream",
                    "contentBytes": base64.b64encode(content_bytes).decode("ascii"),
                }
            )

        recipients = [{"emailAddress": {"address": to_email}}]
        cc_recipients = [{"emailAddress": {"address": cc_email}}] if cc_email else []

        payload: dict[str, Any] = {
            "subject": subject,
            "body": {"contentType": "Text", "content": body_text},
            "toRecipients": recipients,
            "attachments": graph_attachments,
        }
        if cc_recipients:
            payload["ccRecipients"] = cc_recipients

        data = self._request("POST", "/me/messages", json=payload)
        return data.get("id", "")

    def get_or_create_folder(self, display_name: str) -> str:
        """Return a mail folder id by display name, creating it under Inbox when missing."""
        data = self._request("GET", "/me/mailFolders/inbox/childFolders", params={"$top": "100"})
        for folder in data.get("value", []):
            if folder.get("displayName", "").lower() == display_name.lower():
                return folder["id"]

        created = self._request(
            "POST",
            "/me/mailFolders/inbox/childFolders",
            json={"displayName": display_name},
        )
        return created["id"]
