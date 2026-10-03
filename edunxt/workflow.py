"""Case workflow: routes client and founder messages, runs the agents, delivers drafts.

Case states: AWAITING_PROFILE -> DRAFTING -> IN_REVIEW <-> REVISING -> APPROVED (FAILED on error).
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from typing import Callable

from sqlalchemy import func, select

from .agents.intake import IntakeAgent, welcome_message
from .agents.llm import UsageMeter
from .agents.research import ResearchAgent
from .agents.schemas import CaseDraft, StudentProfile
from .channels.base import Channel, InboundMessage, SendError
from .config import Settings
from .documents.render import render_pdfs
from .store import Case, CaseStatus, Contact, MessageLog, Store, utcnow

log = logging.getLogger(__name__)

INBOUND_JOBS = ("inbound",)
RESEARCH_JOBS = ("draft", "revise")
SERVICE_WINDOW = timedelta(hours=23, minutes=30)  # a little under WhatsApp's 24h, to be safe

CASE_REF = re.compile(r"#?\bEN-?(\d{1,6})\b", re.IGNORECASE)
APPROVE = re.compile(r"^\s*(approve[d]?|lgtm|looks good|all good|ok(ay)?,? send|send it|👍|✅)\W*$", re.IGNORECASE)
LANGUAGE_NAMES = {"en": "English", "ar": "Arabic", "franco": "Franco-Arabic", "mixed": "Mixed", "other": "Other"}

FOUNDER_HELP = """*EduNXT agent commands*
• Reply to a draft (swipe-reply, or start with #EN-0012) with changes in plain words, e.g. "remove Leeds, add 2 Dutch universities under €15k"
• *approve*: mark the case final
• *cases*: list open cases
• *new <student details>*: start a case yourself (e.g. for Instagram enquiries)
• *retry #EN-0012*: re-run a failed draft
• *redo #EN-0012*: re-draft from scratch
• *help*: this message"""


class Workflow:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        channel: Channel,
        intake: IntakeAgent,
        research: ResearchAgent,
        render: Callable[..., tuple[Path, Path]] = render_pdfs,
    ):
        self.settings = settings
        self.store = store
        self.channel = channel
        self.intake = intake
        self.research = research
        self.render = render
        self.founders = settings.founder_numbers

    # ================================================================== entry points

    def receive(self, msg: InboundMessage) -> bool:
        """Log an inbound message and queue it. Returns False for duplicates (webhook retries)."""
        with self.store.session.begin() as s:
            if msg.message_id and s.scalar(select(MessageLog.id).where(MessageLog.wa_id == msg.message_id)):
                return False
            contact = s.get(Contact, msg.phone)
            if contact is None:
                contact = Contact(phone=msg.phone, role="founder" if msg.phone in self.founders else "client")
                s.add(contact)
            contact.last_inbound = utcnow()
            if msg.contact_name:
                contact.name = msg.contact_name
            s.add(MessageLog(wa_id=msg.message_id or None, direction="in", phone=msg.phone, kind=msg.kind, body=msg.text, status="received"))
        self.store.enqueue("inbound", asdict(msg))
        return True

    def run_job(self, kind: str, payload: dict) -> None:
        if kind == "inbound":
            msg = InboundMessage(**payload)
            if msg.phone in self.founders:
                self._handle_founder(msg)
            else:
                self._handle_client(msg)
        elif kind == "draft":
            self._do_draft(payload["case_id"])
        elif kind == "revise":
            self._do_revise(payload["case_id"], payload["instruction"])
        else:
            raise ValueError(f"unknown job kind {kind}")

    def run_pending(self, kinds: tuple[str, ...] = INBOUND_JOBS + RESEARCH_JOBS) -> int:
        """Run queued jobs until none are left (used by the simulator and tests)."""
        count = 0
        while job := self.store.claim_next_job(kinds):
            self._run_claimed(job)
            count += 1
        return count

    def _run_claimed(self, job) -> None:
        try:
            self.run_job(job.kind, job.payload)
            self.store.finish_job(job.id)
        except Exception as exc:
            log.exception("job %s (%s) failed", job.id, job.kind)
            self.store.finish_job(job.id, error=repr(exc))

    # ================================================================== clients

    def _handle_client(self, msg: InboundMessage) -> None:
        meter = UsageMeter()
        with self.store.session() as s:
            case = s.scalars(select(Case).where(Case.client_phone == msg.phone).order_by(Case.id.desc())).first()
            contact = s.get(Contact, msg.phone)
            contact_name = contact.name if contact else msg.contact_name
        first_contact = case is None

        if msg.kind == "text" and msg.text.strip():
            result = self.intake.analyze(msg.text, meter)
            language, intent = result.language, result.intent
            update, summary, translation = result.profile_updates, result.summary_en, result.english_translation
        else:
            language, intent, update = None, "other", StudentProfile()
            summary = f"(sent a {msg.kind} message: please check WhatsApp)"
            translation = msg.text

        with self.store.session.begin() as s:
            if first_contact:
                case = Case(
                    client_phone=msg.phone,
                    founder_phone=self._next_founder(s),
                    language=language,
                    profile=StudentProfile(phone=f"+{msg.phone}").model_dump(),
                )
                s.add(case)
                s.flush()
            else:
                case = s.get(Case, case.id)
            profile = StudentProfile(**case.profile).merge(update)
            if not profile.student_name and contact_name and first_contact:
                profile.notes = (profile.notes + "; " if profile.notes else "") + f"WhatsApp name: {contact_name}"
            case.profile = profile.model_dump()
            case.cost_usd += meter.cost_usd
            start_draft = case.status == CaseStatus.AWAITING_PROFILE and profile.is_complete
            if start_draft:
                case.status = CaseStatus.DRAFTING
            case_id, ref, founder, status = case.id, case.ref, case.founder_phone, case.status
            if msg.message_id:  # attach the logged inbound message to its case
                logged = s.scalars(select(MessageLog).where(MessageLog.wa_id == msg.message_id)).first()
                if logged:
                    logged.case_id = case_id

        replied = False
        if first_contact and intent in ("services_enquiry", "profile_info"):
            self._send(msg.phone, case_id, text=welcome_message(profile, contact_name))
            replied = True

        who = f"+{msg.phone}" + (f" ({contact_name})" if contact_name else "")
        lang = LANGUAGE_NAMES.get(language or "", "n/a")
        if first_contact:
            header = f"🆕 *New enquiry {ref}* from {who} · {lang}"
            footer = "✉️ Welcome + questionnaire sent automatically." if replied else "⚠️ No auto-reply sent (not a services enquiry). Please check."
        else:
            header = f"💬 *{ref}* {who} · {lang}"
            footer = ""
        body = [header, f"_{summary}_"]
        if translation and translation.strip() != summary.strip():
            body.append(f"“{translation.strip()[:1200]}”")
        if footer:
            body.append(footer)
        if start_draft:
            body.append(f"✅ Profile complete:\n{profile.summary()}\n\n⏳ Drafting university + scholarship lists now (usually a few minutes).")
        elif status == CaseStatus.AWAITING_PROFILE and (missing := profile.missing()):
            body.append("Still missing: " + ", ".join(m.replace("_", " ") for m in missing))
        self._send_to_founder(founder, case_id, [("text", "\n\n".join(body))])
        if start_draft:
            self.store.enqueue("draft", {"case_id": case_id})

    def _next_founder(self, s) -> str:
        if not self.founders:
            raise RuntimeError("FOUNDER_NUMBERS is not configured")
        count = s.scalar(select(func.count(Case.id))) or 0
        return self.founders[count % len(self.founders)]

    # ================================================================== founders

    def _handle_founder(self, msg: InboundMessage) -> None:
        founder = msg.phone
        self._flush_pending(founder)
        text = (msg.text or "").strip()
        low = text.lower()
        if not text:
            return
        if low in ("help", "?", "commands", "menu"):
            return self._reply(founder, FOUNDER_HELP)
        if low in ("cases", "status", "list", "open"):
            return self._reply(founder, self._case_list(founder))
        if low.startswith(("new ", "new:", "draft ", "draft:")):
            return self._manual_case(founder, text.split(maxsplit=1)[1].lstrip(": ") if " " in text else "")

        case_id = self._resolve_case(founder, msg)
        if case_id is None:
            return self._reply(
                founder,
                "Which case is this about? Swipe-reply to the draft or start with its number (e.g. #EN-0012).\n\n"
                + self._case_list(founder),
            )
        instruction = CASE_REF.sub("", text).strip(" :-,\n")
        command = instruction.lower()

        with self.store.session.begin() as s:
            case = s.get(Case, case_id)
            status, ref = case.status, case.ref
            busy = status in (CaseStatus.DRAFTING, CaseStatus.REVISING)
            if command in ("retry", "redo", "regenerate", "start over") and not busy:
                case.status = CaseStatus.DRAFTING
                status = "RESTART"
            elif APPROVE.match(instruction) and status == CaseStatus.IN_REVIEW:
                case.status = CaseStatus.APPROVED
                status = "APPROVE"
            elif status == CaseStatus.IN_REVIEW and instruction:
                case.status = CaseStatus.REVISING
                status = "REVISE"
            client = case.client_phone
            profile = StudentProfile(**case.profile)

        if status == "RESTART":
            self.store.enqueue("draft", {"case_id": case_id})
            return self._reply(founder, f"🔁 Re-drafting {ref} from scratch.", case_id)
        if status == "APPROVE":
            to = f" Forward the latest PDFs to the client (+{client})." if client else ""
            return self._reply(founder, f"✅ {ref} approved and closed.{to}", case_id)
        if status == "REVISE":
            self.store.enqueue("revise", {"case_id": case_id, "instruction": instruction})
            return self._reply(founder, f"✏️ On it: revising {ref}. I'll send v-next shortly.", case_id)
        if status in (CaseStatus.DRAFTING, CaseStatus.REVISING):
            return self._reply(founder, f"⏳ {ref} is still being worked on. Send your changes once the draft arrives.", case_id)
        if status == CaseStatus.AWAITING_PROFILE:
            return self._add_profile_details(founder, case_id, instruction)
        if status == CaseStatus.FAILED:
            return self._reply(founder, f"{ref} failed last time. Reply *retry #{ref}* to try again.", case_id)
        if status == CaseStatus.APPROVED:
            return self._reply(founder, f"{ref} is already approved. Reply *redo #{ref}* to start a new draft.", case_id)
        return self._reply(founder, FOUNDER_HELP)

    def _resolve_case(self, founder: str, msg: InboundMessage) -> int | None:
        with self.store.session() as s:
            if match := CASE_REF.search(msg.text or ""):
                case = s.get(Case, int(match.group(1)))
                if case:
                    return case.id
            if msg.reply_to_id:
                case_id = s.scalar(select(MessageLog.case_id).where(MessageLog.wa_id == msg.reply_to_id))
                if case_id:
                    return case_id
            open_cases = s.scalars(
                select(Case.id).where(Case.founder_phone == founder, Case.status.in_(CaseStatus.OPEN))
            ).all()
            in_review = s.scalars(
                select(Case.id).where(Case.founder_phone == founder, Case.status == CaseStatus.IN_REVIEW)
            ).all()
        if len(in_review) == 1:
            return in_review[0]
        if len(open_cases) == 1:
            return open_cases[0]
        return None

    def _case_list(self, founder: str) -> str:
        with self.store.session() as s:
            cases = s.scalars(
                select(Case).where(Case.founder_phone == founder, Case.status.in_(CaseStatus.OPEN)).order_by(Case.id)
            ).all()
            lines = [
                f"• #{c.ref} {StudentProfile(**c.profile).student_name or ('+' + c.client_phone if c.client_phone else 'manual')}"
                f" · {c.status.replace('_', ' ').lower()}" + (f" · v{c.version}" if c.version else "")
                for c in cases
            ]
        return "*Your open cases*\n" + "\n".join(lines) if lines else "You have no open cases."

    def _manual_case(self, founder: str, details: str) -> None:
        if not details:
            return self._reply(founder, "Send *new* followed by the student's details, e.g. \"new Omar, IB 34, wants CS in Germany, budget €10k\".")
        meter = UsageMeter()
        result = self.intake.analyze(details, meter)
        profile = StudentProfile().merge(result.profile_updates)
        with self.store.session.begin() as s:
            case = Case(client_phone=None, founder_phone=founder, language=result.language, profile=profile.model_dump(), cost_usd=meter.cost_usd)
            if profile.is_complete:
                case.status = CaseStatus.DRAFTING
            s.add(case)
            s.flush()
            case_id, ref, status = case.id, case.ref, case.status
        if status == CaseStatus.DRAFTING:
            self.store.enqueue("draft", {"case_id": case_id})
            return self._reply(founder, f"🆕 Created {ref}:\n{profile.summary()}\n\n⏳ Drafting now.", case_id)
        missing = ", ".join(m.replace("_", " ") for m in profile.missing(("intended_course", "countries", "curriculum", "predicted_grades")))
        self._reply(founder, f"🆕 Created {ref}:\n{profile.summary()}\n\nStill need: {missing}. Reply to this message with them.", case_id)

    def _add_profile_details(self, founder: str, case_id: int, details: str) -> None:
        meter = UsageMeter()
        result = self.intake.analyze(details, meter)
        with self.store.session.begin() as s:
            case = s.get(Case, case_id)
            profile = StudentProfile(**case.profile).merge(result.profile_updates)
            case.profile = profile.model_dump()
            case.cost_usd += meter.cost_usd
            ready = profile.is_complete
            if ready:
                case.status = CaseStatus.DRAFTING
            ref = case.ref
        if ready:
            self.store.enqueue("draft", {"case_id": case_id})
            return self._reply(founder, f"✅ {ref} profile complete:\n{profile.summary()}\n\n⏳ Drafting now.", case_id)
        missing = ", ".join(m.replace("_", " ") for m in profile.missing(("intended_course", "countries", "curriculum", "predicted_grades")))
        self._reply(founder, f"Updated {ref}:\n{profile.summary()}\n\nStill need: {missing}.", case_id)

    # ================================================================== research jobs

    def _do_draft(self, case_id: int) -> None:
        with self.store.session() as s:
            case = s.get(Case, case_id)
            profile, founder, ref = StudentProfile(**case.profile), case.founder_phone, case.ref
        meter = UsageMeter()
        try:
            draft = self.research.draft(profile, self.settings.list_size, meter)
        except Exception as exc:
            return self._fail(case_id, meter, f"Drafting {ref} failed: {exc}")
        self._deliver(case_id, draft, meter, headline="draft ready for review")

    def _do_revise(self, case_id: int, instruction: str) -> None:
        with self.store.session() as s:
            case = s.get(Case, case_id)
            profile, current, ref = StudentProfile(**case.profile), CaseDraft(**(case.draft or {})), case.ref
        meter = UsageMeter()
        try:
            draft, summary = self.research.revise(profile, current, instruction, meter)
        except Exception as exc:
            with self.store.session.begin() as s:
                s.get(Case, case_id).status = CaseStatus.IN_REVIEW  # keep the previous version reviewable
            return self._fail(case_id, meter, f"Revising {ref} failed: {exc}. The previous version is unchanged; try rephrasing.", keep_status=True)
        self._deliver(case_id, draft, meter, headline="revised", changes=summary)

    def _fail(self, case_id: int, meter: UsageMeter, text: str, keep_status: bool = False) -> None:
        log.error(text)
        with self.store.session.begin() as s:
            case = s.get(Case, case_id)
            if not keep_status:
                case.status = CaseStatus.FAILED
            case.cost_usd += meter.cost_usd
            founder, ref = case.founder_phone, case.ref
        hint = "" if keep_status else f"\nReply *retry #{ref}* to try again."
        self._send_to_founder(founder, case_id, [("text", f"⚠️ {text}{hint}")])

    def _deliver(self, case_id: int, draft: CaseDraft, meter: UsageMeter, headline: str, changes: str = "") -> None:
        with self.store.session.begin() as s:
            case = s.get(Case, case_id)
            case.version += 1
            case.draft = draft.model_dump()
            case.status = CaseStatus.IN_REVIEW
            case.cost_usd += meter.cost_usd
            profile, founder, ref, version, client = StudentProfile(**case.profile), case.founder_phone, case.ref, case.version, case.client_phone
            total_cost = case.cost_usd
        log.info("%s v%d usage: %s", ref, version, meter.describe())
        unis_pdf, sch_pdf = self.render(profile, draft, self.settings.output_dir / ref, ref, version, client)
        text = founder_summary(ref, version, profile, draft, headline, changes, meter.cost_usd, total_cost)
        self._send_to_founder(
            founder,
            case_id,
            [
                ("text", text),
                ("document", unis_pdf, f"{ref} v{version} · University options"),
                ("document", sch_pdf, f"{ref} v{version} · Scholarships"),
            ],
        )

    # ================================================================== sending

    def _reply(self, founder: str, text: str, case_id: int | None = None) -> None:
        self._send_to_founder(founder, case_id, [("text", text)])

    def _send(self, phone: str, case_id: int | None, text: str | None = None, document: Path | None = None, caption: str = "") -> str:
        if document is not None:
            msg_id = self.channel.send_document(phone, document, caption)
            kind, body = "document", f"{document}|{caption}"
        else:
            msg_id = self.channel.send_text(phone, text or "")
            kind, body = "text", text or ""
        with self.store.session.begin() as s:
            s.add(MessageLog(wa_id=msg_id, direction="out", phone=phone, case_id=case_id, kind=kind, body=body))
        return msg_id

    def _within_window(self, phone: str) -> bool:
        if not self.channel.has_service_window:
            return True
        with self.store.session() as s:
            contact = s.get(Contact, phone)
            return bool(contact and contact.last_inbound and utcnow() - contact.last_inbound < SERVICE_WINDOW)

    def _send_to_founder(self, founder: str, case_id: int | None, items: list[tuple]) -> None:
        """Send texts/documents to a founder, holding them until the founder writes back if the
        WhatsApp 24h window is closed (a template message asks them to reply)."""
        if self._within_window(founder):
            for i, item in enumerate(items):
                try:
                    self._send_item(founder, case_id, item)
                except SendError as exc:
                    if not exc.outside_window:
                        raise
                    return self._hold(founder, case_id, items[i:])
            return
        self._hold(founder, case_id, items)

    def _send_item(self, founder: str, case_id: int | None, item: tuple) -> None:
        if item[0] == "text":
            self._send(founder, case_id, text=item[1])
        else:
            self._send(founder, case_id, document=Path(item[1]), caption=item[2])

    def _hold(self, founder: str, case_id: int | None, items: list[tuple]) -> None:
        with self.store.session.begin() as s:
            for item in items:
                body = item[1] if item[0] == "text" else f"{item[1]}|{item[2]}"
                s.add(MessageLog(direction="out", phone=founder, case_id=case_id, kind=item[0], body=body, status="pending"))
            ref = s.get(Case, case_id).ref if case_id else "update"
        try:
            self.channel.send_template(founder, self.settings.wa_founder_template, self.settings.wa_template_language, [ref])
        except SendError as exc:
            log.error("Could not send template to founder %s: %s", founder, exc)

    def _flush_pending(self, founder: str) -> None:
        with self.store.session.begin() as s:
            pending = s.scalars(
                select(MessageLog).where(MessageLog.phone == founder, MessageLog.status == "pending").order_by(MessageLog.id)
            ).all()
            for row in pending:
                row.status = "flushed"
            items = [(r.kind, r.body, r.case_id) for r in pending]
        for kind, body, case_id in items:
            if kind == "text":
                self._send(founder, case_id, text=body)
            else:
                path, _, caption = body.partition("|")
                self._send(founder, case_id, document=Path(path), caption=caption)


def founder_summary(
    ref: str, version: int, profile: StudentProfile, draft: CaseDraft, headline: str, changes: str, cost: float, total_cost: float
) -> str:
    lines = [f"📋 *{ref} · {profile.student_name or 'Student'}*: v{version} {headline}"]
    if changes:
        lines.append(f"✏️ {changes}")
    lines += ["", profile.summary(), "", "*Universities*"]
    for i, u in enumerate(draft.universities, 1):
        flags = (" ⭐ partner" if u.partner else "") + (f" ⚠️ {u.confidence} confidence" if u.confidence != "high" else "")
        lines.append(f"{i}. {u.university}: {u.fees}{flags}")
        if u.note:
            lines.append(f"   ↳ {u.note}")
        if u.source_url:
            lines.append(f"   {u.source_url}")
    found = sum(1 for r in draft.scholarships if r.scholarships)
    lines += ["", f"🎓 Scholarships found for {found}/{len(draft.universities)} universities."]
    if draft.founder_notes:
        lines += ["", f"📝 {draft.founder_notes}"]
    lines += ["", f"💲 est. ${cost:.2f} this run (${total_cost:.2f} total)", "Reply to this message with changes, or *approve*."]
    text = "\n".join(lines)
    return text if len(text) <= 4000 else text[:3990] + "\n…"


class Worker:
    """Background job runner with two lanes, so client replies never wait behind research."""

    def __init__(self, workflow: Workflow, research_threads: int = 2, poll_seconds: float = 1.0):
        self.workflow = workflow
        self.poll = poll_seconds
        self.research_threads = research_threads
        self._stop = threading.Event()
        self.threads: list[threading.Thread] = []

    def start(self) -> None:
        self.workflow.store.requeue_running()
        lanes = [INBOUND_JOBS] + [RESEARCH_JOBS] * self.research_threads
        for i, kinds in enumerate(lanes):
            t = threading.Thread(target=self._loop, args=(kinds,), name=f"worker-{i}-{kinds[0]}", daemon=True)
            t.start()
            self.threads.append(t)

    def stop(self) -> None:
        self._stop.set()

    def _loop(self, kinds: tuple[str, ...]) -> None:
        while not self._stop.is_set():
            job = self.workflow.store.claim_next_job(kinds)
            if job is None:
                self._stop.wait(self.poll)
                continue
            self.workflow._run_claimed(job)
