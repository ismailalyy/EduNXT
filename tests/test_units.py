from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from edunxt.agents.llm import UsageMeter
from edunxt.agents.research import ResearchAgent
from edunxt.agents.schemas import CaseDraft, ScholarshipRow, StudentProfile
from edunxt.app import create_app
from edunxt.channels.whatsapp import parse_webhook, verify_signature
from edunxt.documents.render import render_html
from edunxt.sample import sample_case
from edunxt.tools.partners import load_partners, search_partners, validate_partners


# ---------------------------------------------------------------- profile


def test_profile_merge_and_completeness():
    p = StudentProfile(curriculum="IB", countries=["United Kingdom"])
    assert not p.is_complete
    p = p.merge(StudentProfile(predicted_grades="36", intended_course="Psychology", countries=[]))
    assert p.is_complete and p.countries == ["United Kingdom"]
    p = p.merge(StudentProfile(notes="wants Scotland")).merge(StudentProfile(notes="needs scholarship"))
    assert p.notes == "wants Scotland; needs scholarship"


# ---------------------------------------------------------------- partners


def test_partner_search(settings):
    rows = load_partners(settings.partners_csv)
    assert validate_partners(settings.partners_csv) == []

    def matches(countries, course):
        return [r for r in search_partners(rows, countries, course) if r["course_match"]]

    psychology = matches(["England"], "Psychology")
    assert len(psychology) == 10 and not any(r["partner"] for r in psychology)
    assert matches(["Germany"], "Psychology") == []
    assert search_partners(rows, ["Japan"], "Psychology") == []

    europe_ee = search_partners(rows, ["Europe"], "Electrical Engineering")
    assert not any(r["country"] == "Canada" for r in europe_ee)
    assert "Digital Systems & Design BSc" in [r["course"] for r in europe_ee]  # non-matching name still offered
    assert all(r["course_match"] for r in europe_ee[:12])  # course matches are listed first
    assert len(matches(["Canada"], "Electrical Engineering")) == 10

    eu_medicine = matches(["EU"], "Medicine")
    assert {r["country"] for r in eu_medicine} == {"Italy", "Bulgaria"} and len(eu_medicine) == 8
    # "EU" excludes the UK; "Europe" includes it
    assert not any(r["country"] == "United Kingdom" for r in search_partners(rows, ["EU"], "Events Management"))
    assert any(r["country"] == "United Kingdom" for r in matches(["Europe"], "Events Management"))


# ---------------------------------------------------------------- whatsapp


WEBHOOK = {
    "object": "whatsapp_business_account",
    "entry": [{"changes": [{"value": {
        "contacts": [{"wa_id": "201000000001", "profile": {"name": "Layla"}}],
        "messages": [
            {"from": "201000000001", "id": "wamid.A", "type": "text", "text": {"body": "salam"}},
            {"from": "201000000001", "id": "wamid.B", "type": "audio", "audio": {"id": "1"},
             "context": {"id": "wamid.OUT"}},
        ],
    }}]}],
}


def test_parse_webhook():
    msgs = parse_webhook(WEBHOOK)
    assert [(m.message_id, m.kind, m.text) for m in msgs] == [("wamid.A", "text", "salam"), ("wamid.B", "audio", "")]
    assert msgs[0].contact_name == "Layla" and msgs[1].reply_to_id == "wamid.OUT"
    assert parse_webhook({"entry": [{"changes": [{"value": {"statuses": [{}]}}]}]}) == []


def test_signature():
    body = b'{"a":1}'
    sig = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert verify_signature("secret", body, sig)
    assert not verify_signature("secret", body, "sha256=bad")
    assert not verify_signature("secret", body, None)


def test_webhook_endpoints(settings, workflow):
    settings.webhook_verify_token = "tok"
    settings.meta_app_secret = "secret"
    client = TestClient(create_app(settings, workflow, start_worker=False))
    ok = client.get("/webhooks/meta", params={"hub.mode": "subscribe", "hub.verify_token": "tok", "hub.challenge": "42"})
    assert ok.status_code == 200 and ok.text == "42"
    assert client.get("/webhooks/meta", params={"hub.mode": "subscribe", "hub.verify_token": "no"}).status_code == 403

    body = json.dumps(WEBHOOK).encode()
    assert client.post("/webhooks/meta", content=body).status_code == 401
    sig = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert client.post("/webhooks/meta", content=body, headers={"X-Hub-Signature-256": sig}).status_code == 200
    assert workflow.store.has_queued(("inbound",))


# ---------------------------------------------------------------- rendering


def test_render_html(settings):
    profile, draft = sample_case(settings)
    html = render_html("universities", profile, draft, phone="201000000001")
    assert "UNIVERSITY OPTIONS FORM" in html and "University of Edinburgh" in html and "+201000000001" in html
    assert "Requirements (IB)" in html
    draft = CaseDraft(universities=draft.universities[:2], scholarships=[ScholarshipRow(university="Durham University")])
    html = render_html("scholarships", profile, draft)
    assert "SCHOLARSHIP OPTIONS" in html and "Durham University" in html


# ---------------------------------------------------------------- research agent loop


def _block(type_, **kw):
    return SimpleNamespace(type=type_, **kw)


def _resp(stop, *content):
    return SimpleNamespace(stop_reason=stop, content=list(content), model="claude-opus-5-5",
                           usage=SimpleNamespace(input_tokens=1000, output_tokens=100, cache_read_input_tokens=0,
                                                 cache_creation_input_tokens=0, server_tool_use=None))


UNI = {"university": "University of Leeds", "country": "United Kingdom", "course": "Psychology BSc", "fees": "£32,750",
       "requirements": "35 points", "partner": False, "source_url": "https://www.leeds.ac.uk/", "confidence": "high", "note": ""}


def test_agent_loop_handles_tools_pause_and_bad_submission(settings):
    agent = ResearchAgent(client=None, model="claude-opus-5-5", partners_csv=settings.partners_csv)
    script = [
        _resp("tool_use", _block("tool_use", id="t1", name="lookup_partner_universities",
                                 input={"countries": ["UK"], "course": "Psychology"})),
        _resp("pause_turn", _block("text", text="searching")),
        _resp("tool_use", _block("tool_use", id="t2", name="submit_university_list",
                                 input={"universities": [], "founder_notes": ""})),
        _resp("tool_use", _block("tool_use", id="t3", name="submit_university_list",
                                 input={"universities": [UNI], "founder_notes": "ok"})),
    ]
    seen = []

    def fake_create(**kwargs):
        seen.append(kwargs["messages"][-1])
        assert kwargs["fallbacks"] == "default" and kwargs["thinking"] == {"type": "adaptive"}
        return script.pop(0)

    meter = UsageMeter()
    result = agent._agent_loop("sys", "brief", [], "submit_university_list", "medium", meter, create=fake_create)
    assert result["universities"][0]["university"] == "University of Leeds"
    lookup_result = seen[1]["content"][0]
    assert lookup_result["tool_use_id"] == "t1" and "University of Leeds" in lookup_result["content"]
    assert seen[2]["role"] == "assistant"  # pause_turn: resent without adding a user message
    assert seen[3]["content"][0]["is_error"] is True  # empty list rejected
    assert meter.input_tokens == 4000 and meter.cost_usd > 0


def test_make_client_modes(monkeypatch):
    from edunxt.agents.llm import make_client

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://localhost:9")
    assert str(make_client("sk-test").base_url).startswith("https://api.anthropic.com")
    monkeypatch.setenv("EDUNXT_ANTHROPIC_AUTH", "proxy")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = make_client("")
    assert str(client.base_url).startswith("https://api.anthropic.com") and client.api_key is None
