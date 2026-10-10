"""Data shapes shared by the agents, the workflow and the PDF renderer."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

# Fields the Drafting Agent needs before it can draft a useful list. Nationality decides
# whether the student pays the international or the local (home / domestic / EU) fee.
REQUIRED_PROFILE_FIELDS = ("intended_course", "countries", "curriculum", "predicted_grades", "nationality")

PROFILE_QUESTIONS = {
    "student_name": "Student's full name",
    "curriculum": "High-school curriculum (e.g. IB, A-Level, American Diploma, IGCSE, Thanaweya Amma)",
    "predicted_grades": "Predicted or current grades (and subjects, e.g. IB HL subjects or A-Level subjects)",
    "intended_course": "The course / major you want to study",
    "degree_level": "Degree level (Foundation, Bachelor's, Master's)",
    "countries": "Country or countries you'd like to study in",
    "budget": "Yearly tuition budget (with currency)",
    "intake": "Intended start date (e.g. September 2027)",
    "nationality": "Nationality (this affects fees and scholarships)",
    "email": "Email address",
}


class StudentProfile(BaseModel):
    student_name: Optional[str] = None
    curriculum: Optional[str] = None
    predicted_grades: Optional[str] = None
    intended_course: Optional[str] = None
    degree_level: Optional[str] = None
    countries: list[str] = Field(default_factory=list)
    budget: Optional[str] = None
    intake: Optional[str] = None
    nationality: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    notes: Optional[str] = None

    def merge(self, update: "StudentProfile") -> "StudentProfile":
        """Return a copy with every non-empty field of ``update`` applied."""
        data = self.model_dump()
        for key, value in update.model_dump().items():
            if value not in (None, "", []):
                if key == "notes" and data.get("notes"):
                    value = f"{data['notes']}; {value}"
                data[key] = value
        return StudentProfile(**data)

    def missing(self, fields=tuple(PROFILE_QUESTIONS)) -> list[str]:
        return [f for f in fields if getattr(self, f) in (None, "", [])]

    @property
    def is_complete(self) -> bool:
        return not self.missing(REQUIRED_PROFILE_FIELDS)

    def summary(self) -> str:
        parts = []
        for key, label in (
            ("student_name", "Name"),
            ("curriculum", "Curriculum"),
            ("predicted_grades", "Grades"),
            ("intended_course", "Course"),
            ("degree_level", "Level"),
            ("countries", "Countries"),
            ("budget", "Budget"),
            ("intake", "Intake"),
            ("nationality", "Nationality"),
            ("email", "Email"),
            ("notes", "Notes"),
        ):
            value = getattr(self, key)
            if value:
                parts.append(f"{label}: {', '.join(value) if isinstance(value, list) else value}")
        return "\n".join(parts) or "(no details yet)"


class IntakeResult(BaseModel):
    """Structured output of the Intake Agent for a single client message."""

    language: Literal["en", "ar", "franco", "mixed", "other"]
    intent: Literal["services_enquiry", "profile_info", "question", "other"]
    english_translation: str = Field(description="Faithful English translation of the message.")
    summary_en: str = Field(description="One-line English summary for the founder.")
    profile_updates: StudentProfile


class UniversityRow(BaseModel):
    university: str
    country: str
    course: str = Field(description="Exact programme title, e.g. 'BSc (Hons) Psychology (4 years)'.")
    fees: str = Field(description="Annual tuition the student pays, with currency symbol, e.g. '£32,350'.")
    fee_basis: Literal["international", "local"] = "international"
    requirements: str = Field(description="Entry requirements expressed in the student's curriculum.")
    partner: bool = False
    source_url: str = ""
    confidence: Literal["high", "medium", "low"] = "medium"
    note: str = Field(default="", description="Anything the founder should double-check.")


class Scholarship(BaseModel):
    name: str
    value: str = ""
    eligibility: str = ""
    deadline: str = ""
    source_url: str = ""


class ScholarshipRow(BaseModel):
    university: str
    scholarships: list[Scholarship] = Field(default_factory=list)


class CaseDraft(BaseModel):
    universities: list[UniversityRow] = Field(default_factory=list)
    scholarships: list[ScholarshipRow] = Field(default_factory=list)
    founder_notes: str = ""

    def scholarships_for(self, university: str) -> list[Scholarship]:
        for row in self.scholarships:
            if row.university.strip().lower() == university.strip().lower():
                return row.scholarships
        return []
