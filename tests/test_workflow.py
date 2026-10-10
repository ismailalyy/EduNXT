from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from edunxt.channels.base import InboundMessage
from edunxt.store import Case, CaseStatus, Contact, MessageLog, utcnow
from edunxt.workflow import Workflow

from .conftest import CLIENT, FOUNDER, FOUNDER_2, FakeIntake, FakeResearch, fake_render

_n = 0


def send(workflow, phone, text, reply_to=None):
    global _n
    _n += 1
    workflow.receive(InboundMessage(phone=phone, text=text, message_id=f"wamid.test.{_n}", reply_to_id=reply_to))
    workflow.run_pending()


def to(channel, phone):
    return [m for m in channel.sent if m["phone"] == phone]


def case(workflow, case_id=1) -> Case:
    with workflow.store.session() as s:
        return s.get(Case, case_id)


def test_full_happy_path(workflow, channel):
    # 1. First enquiry in Franco-Arabic: client gets the English welcome, founder gets notified.
    send(workflow, CLIENT, "salam, 3ayez a3raf 3an el services beta3etkom")
    client_msgs = to(channel, CLIENT)
    assert len(client_msgs) == 1
    assert "EduNXT" in client_msgs[0]["body"] and "free of charge" in client_msgs[0]["body"]
    assert "curriculum" in client_msgs[0]["body"].lower()
    founder_msgs = to(channel, FOUNDER)
    assert "New enquiry EN-0001" in founder_msgs[0]["body"]
    assert "Franco-Arabic" in founder_msgs[0]["body"]
    assert case(workflow).status == CaseStatus.AWAITING_PROFILE

    # 2. Client sends their profile: drafting runs, founder receives summary + 2 PDFs.
    send(workflow, CLIENT, "I'm Layla, IB predicted 36, psychology in the UK")
    c = case(workflow)
    assert c.status == CaseStatus.IN_REVIEW and c.version == 1
    founder_msgs = to(channel, FOUNDER)
    docs = [m for m in founder_msgs if m["kind"] == "document"]
    assert len(docs) == 2
    summary = [m for m in founder_msgs if "v1 draft ready" in m["body"]][0]["body"]
    assert "University of Leeds" in summary and "approve" in summary

    # 3. Founder swipe-replies to the PDF with a change request: v2 arrives.
    doc_id = docs[0]["id"]
    send(workflow, FOUNDER, "replace Sheffield with UCL", reply_to=doc_id)
    c = case(workflow)
    assert c.version == 2 and c.status == CaseStatus.IN_REVIEW
    assert [u["university"] for u in c.draft["universities"]] == ["University of Leeds", "UCL"]
    assert workflow.drafter.revisions == ["replace Sheffield with UCL"]
    assert any("v2 revised" in m["body"] for m in to(channel, FOUNDER))

    # 4. Founder approves.
    send(workflow, FOUNDER, "approve")
    assert case(workflow).status == CaseStatus.APPROVED
    assert "approved" in to(channel, FOUNDER)[-1]["body"]

    # The client only ever received the welcome message.
    assert len(to(channel, CLIENT)) == 1


def test_later_client_messages_are_forwarded_not_answered(workflow, channel):
    send(workflow, CLIENT, "hi, can you help me study abroad?")
    send(workflow, CLIENT, "my budget is 30k pounds")
    assert len(to(channel, CLIENT)) == 1
    forwarded = to(channel, FOUNDER)[-1]["body"]
    assert "EN-0001" in forwarded and "budget" in forwarded
    assert case(workflow).profile["budget"] == "£30,000 per year"


def test_non_enquiry_first_message_gets_no_reply(workflow, channel):
    send(workflow, CLIENT, "who is this?")
    assert to(channel, CLIENT) == []
    assert "No auto-reply sent" in to(channel, FOUNDER)[0]["body"]


def test_duplicate_webhook_delivery_is_ignored(workflow, channel):
    msg = InboundMessage(phone=CLIENT, text="can you help me?", message_id="wamid.dup")
    assert workflow.receive(msg) is True
    assert workflow.receive(msg) is False
    workflow.run_pending()
    assert len(to(channel, CLIENT)) == 1


def test_case_reference_in_text(workflow, channel):
    send(workflow, CLIENT, "help please")
    send(workflow, CLIENT, "IB 36 psychology UK")
    send(workflow, FOUNDER, "#EN-0001 replace Sheffield with UCL")
    assert workflow.drafter.revisions == ["replace Sheffield with UCL"]


def test_ambiguous_founder_message_asks_which_case(workflow, channel):
    send(workflow, CLIENT, "help please")
    send(workflow, CLIENT, "IB 36 psychology UK")
    send(workflow, "201000000002", "help please")
    send(workflow, "201000000002", "IB 36 psychology UK")
    send(workflow, FOUNDER, "remove Leeds")
    assert "Which case" in to(channel, FOUNDER)[-1]["body"]
    assert workflow.drafter.revisions == []


def test_round_robin_assignment(settings, channel):
    settings.founder_numbers = [FOUNDER, FOUNDER_2]
    from edunxt.store import Store

    wf = Workflow(settings, Store(settings.database_url), channel, FakeIntake(), FakeResearch(), render=fake_render)
    send(wf, CLIENT, "help please")
    send(wf, "201000000002", "help please")
    assert case(wf, 1).founder_phone == FOUNDER
    assert case(wf, 2).founder_phone == FOUNDER_2


def test_founder_manual_case(workflow, channel):
    send(workflow, FOUNDER, "new Layla, IB 36, psychology UK")
    c = case(workflow)
    assert c.client_phone is None and c.status == CaseStatus.IN_REVIEW
    assert len([m for m in to(channel, FOUNDER) if m["kind"] == "document"]) == 2


def test_failed_draft_can_be_retried(workflow, channel):
    calls = {"n": 0}
    original = workflow.drafter.draft

    def flaky(*args):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return original(*args)

    workflow.drafter.draft = flaky
    send(workflow, CLIENT, "help")
    send(workflow, CLIENT, "IB 36 psychology UK")
    assert case(workflow).status == CaseStatus.FAILED
    assert "retry #EN-0001" in to(channel, FOUNDER)[-1]["body"]
    send(workflow, FOUNDER, "retry #EN-0001")
    assert case(workflow).status == CaseStatus.IN_REVIEW


def test_outside_24h_window_holds_messages_until_founder_replies(workflow, channel):
    channel.has_service_window = True
    send(workflow, CLIENT, "help please")
    # Founder never wrote to the number, so the notification is held and a template is sent.
    assert [m["kind"] for m in to(channel, FOUNDER)] == ["template"]
    with workflow.store.session() as s:
        assert s.scalars(select(MessageLog).where(MessageLog.status == "pending")).all()
    send(workflow, FOUNDER, "cases")
    kinds = [m["kind"] for m in to(channel, FOUNDER)]
    assert kinds == ["template", "text"]  # only the held notification; the reply just reopened the window
    assert "New enquiry" in to(channel, FOUNDER)[1]["body"]

    # Window open now: messages go straight through.
    send(workflow, CLIENT, "IB 36 psychology UK")
    assert len([m for m in to(channel, FOUNDER) if m["kind"] == "document"]) == 2

    # 24h later the window is closed again.
    with workflow.store.session.begin() as s:
        s.get(Contact, FOUNDER).last_inbound = utcnow() - timedelta(hours=25)
    before = len(to(channel, FOUNDER))
    send(workflow, CLIENT, "my budget is 30k")
    assert [m["kind"] for m in to(channel, FOUNDER)[before:]] == ["template"]


def test_reply_to_template_is_not_treated_as_a_change_request(workflow, channel):
    send(workflow, FOUNDER, "help")  # open the founder's window
    send(workflow, CLIENT, "help please")
    channel.has_service_window = True
    with workflow.store.session.begin() as s:
        s.get(Contact, FOUNDER).last_inbound = utcnow() - timedelta(hours=25)
    send(workflow, CLIENT, "IB 36 psychology UK")  # draft is held behind the template
    assert to(channel, FOUNDER)[-1]["kind"] == "template"
    assert case(workflow).status == CaseStatus.IN_REVIEW

    send(workflow, FOUNDER, "ok")
    assert [m["kind"] for m in to(channel, FOUNDER)[-3:]] == ["text", "document", "document"]
    assert workflow.drafter.revisions == []
    assert case(workflow).status == CaseStatus.IN_REVIEW


def test_founder_research_command_updates_lists(workflow, channel):
    send(workflow, FOUNDER, "research add 2 Dutch electrical engineering bachelor's under €15k")
    assert workflow.researcher.requests == ["add 2 Dutch electrical engineering bachelor's under €15k"]
    replies = [m["body"] for m in to(channel, FOUNDER)]
    assert replies[0].startswith("🔎 Researching")
    assert "2 universities added" in replies[1] and "1 searches, 1 pages read" in replies[1]
    send(workflow, FOUNDER, "research")
    assert "Send *research* followed by" in to(channel, FOUNDER)[-1]["body"]
