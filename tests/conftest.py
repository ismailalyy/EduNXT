from __future__ import annotations

from pathlib import Path

import pytest

from edunxt.agents.schemas import (
    CaseDraft,
    IntakeResult,
    Scholarship,
    ScholarshipRow,
    StudentProfile,
    UniversityRow,
)
from edunxt.channels.simulator import SimulatorChannel
from edunxt.config import Settings
from edunxt.store import Store
from edunxt.workflow import Workflow

CLIENT = "201000000001"
FOUNDER = "201999999999"
FOUNDER_2 = "201888888888"


class FakeIntake:
    """Keyword-based stand-in for the Intake Agent."""

    def __init__(self):
        self.calls = []

    def analyze(self, text, meter=None):
        self.calls.append(text)
        low = text.lower()
        update = StudentProfile()
        intent = "other"
        if any(w in low for w in ("services", "help", "3ayez", "عايز", "study abroad")):
            intent = "services_enquiry"
        if "ib" in low.split():
            intent = "profile_info"
            update = StudentProfile(
                student_name="Layla Diab",
                curriculum="IB",
                predicted_grades="36",
                intended_course="Psychology",
                countries=["United Kingdom"],
            )
        if "budget" in low:
            intent = "profile_info"
            update = StudentProfile(budget="£30,000 per year")
        return IntakeResult(
            language="franco" if "3ayez" in low else "en",
            intent=intent,
            english_translation=text,
            summary_en=f"summary: {text[:30]}",
            profile_updates=update,
        )


def _row(name, fee="£30,000"):
    return UniversityRow(university=name, country="United Kingdom", course="BSc Psychology", fees=fee, requirements="36 points")


class FakeResearch:
    def __init__(self):
        self.drafts = 0
        self.revisions = []

    def draft(self, profile, list_size, meter):
        self.drafts += 1
        unis = [_row("University of Leeds"), _row("University of Sheffield")]
        return CaseDraft(
            universities=unis,
            scholarships=[
                ScholarshipRow(university="University of Leeds", scholarships=[Scholarship(name="Leeds Award", value="£2,000")]),
                ScholarshipRow(university="University of Sheffield"),
            ],
        )

    def revise(self, profile, current, instruction, meter):
        self.revisions.append(instruction)
        unis = [u for u in current.universities if "sheffield" not in u.university.lower()] + [_row("UCL", "£35,000")]
        return CaseDraft(universities=unis, scholarships=current.scholarships), "Replaced Sheffield with UCL."


class FakeResearcher:
    def __init__(self):
        self.requests = []

    def update(self, request, meter):
        from edunxt.agents.research import ResearchOutcome

        self.requests.append(request)
        return ResearchOutcome("Added 2 Dutch programmes.", universities_added=2,
                               sources=[{"tool": "web_search", "query": "q"}, {"tool": "web_fetch", "url": "u"}])


def fake_render(profile, draft, out_dir: Path, ref, version, phone=None):
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for label in ("University_Options", "Scholarships"):
        p = out_dir / f"{label}_{ref}_v{version}.pdf"
        p.write_bytes(b"%PDF-fake " + ", ".join(u.university for u in draft.universities).encode())
        paths.append(p)
    return tuple(paths)


@pytest.fixture
def settings(tmp_path):
    return Settings(
        founder_numbers=[FOUNDER],
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        output_dir=tmp_path / "out",
        partners_csv=Path(__file__).resolve().parent.parent / "data" / "partners.csv",
    )


@pytest.fixture
def channel():
    return SimulatorChannel(echo=False)


@pytest.fixture
def workflow(settings, channel):
    return Workflow(settings, Store(settings.database_url), channel, FakeIntake(), FakeResearch(), FakeResearcher(), render=fake_render)
