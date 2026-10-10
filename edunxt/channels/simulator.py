"""Local stand-in for WhatsApp: prints outgoing messages and copies documents to a folder."""

from __future__ import annotations

import itertools
import shutil
from pathlib import Path

_ids = itertools.count(1)


class SimulatorChannel:
    name = "simulator"
    has_service_window = False

    def __init__(self, labels: dict[str, str] | None = None, inbox_dir: Path | None = None, echo: bool = True):
        self.labels = labels or {}
        self.inbox_dir = inbox_dir
        self.echo = echo
        self.sent: list[dict] = []  # inspected by tests

    def _record(self, phone: str, kind: str, body: str) -> str:
        msg_id = f"sim.out.{next(_ids)}"
        self.sent.append({"id": msg_id, "phone": phone, "kind": kind, "body": body})
        if self.echo:
            who = self.labels.get(phone, f"+{phone}")
            indented = "\n".join("   " + line for line in body.splitlines())
            print(f"\n📤 to {who} [{msg_id}]\n{indented}\n")
        return msg_id

    def send_text(self, phone: str, text: str) -> str:
        return self._record(phone, "text", text)

    def send_document(self, phone: str, path: Path, caption: str = "") -> str:
        shown = path
        if self.inbox_dir:
            self.inbox_dir.mkdir(parents=True, exist_ok=True)
            shown = self.inbox_dir / path.name
            if shown.resolve() != path.resolve():
                shutil.copy(path, shown)
        return self._record(phone, "document", f"📎 {shown}" + (f"\n{caption}" if caption else ""))

    def send_template(self, phone: str, template: str, language: str, params: list[str]) -> str:
        return self._record(phone, "template", f"[template {template}] {params}")
