"""WhatsApp Cloud API channel (Meta Graph API)."""

from __future__ import annotations

import hashlib
import hmac
import logging
import mimetypes
from pathlib import Path

import httpx

from .base import InboundMessage, SendError

log = logging.getLogger(__name__)

GRAPH = "https://graph.facebook.com"
OUTSIDE_WINDOW_CODES = {131047}  # "Re-engagement message": more than 24h since the user last wrote


def verify_signature(app_secret: str, body: bytes, header: str | None) -> bool:
    """Check Meta's X-Hub-Signature-256 header against the raw request body."""
    if not app_secret:
        return True  # signature checking disabled (local testing only)
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def parse_webhook(payload: dict) -> list[InboundMessage]:
    """Extract user messages from a WhatsApp webhook payload; delivery statuses are ignored."""
    messages = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            names = {c.get("wa_id"): c.get("profile", {}).get("name") for c in value.get("contacts", [])}
            for msg in value.get("messages", []):
                kind = msg.get("type", "other")
                if kind == "text":
                    text = msg.get("text", {}).get("body", "")
                elif kind == "button":
                    text = msg.get("button", {}).get("text", "")
                    kind = "text"
                elif kind == "interactive":
                    inter = msg.get("interactive", {})
                    text = (inter.get("button_reply") or inter.get("list_reply") or {}).get("title", "")
                    kind = "text"
                elif kind in ("image", "document", "video"):
                    text = msg.get(kind, {}).get("caption", "")
                else:
                    text = ""
                messages.append(
                    InboundMessage(
                        phone=msg.get("from", ""),
                        text=text,
                        message_id=msg.get("id", ""),
                        contact_name=names.get(msg.get("from")),
                        reply_to_id=(msg.get("context") or {}).get("id"),
                        kind=kind if kind in ("text", "audio", "image", "document") else "other",
                    )
                )
    return messages


class WhatsAppChannel:
    name = "whatsapp"
    has_service_window = True

    def __init__(self, phone_number_id: str, access_token: str, api_version: str = "v23.0", http: httpx.Client | None = None):
        self.phone_number_id = phone_number_id
        self.base = f"{GRAPH}/{api_version}"
        self.http = http or httpx.Client(timeout=60, headers={"Authorization": f"Bearer {access_token}"})

    def _post_message(self, body: dict) -> str:
        resp = self.http.post(f"{self.base}/{self.phone_number_id}/messages", json={"messaging_product": "whatsapp", **body})
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400 or "error" in data:
            err = data.get("error", {})
            code = err.get("code")
            raise SendError(f"WhatsApp error {code}: {err.get('message', resp.text)}", outside_window=code in OUTSIDE_WINDOW_CODES)
        return data["messages"][0]["id"]

    def send_text(self, phone: str, text: str) -> str:
        # WhatsApp caps text bodies at 4096 characters.
        return self._post_message({"to": phone, "type": "text", "text": {"body": text[:4096], "preview_url": False}})

    def upload_media(self, path: Path) -> str:
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        with path.open("rb") as fh:
            resp = self.http.post(
                f"{self.base}/{self.phone_number_id}/media",
                data={"messaging_product": "whatsapp", "type": mime},
                files={"file": (path.name, fh, mime)},
            )
        data = resp.json()
        if resp.status_code >= 400 or "id" not in data:
            raise SendError(f"WhatsApp media upload failed: {data}")
        return data["id"]

    def send_document(self, phone: str, path: Path, caption: str = "") -> str:
        media_id = self.upload_media(path)
        return self._post_message(
            {"to": phone, "type": "document", "document": {"id": media_id, "filename": path.name, "caption": caption[:1024]}}
        )

    def send_template(self, phone: str, template: str, language: str, params: list[str]) -> str:
        components = [{"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}] if params else []
        return self._post_message(
            {"to": phone, "type": "template", "template": {"name": template, "language": {"code": language}, "components": components}}
        )

    def phone_number_info(self) -> dict:
        resp = self.http.get(f"{self.base}/{self.phone_number_id}", params={"fields": "display_phone_number,verified_name,quality_rating"})
        data = resp.json()
        if resp.status_code >= 400:
            raise SendError(str(data.get("error", data)))
        return data
