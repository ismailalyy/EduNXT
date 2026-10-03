"""Live Intake Agent checks against the real API. Run with: RUN_LIVE=1 pytest tests/test_live_intake.py"""

from __future__ import annotations

import os

import pytest

from edunxt.agents.intake import IntakeAgent
from edunxt.agents.llm import make_client
from edunxt.config import load_settings

pytestmark = pytest.mark.skipif(not os.environ.get("RUN_LIVE"), reason="set RUN_LIVE=1 to call the Anthropic API")

CASES = [
    ("Hi, I'd like to know more about your services for studying abroad", "en", "services_enquiry"),
    ("السلام عليكم، عايزة أعرف إزاي تساعدوني أدرس برا", "ar", "services_enquiry"),
    ("salam, 3ayez a3raf ezay te2dar tesa3edni adros barra", "franco", "services_enquiry"),
]


@pytest.fixture(scope="module")
def agent():
    s = load_settings()
    return IntakeAgent(make_client(s.anthropic_api_key), s.intake_model)


@pytest.mark.parametrize("text,language,intent", CASES)
def test_language_and_intent(agent, text, language, intent):
    result = agent.analyze(text)
    assert result.language in (language, "mixed")
    assert result.intent == intent
    assert result.english_translation.isascii() or language == "en"


def test_franco_profile_extraction(agent):
    result = agent.analyze("ana esmy Omar, ba3mel IB w predicted 34, 3ayez adros computer science fe almanya aw holanda, budget 10k euro fel sana")
    p = result.profile_updates
    assert p.curriculum == "IB"
    assert "34" in (p.predicted_grades or "")
    assert "computer" in (p.intended_course or "").lower()
    assert {"Germany", "Netherlands"} <= set(p.countries)
