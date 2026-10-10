from __future__ import annotations

import csv
import json
import shutil
from types import SimpleNamespace

import pytest

from edunxt.agents.drafting import NO_REQUIREMENTS, DraftingAgent, DraftingError, Pick, Selection
from edunxt.agents.llm import UsageMeter
from edunxt.agents.research import ResearchAgent
from edunxt.agents.schemas import StudentProfile
from edunxt.tools.partners import load_partners, load_scholarships, upsert_universities

USAGE = SimpleNamespace(input_tokens=1000, output_tokens=100, cache_read_input_tokens=0,
                        cache_creation_input_tokens=0, server_tool_use=None)


@pytest.fixture
def lists(settings, tmp_path):
    unis = tmp_path / "universities.csv"
    shutil.copy(settings.partners_csv, unis)
    sch = tmp_path / "scholarships.csv"
    sch.write_text("university,name,value,eligibility,deadline,url,last_checked\n"
                   "University of Toronto,Lester B. Pearson Scholarship,Full tuition,Outstanding international students,Nov 2026,https://example.edu/pearson,2026-10-10\n",
                   encoding="utf-8")
    return unis, sch


def _block(type_, **kw):
    return SimpleNamespace(type=type_, **kw)


def _resp(stop, *content):
    return SimpleNamespace(stop_reason=stop, content=list(content), model="claude-opus-5-5", usage=USAGE)


# ---------------------------------------------------------------- research agent


def test_research_agent_updates_lists_and_logs_sources(lists, tmp_path):
    unis, sch = lists
    log_path = tmp_path / "research_log.jsonl"
    agent = ResearchAgent(None, "claude-opus-5-5", unis, sch, allowed_domains=["utoronto.ca"], log_path=log_path)
    toronto = {"university": "University of Toronto", "country": "Canada", "course": "BASc Electrical & Computer Engineering",
               "degree_level": "", "fees": "", "requirements": "IB: 36+; Thanaweya Amma: 95%", "url": "https://utoronto.ca/ece",
               "deadline": "", "notes": ""}
    concordia = {"university": "Concordia University", "country": "Canada", "course": "BEng Electrical Engineering",
                 "degree_level": "Bachelor's", "fees": "CA$34,000", "requirements": "IB: 30", "url": "https://concordia.ca",
                 "deadline": "Mar 1, 2027", "notes": ""}
    scholarship = {"university": "Concordia University", "name": "Concordia International Award", "value": "CA$5,000",
                   "eligibility": "Entrance", "deadline": "Mar 1", "url": "https://concordia.ca/award"}
    script = [
        _resp("tool_use",
              _block("server_tool_use", name="web_search", input={"query": "Concordia electrical engineering international fees"}),
              _block("tool_use", id="t1", name="lookup_universities", input={"countries": ["Canada"], "course": "Electrical Engineering"})),
        _resp("pause_turn", _block("server_tool_use", name="web_fetch", input={"url": "https://concordia.ca"})),
        _resp("tool_use", _block("tool_use", id="t2", name="submit_updates",
                                 input={"universities": [concordia], "scholarships": [], "summary": ""})),
        _resp("tool_use", _block("tool_use", id="t3", name="submit_updates",
                                 input={"universities": [toronto, concordia], "scholarships": [scholarship], "summary": "Added Concordia."})),
    ]
    sent = []

    def fake_create(**kwargs):
        sent.append({**kwargs, "messages": list(kwargs["messages"])})
        return script.pop(0)

    meter = UsageMeter()
    outcome = agent.update("add Concordia", meter, create=fake_create)

    assert (outcome.universities_added, outcome.universities_updated, outcome.scholarships_added) == (1, 1, 1)
    assert outcome.sources == [{"tool": "web_search", "query": "Concordia electrical engineering international fees"},
                               {"tool": "web_fetch", "url": "https://concordia.ca"}]
    web = {t["name"]: t for t in sent[0]["tools"] if t.get("type", "").startswith("web_")}
    assert web["web_fetch"]["max_content_tokens"] == 8000 and web["web_search"]["allowed_domains"] == ["utoronto.ca"]
    assert sent[3]["messages"][-1]["content"][0]["is_error"] is True  # empty summary rejected

    rows = {r["university"]: r for r in load_partners(unis)}
    assert rows["University of Toronto"]["requirements"].startswith("IB: 36+")
    assert rows["University of Toronto"]["fees"] == "CA$70,060"  # blank update didn't erase it
    assert rows["University of Toronto"]["partner"] is True and rows["Concordia University"]["partner"] is False
    assert rows["Concordia University"]["last_checked"]
    assert [s["name"] for s in load_scholarships(sch)][-1] == "Concordia International Award"
    entry = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
    assert entry["request"] == "add Concordia" and len(entry["sources"]) == 2


def test_upsert_never_blanks_known_values(lists):
    unis, _ = lists
    added, updated = upsert_universities(unis, [{"university": "McGill University", "course": "BEng Electrical Engineering",
                                                 "country": "Canada", "fees": "", "notes": "checked"}])
    assert (added, updated) == (0, 1)
    row = next(r for r in load_partners(unis) if r["university"] == "McGill University")
    assert row["fees"] == "CA$65,168" and row["notes"] == "checked"


# ---------------------------------------------------------------- drafting agent


class FakeParseClient:
    def __init__(self, selection):
        self.selection = selection
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))

    def _parse(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(parsed_output=self.selection, stop_reason="end_turn", usage=USAGE, model="claude-sonnet-5-5")


CANADA_EE = StudentProfile(student_name="Khaled", curriculum="IB", predicted_grades="36",
                           intended_course="Electrical Engineering", countries=["Canada"])


def _id(unis, name):
    return next(i for i, r in enumerate(load_partners(unis)) if r["university"] == name)


def test_drafting_agent_uses_only_the_lists(lists):
    unis, sch = lists
    toronto, mcgill = _id(unis, "University of Toronto"), _id(unis, "McGill University")
    client = FakeParseClient(Selection(picks=[Pick(id=toronto, note="reach"), Pick(id=999), Pick(id=mcgill)],
                                       missing_coverage="Few safe options."))
    draft = DraftingAgent(client, "claude-sonnet-5-5", unis, sch).draft(CANADA_EE, 10, UsageMeter())

    assert [u.university for u in draft.universities] == ["University of Toronto", "McGill University"]  # 999 ignored
    assert draft.universities[0].fees == "CA$70,060" and draft.universities[0].requirements == NO_REQUIREMENTS
    assert draft.universities[0].partner is True and draft.universities[0].note == "reach"
    assert draft.scholarships_for("University of Toronto")[0].name == "Lester B. Pearson Scholarship"
    assert draft.scholarships_for("McGill University") == []
    assert "Few safe options." in draft.founder_notes

    brief = client.calls[0]["messages"][0]["content"]
    assert "University of Toronto" in brief and "University of Edinburgh" not in brief  # only Canada rows sent
    assert client.calls[0]["model"] == "claude-sonnet-5-5" and "tools" not in client.calls[0]  # no web access


def test_drafting_agent_revision_sends_current_selection(lists):
    unis, sch = lists
    toronto, mcgill = _id(unis, "University of Toronto"), _id(unis, "McGill University")
    agent = DraftingAgent(FakeParseClient(Selection(picks=[Pick(id=toronto), Pick(id=mcgill)])), "m", unis, sch)
    current = agent.draft(CANADA_EE, 10, UsageMeter())
    agent.client = FakeParseClient(Selection(picks=[Pick(id=mcgill)], summary_of_changes="Removed Toronto."))
    revised, summary = agent.revise(CANADA_EE, current, "remove Toronto", UsageMeter())
    assert [u.university for u in revised.universities] == ["McGill University"] and summary == "Removed Toronto."
    brief = agent.client.calls[0]["messages"][0]["content"]
    assert f"(ids): [{toronto}, {mcgill}]" in brief
    assert "Politecnico di Milano" in brief  # revisions see the whole list, e.g. "add a European option"


def test_drafting_agent_points_to_research_when_list_is_empty(lists):
    unis, sch = lists
    agent = DraftingAgent(FakeParseClient(Selection(picks=[])), "m", unis, sch)
    with pytest.raises(DraftingError, match="research"):
        agent.draft(StudentProfile(intended_course="Architecture", countries=["Japan"]), 10, UsageMeter())
