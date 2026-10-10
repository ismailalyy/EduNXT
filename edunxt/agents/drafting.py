"""Drafting Agent: builds a student's two documents from EduNXT's lists only, with no web access.

The model chooses rows by id; the documents are then built from the list's own text, so a
fee, requirement or scholarship can never be invented or misquoted at this step.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

from ..tools.partners import load_partners, load_scholarships, scholarships_for, search_partners
from .llm import UsageMeter, load_prompt
from .schemas import CaseDraft, Scholarship, ScholarshipRow, StudentProfile, UniversityRow

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
NO_REQUIREMENTS = "Not yet in EduNXT's list: please confirm"


class DraftingError(RuntimeError):
    pass


class Pick(BaseModel):
    id: int
    fee_basis: Literal["international", "local"] = Field(
        default="international", description="'local' only if the student's nationality qualifies for the row's fees_local."
    )
    note: str = Field(default="", description="Anything the founder should double-check about this row.")


class Selection(BaseModel):
    picks: list[Pick]
    missing_coverage: str = ""
    founder_notes: str = ""
    summary_of_changes: str = Field(default="", description="Revisions only: one line on what changed.")


class DraftingAgent:
    def __init__(self, client: anthropic.Anthropic, model: str, universities_csv: Path, scholarships_csv: Path, effort: str = "medium"):
        self.client = client
        self.model = model
        self.effort = effort
        self.universities_csv = Path(universities_csv)
        self.scholarships_csv = Path(scholarships_csv)
        self.system = load_prompt("drafting")

    # ------------------------------------------------------------------ public API (same as before)

    def draft(self, profile: StudentProfile, list_size: int, meter: UsageMeter) -> CaseDraft:
        rows, candidates = self._candidates(profile)
        brief = (
            f"Number of universities requested: {list_size}\n\n"
            f"Student profile:\n{profile.summary()}\n\n"
            f"EduNXT's list for these countries ({len(candidates)} rows):\n{self._rows_json(candidates)}"
        )
        selection = self._select(brief, meter)
        return self._build(rows, selection)

    def revise(self, profile: StudentProfile, current: CaseDraft, instruction: str, meter: UsageMeter) -> tuple[CaseDraft, str]:
        rows, _ = self._candidates(profile)
        # The founder may ask for another country ("add a Dutch option"), so offer the whole list.
        candidates = search_partners(rows, [], profile.intended_course or "")
        current_ids = self._ids_of(rows, current)
        brief = (
            f"Student profile:\n{profile.summary()}\n\n"
            f"Current selection, in order (ids): {current_ids}\n\n"
            f"EduNXT's full list ({len(candidates)} rows):\n{self._rows_json(candidates)}\n\n"
            f"<founder_request>\n{instruction}\n</founder_request>\n\n"
            "Return the complete new selection."
        )
        selection = self._select(brief, meter)
        draft = self._build(rows, selection)
        return draft, selection.summary_of_changes or "Selection updated."

    # ------------------------------------------------------------------ internals

    def _candidates(self, profile: StudentProfile) -> tuple[list[dict], list[dict]]:
        rows = load_partners(self.universities_csv)
        for i, row in enumerate(rows):
            row["id"] = i
        candidates = search_partners(rows, profile.countries, profile.intended_course or "")
        if not candidates:
            raise DraftingError(
                f"EduNXT's list has no universities in {', '.join(profile.countries) or 'the requested countries'}. "
                "Ask the research agent to add some (send: research ...)."
            )
        return rows, candidates

    @staticmethod
    def _rows_json(candidates: list[dict]) -> str:
        keep = ("id", "university", "country", "course", "degree_level", "fees", "fees_local", "requirements", "partner", "deadline", "notes", "course_match")
        return "\n".join(json.dumps({k: r.get(k) for k in keep if r.get(k) not in (None, "")}, ensure_ascii=False) for r in candidates)

    @staticmethod
    def _ids_of(rows: list[dict], draft: CaseDraft) -> list[int]:
        ids = []
        for u in draft.universities:
            for r in rows:
                if r["university"].casefold() == u.university.casefold() and r["course"].casefold() == u.course.casefold():
                    ids.append(r["id"])
                    break
        return ids

    def _select(self, brief: str, meter: UsageMeter) -> Selection:
        response = self.client.beta.messages.parse(
            model=self.model,
            max_tokens=16000,
            system=[{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": brief}],
            output_format=Selection,
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        meter.add(getattr(response, "model", self.model), response.usage)
        if response.stop_reason == "refusal" or response.parsed_output is None:
            raise DraftingError(f"the model returned no selection (stop_reason={response.stop_reason})")
        return response.parsed_output

    def _build(self, rows: list[dict], selection: Selection) -> CaseDraft:
        by_id = {r["id"]: r for r in rows}
        scholarships = load_scholarships(self.scholarships_csv)
        universities, scholarship_rows, unknown, seen = [], [], [], set()
        for pick in selection.picks:
            row = by_id.get(pick.id)
            if row is None:
                unknown.append(pick.id)
                continue
            if pick.id in seen:
                continue
            seen.add(pick.id)
            local = pick.fee_basis == "local" and bool(row.get("fees_local"))
            universities.append(
                UniversityRow(
                    university=row["university"],
                    country=row["country"],
                    course=row["course"],
                    fees=(row["fees_local"] if local else row.get("fees")) or "Not yet in EduNXT's list",
                    fee_basis="local" if local else "international",
                    requirements=row.get("requirements") or NO_REQUIREMENTS,
                    partner=bool(row.get("partner")),
                    source_url=row.get("url", ""),
                    confidence=(
                        "low" if "unverified" in (row.get("notes") or "").lower()
                        else "high" if row.get("requirements") and row.get("fees") else "medium"
                    ),
                    note=pick.note,
                )
            )
            scholarship_rows.append(
                ScholarshipRow(
                    university=row["university"],
                    scholarships=[
                        Scholarship(
                            name=s["name"],
                            value=s.get("value", ""),
                            eligibility=s.get("eligibility", ""),
                            deadline=s.get("deadline", ""),
                            source_url=s.get("url", ""),
                        )
                        for s in scholarships_for(scholarships, row["university"])
                    ],
                )
            )
        if unknown:
            log.warning("Drafting agent returned unknown row ids %s; ignored", unknown)
        if not universities:
            raise DraftingError(selection.missing_coverage or "no suitable universities in EduNXT's list")
        notes = " ".join(n for n in (selection.missing_coverage, selection.founder_notes) if n)
        return CaseDraft(universities=universities, scholarships=scholarship_rows, founder_notes=notes)
