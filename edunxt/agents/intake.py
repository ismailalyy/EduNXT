"""Intake Agent: reads client messages (English, Arabic, Franco-Arabic) and returns structured data.

It has no tools and never writes free text to a client. The only client-facing reply is
built from a fixed template in ``welcome_message`` below.
"""

from __future__ import annotations

import logging

import anthropic

from .llm import UsageMeter, load_prompt
from .schemas import PROFILE_QUESTIONS, IntakeResult, StudentProfile

log = logging.getLogger(__name__)


class IntakeAgent:
    def __init__(self, client: anthropic.Anthropic, model: str):
        self.client = client
        self.model = model
        self.system = load_prompt("intake")

    def analyze(self, text: str, meter: UsageMeter | None = None) -> IntakeResult:
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=2048,
            system=self.system,
            messages=[{"role": "user", "content": f"<client_message>\n{text}\n</client_message>"}],
            output_format=IntakeResult,
        )
        if meter:
            meter.add(self.model, response.usage)
        if response.stop_reason == "refusal" or response.parsed_output is None:
            log.warning("Intake could not parse message (stop_reason=%s)", response.stop_reason)
            return IntakeResult(
                language="other",
                intent="other",
                english_translation=text,
                summary_en="(could not analyze this message automatically)",
                profile_updates=StudentProfile(),
            )
        return response.parsed_output


def welcome_message(profile: StudentProfile, contact_name: str | None = None) -> str:
    """The one message the agent ever sends a client: a fixed English welcome and questionnaire."""
    greeting = f"Hi {contact_name.split()[0]}!" if contact_name else "Hi there!"
    missing = profile.missing()
    lines = [
        f"{greeting} 👋 Thank you for reaching out to *EduNXT*, your pathway to global education.",
        "",
        "We help students find and apply to universities abroad, and our guidance and support is "
        "*completely free of charge*. We'll put together a personalised list of universities and "
        "scholarships for you.",
    ]
    if missing:
        lines += ["", "To get started, please reply with:"]
        lines += [f"{i}. {PROFILE_QUESTIONS[f]}" for i, f in enumerate(missing, 1)]
        lines += ["", "You can reply in English or Arabic. One of our consultants will follow up shortly."]
    else:
        lines += ["", "Thanks for sharing your details. One of our consultants will follow up shortly."]
    return "\n".join(lines)
