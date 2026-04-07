"""Shared email provider interface for inbox polling."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class EmailMessage:
    """Provider-agnostic inbox message payload."""

    id: str
    subject: str
    sender_name: str | None
    sender_email: str | None
    body_preview: str
    body_content: str
    body_content_type: str
    internet_message_id: str | None
    received_datetime: str | None = None
    has_attachments: bool = False


class EmailProvider(ABC):
    """Abstract interface for polling and acknowledging inbox messages."""

    @abstractmethod
    def fetch_messages(self, limit: int = 25, since: str | None = None) -> list[EmailMessage]:
        """Fetch inbox messages, optionally after a high-water mark."""
        pass

    @abstractmethod
    def mark_read(self, message_id: str) -> None:
        """Mark a message as read/processed."""
        pass
