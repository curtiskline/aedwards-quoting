"""Tests for inbox email provider abstractions."""

from __future__ import annotations

from unittest.mock import MagicMock

from allenedwards.email_provider import EmailProvider
from allenedwards.gmail import GmailClient
from allenedwards.outlook import OutlookClient


def test_outlook_implements_email_provider_interface():
    client = OutlookClient(email_address="user@example.com", password="secret")
    assert isinstance(client, EmailProvider)


def test_outlook_backward_compatible_method_aliases():
    client = MagicMock(spec=OutlookClient)
    client.fetch_messages.return_value = []

    result = OutlookClient.list_inbox_messages(client, limit=10, since="2026-04-01T00:00:00Z")

    assert result == []
    client.fetch_messages.assert_called_once_with(limit=10, since="2026-04-01T00:00:00Z")

    OutlookClient.mark_as_read(client, "msg-1")
    client.mark_read.assert_called_once_with("msg-1")


def test_gmail_fetch_messages_maps_payload_and_sorts_ascending():
    api = MagicMock()
    messages_api = api.users.return_value.messages.return_value
    messages_api.list.return_value.execute.return_value = {
        "messages": [{"id": "newer"}, {"id": "older"}]
    }
    get_payloads = [
        {
            "id": "newer",
            "snippet": "newer preview",
            "internalDate": "1712332800000",
            "payload": {
                "mimeType": "multipart/alternative",
                "headers": [
                    {"name": "Subject", "value": "RFQ B"},
                    {"name": "From", "value": "Buyer B <buyerb@example.com>"},
                    {"name": "Message-ID", "value": "<b@example.com>"},
                ],
                "parts": [
                    {
                        "mimeType": "text/plain",
                        "body": {"data": "TmVlZCAyMCBwaXBlcw=="},
                        "filename": "",
                    }
                ],
            },
        },
        {
            "id": "older",
            "snippet": "older preview",
            "internalDate": "1712246400000",
            "payload": {
                "mimeType": "multipart/mixed",
                "headers": [
                    {"name": "Subject", "value": "RFQ A"},
                    {"name": "From", "value": "Buyer A <buyera@example.com>"},
                    {"name": "Message-ID", "value": "<a@example.com>"},
                ],
                "parts": [
                    {
                        "mimeType": "application/pdf",
                        "filename": "rfq.pdf",
                        "body": {"attachmentId": "att-1"},
                    },
                    {
                        "mimeType": "text/plain",
                        "body": {"data": "TmVlZCAxMCBwaXBlcw=="},
                        "filename": "",
                    },
                ],
            },
        },
    ]
    messages_api.get.side_effect = [
        MagicMock(execute=MagicMock(return_value=payload)) for payload in get_payloads
    ]

    client = GmailClient(
        email_address="devin@918.software",
        client_id="id",
        client_secret="secret",
        refresh_token="refresh",
    )
    client._service = api
    messages = client.fetch_messages(limit=25, since="2026-04-05T00:00:00Z")

    messages_api.list.assert_called_once()
    list_kwargs = messages_api.list.call_args.kwargs
    assert list_kwargs["q"].startswith("in:inbox after:")
    assert len(messages) == 2
    assert [m.id for m in messages] == ["older", "newer"]
    assert messages[0].has_attachments is True
    assert messages[0].body_content == "Need 10 pipes"
    assert messages[1].body_content == "Need 20 pipes"


def test_gmail_mark_read_removes_unread_label():
    api = MagicMock()
    messages_api = api.users.return_value.messages.return_value

    client = GmailClient(
        email_address="devin@918.software",
        client_id="id",
        client_secret="secret",
        refresh_token="refresh",
    )
    client._service = api
    client.mark_read("abc123")

    messages_api.modify.assert_called_once_with(
        userId="me",
        id="abc123",
        body={"removeLabelIds": ["UNREAD"]},
    )
