"""Sample case reproducing the EduNXT example list (Layla Diab), used by render-sample and tests."""

from __future__ import annotations

from .agents.schemas import CaseDraft, Scholarship, ScholarshipRow, StudentProfile, UniversityRow
from .config import Settings
from .tools.partners import load_partners


def sample_case(settings: Settings) -> tuple[StudentProfile, CaseDraft]:
    profile = StudentProfile(
        student_name="Layla Diab",
        curriculum="IB",
        predicted_grades="IB predicted 37 (HL Psychology 6, Biology 6, English 6)",
        intended_course="Psychology",
        degree_level="Bachelor's (BSc)",
        countries=["United Kingdom"],
    )
    rows = load_partners(settings.partners_csv)
    universities = [
        UniversityRow(
            university=r["university"],
            country=r["country"],
            course=r["course"],
            fees=r["fees"],
            requirements=r["requirements"].removeprefix("IB: "),
            partner=r["partner"],
            source_url=r["url"],
            confidence="high",
        )
        for r in rows
        if r["country"] == "United Kingdom" and "psychology" in r["course"].lower()
    ]
    scholarships = [ScholarshipRow(university=u.university) for u in universities]
    # Illustrative entry only, so the scholarships layout can be checked; not researched data.
    if scholarships:
        scholarships[0].scholarships = [
            Scholarship(
                name="(example) International Undergraduate Merit Award",
                value="£5,000 per year",
                eligibility="Example eligibility text",
                deadline="Example deadline",
            )
        ]
    return profile, CaseDraft(universities=universities, scholarships=scholarships)
