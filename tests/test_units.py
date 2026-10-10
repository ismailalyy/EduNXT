from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from edunxt.agents.llm import UsageMeter
from edunxt.agents.schemas import REQUIRED_PROFILE_FIELDS, CaseDraft, Scholarship, ScholarshipRow, StudentProfile
from edunxt.app import create_app
from edunxt.channels.whatsapp import parse_webhook, verify_signature
from edunxt.documents.render import points, render_html
from edunxt.sample import sample_case
from edunxt.tools.partners import load_partners, search_partners, validate_partners


# ---------------------------------------------------------------- profile


def test_profile_merge_and_completeness():
    p = StudentProfile(curriculum="IB", countries=["United Kingdom"])
    assert not p.is_complete
    p = p.merge(StudentProfile(predicted_grades="36", intended_course="Psychology", countries=[]))
    assert not p.is_complete and p.missing(REQUIRED_PROFILE_FIELDS) == ["nationality"]  # decides local vs international fees
    p = p.merge(StudentProfile(nationality="Egyptian"))
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
    assert "Nationality:</label><div>Egyptian" in html and "Fees Shown:</label><div>International" in html


def test_points_split_long_text_into_labelled_bullets():
    text = "IB: 36 points (6,6,6 HL; no 3s); Math AA HL; English: IELTS 6.5; Other: interview"
    assert points(text) == [("IB", "36 points (6,6,6 HL; no 3s)"), ("", "Math AA HL"), ("English", "IELTS 6.5"), ("Other", "interview")]
    assert points("") == [] and points("Not published") == [("", "Not published")]


def test_requirements_and_eligibility_render_as_bullets(settings):
    profile, draft = sample_case(settings)
    draft.universities[0].requirements = "IB: 36; English: IELTS 6.5"
    html = render_html("universities", profile, draft)
    assert '<li><b>IB:</b> 36</li><li><b>English:</b> IELTS 6.5</li>' in html
    sch = Scholarship(name="Award", eligibility="International students; merit-based", deadline="Jan 2027")
    draft = CaseDraft(universities=draft.universities[:1], scholarships=[ScholarshipRow(university=draft.universities[0].university, scholarships=[sch])])
    html = render_html("scholarships", profile, draft)
    assert "<li>International students</li><li>merit-based</li><li><b>Deadline:</b> Jan 2027</li>" in html


def test_long_lists_continue_onto_a_second_page(settings):
    weasyprint = pytest.importorskip("weasyprint")
    profile, draft = sample_case(settings)
    long_req = "; ".join(f"Point {i}: " + "detail " * 12 for i in range(8))
    draft.universities = [u.model_copy(update={"requirements": long_req}) for u in draft.universities * 3][:12]
    pages = weasyprint.HTML(string=render_html("universities", profile, draft)).render().pages
    assert len(pages) > 1


def test_make_client_modes(monkeypatch):
    from edunxt.agents.llm import make_client

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://localhost:9")
    assert str(make_client("sk-test").base_url).startswith("https://api.anthropic.com")
    monkeypatch.setenv("EDUNXT_ANTHROPIC_AUTH", "proxy")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = make_client("")
    assert str(client.base_url).startswith("https://api.anthropic.com") and client.api_key is None


def test_make_client_sends_workspace_header(monkeypatch):
    import httpx2

    from edunxt.agents.llm import make_client

    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "wrkspc_test")
    seen = {}

    def handler(request):
        seen.update(request.headers)
        return httpx2.Response(200, json={"id": "m", "type": "message", "role": "assistant", "model": "claude-haiku-4-5",
                                          "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn",
                                          "usage": {"input_tokens": 1, "output_tokens": 1}})

    client = make_client("sk-test").with_options(http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    client.messages.create(model="claude-haiku-4-5", max_tokens=5, messages=[{"role": "user", "content": "hi"}])
    assert seen["anthropic-workspace-id"] == "wrkspc_test"
