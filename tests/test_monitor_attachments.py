"""Tests for attachment fetching in the Outlook monitor pipeline."""

from __future__ import annotations

import base64
import email
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from allenedwards.monitor import InboxMonitor, _parse_message_to_rfqs
from allenedwards.outlook import OutlookAttachment, OutlookClient, OutlookMessage


def _make_msg(
    *,
    has_attachments: bool = False,
    body: str = "Please quote 10 pcs 6-5/8 x 0.25 GR50 sleeves",
) -> OutlookMessage:
    return OutlookMessage(
        id="msg-001",
        subject="RFQ - Sleeves",
        sender_name="Test User",
        sender_email="test@example.com",
        body_preview=body[:50],
        body_content=body,
        body_content_type="text",
        internet_message_id="<abc@example.com>",
        received_datetime="2026-03-13T12:00:00Z",
        has_attachments=has_attachments,
    )


class FakeProvider:
    """Stub LLM provider that returns minimal valid RFQ JSON."""

    def complete_json(self, prompt: str, system: str = "") -> dict:
        if "Classify" in system or "classifier" in system:
            return {"is_rfq": True, "confidence": 0.95, "reason": "pipe products"}
        return {
            "customer_name": "Test Corp",
            "contact_name": "Test User",
            "contact_email": "test@example.com",
            "contact_phone": None,
            "quote_number": None,
            "quotes": [
                {
                    "project_line": None,
                    "ship_to": {"company": "Test Corp", "city": "Houston", "state": "TX"},
                    "po_number": None,
                    "items": [
                        {
                            "product_type": "sleeve",
                            "quantity": 10,
                            "diameter": "6.625",
                            "wall_thickness": "0.25",
                            "grade": "50",
                            "length_ft": 40,
                            "milling": False,
                            "painting": False,
                            "description": "6-5/8 x 0.25 GR50 sleeve",
                        }
                    ],
                }
            ],
            "urgency": "normal",
            "notes": None,
            "confidence": 0.9,
        }


# ---------- OutlookClient.get_attachments ----------


class TestGetAttachments:
    def test_file_attachment_decoded(self):
        """fileAttachment content bytes are base64-decoded."""
        raw_content = b"PDF file contents here"
        graph_response = {
            "value": [
                {
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "id": "att-1",
                    "name": "rfq.pdf",
                    "contentType": "application/pdf",
                    "contentBytes": base64.b64encode(raw_content).decode(),
                }
            ]
        }

        client = MagicMock(spec=OutlookClient)
        client._request = MagicMock(return_value=graph_response)

        # Call the real method with the mocked _request
        result = OutlookClient.get_attachments(client, "msg-001")

        assert len(result) == 1
        assert result[0].filename == "rfq.pdf"
        assert result[0].content_bytes == raw_content
        assert result[0].content_type == "application/pdf"

    def test_item_attachment_fetched_as_mime(self):
        """itemAttachment triggers a $value fetch for the raw MIME bytes."""
        graph_response = {
            "value": [
                {
                    "@odata.type": "#microsoft.graph.itemAttachment",
                    "id": "att-2",
                    "name": "Forwarded RFQ",
                    "contentType": "message/rfc822",
                }
            ]
        }

        mime_bytes = b"From: someone@example.com\r\nSubject: test\r\n\r\nBody here"

        client = MagicMock(spec=OutlookClient)
        client._request = MagicMock(return_value=graph_response)
        client._request_raw = MagicMock(return_value=mime_bytes)

        result = OutlookClient.get_attachments(client, "msg-001")

        assert len(result) == 1
        assert result[0].content_type == "message/rfc822"
        assert result[0].content_bytes == mime_bytes
        client._request_raw.assert_called_once_with(
            "GET", "/me/messages/msg-001/attachments/att-2/$value"
        )

    def test_empty_attachments(self):
        """No attachments returns empty list."""
        client = MagicMock(spec=OutlookClient)
        client._request = MagicMock(return_value={"value": []})

        result = OutlookClient.get_attachments(client, "msg-001")
        assert result == []


# ---------- Monitor attachment wiring ----------


class TestMonitorAttachmentWiring:
    def test_has_attachments_triggers_fetch(self, tmp_path):
        """Message with has_attachments=True triggers get_attachments call."""
        outlook = MagicMock(spec=OutlookClient)
        outlook.list_inbox_messages.return_value = [_make_msg(has_attachments=True)]
        outlook.get_attachments.return_value = []
        outlook.create_draft.return_value = "draft-1"

        monitor = InboxMonitor(
            outlook=outlook,
            provider=FakeProvider(),
            poll_interval_seconds=60,
            state_path=tmp_path / "state.json",
            output_dir=tmp_path / "quotes",
        )

        with patch("allenedwards.monitor.generate_quote_pdf"):
            with patch.object(Path, "read_bytes", return_value=b"%PDF-fake"):
                monitor.run_once()

        outlook.get_attachments.assert_called_once_with("msg-001")

    def test_no_attachments_skips_fetch(self, tmp_path):
        """Message with has_attachments=False does not call get_attachments."""
        outlook = MagicMock(spec=OutlookClient)
        outlook.list_inbox_messages.return_value = [_make_msg(has_attachments=False)]
        outlook.create_draft.return_value = "draft-1"

        monitor = InboxMonitor(
            outlook=outlook,
            provider=FakeProvider(),
            poll_interval_seconds=60,
            state_path=tmp_path / "state.json",
            output_dir=tmp_path / "quotes",
        )

        with patch("allenedwards.monitor.generate_quote_pdf"):
            with patch.object(Path, "read_bytes", return_value=b"%PDF-fake"):
                monitor.run_once()

        outlook.get_attachments.assert_not_called()


# ---------- _parse_message_to_rfqs attachment integration ----------


class TestParseMessageAttachments:
    def test_rfc822_attachment_added_to_eml(self):
        """message/rfc822 attachment is added as MIME part for the parser."""
        msg = _make_msg()
        embedded_eml = (
            b"From: vendor@example.com\r\n"
            b"Subject: Original RFQ\r\n"
            b"\r\n"
            b"Need 20 pcs 8-5/8 x 0.375 GR65 sleeves"
        )
        attachments = [
            OutlookAttachment(
                filename="Forwarded.eml",
                content_bytes=embedded_eml,
                content_type="message/rfc822",
            )
        ]

        provider = FakeProvider()
        rfqs = _parse_message_to_rfqs(msg, msg.body_content, provider, attachments)

        assert rfqs is not None
        assert len(rfqs) >= 1

    def test_pdf_attachment_added_to_eml(self):
        """PDF attachment is wired as application/pdf MIME part."""
        msg = _make_msg()
        attachments = [
            OutlookAttachment(
                filename="quote-request.pdf",
                content_bytes=b"%PDF-1.4 fake pdf content",
                content_type="application/pdf",
            )
        ]

        provider = FakeProvider()
        rfqs = _parse_message_to_rfqs(msg, msg.body_content, provider, attachments)

        assert rfqs is not None
        assert len(rfqs) >= 1

    def test_no_attachments_still_works(self):
        """Passing empty attachments works the same as before."""
        msg = _make_msg()
        provider = FakeProvider()

        rfqs = _parse_message_to_rfqs(msg, msg.body_content, provider, [])
        assert rfqs is not None

        rfqs2 = _parse_message_to_rfqs(msg, msg.body_content, provider)
        assert rfqs2 is not None
