"""Messaging channel interface. WhatsApp and the local simulator implement it; Instagram can be added later."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class InboundMessage:
    phone: str  # digits only
    text: str
    message_id: str
    contact_name: str | None = None
    reply_to_id: str | None = None  # id of the message this one replies to (WhatsApp swipe-reply)
    kind: str = "text"  # text | audio | image | document | other


class SendError(RuntimeError):
    def __init__(self, message: str, outside_window: bool = False):
        super().__init__(message)
        self.outside_window = outside_window


class Channel(Protocol):
    name: str
    # True when free-form messages can only be sent within 24h of the recipient's last message.
    has_service_window: bool

    def send_text(self, phone: str, text: str) -> str:
        """Send a text message; returns the provider message id."""

    def send_document(self, phone: str, path: Path, caption: str = "") -> str:
        """Send a file; returns the provider message id."""

    def send_template(self, phone: str, template: str, language: str, params: list[str]) -> str:
        """Send a pre-approved template message (allowed outside the 24h window)."""
