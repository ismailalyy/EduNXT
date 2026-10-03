"""Research Agent: drafts the university list and the scholarship list, and revises both.

Only founders see its output. Scholarships are a sub-task: one small parallel call per
university, so the two documents always describe the same list.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any, Callable

import anthropic

from ..tools.partners import load_partners, search_partners
from .llm import UsageMeter, load_prompt
from .schemas import CaseDraft, Scholarship, ScholarshipRow, StudentProfile, UniversityRow

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TURNS = 40
SCHOLARSHIP_WORKERS = 5


class ResearchError(RuntimeError):
    pass


def _strict(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required or list(properties),
            "additionalProperties": False,
        },
    }


_STR = {"type": "string"}
UNIVERSITY_ITEM = {
    "type": "object",
    "properties": {
        "university": _STR,
        "country": _STR,
        "course": _STR,
        "fees": _STR,
        "requirements": _STR,
        "partner": {"type": "boolean"},
        "source_url": _STR,
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "note": _STR,
    },
    "required": ["university", "country", "course", "fees", "requirements", "partner", "source_url", "confidence", "note"],
    "additionalProperties": False,
}
SCHOLARSHIP_ITEM = {
    "type": "object",
    "properties": {"name": _STR, "value": _STR, "eligibility": _STR, "deadline": _STR, "source_url": _STR},
    "required": ["name", "value", "eligibility", "deadline", "source_url"],
    "additionalProperties": False,
}

LOOKUP_TOOL = _strict(
    "lookup_partner_universities",
    "Search EduNXT's own university sheet. Rows with partner=true pay EduNXT commission and should be "
    "included whenever they fit the student. Countries may be regions such as 'Europe' or 'EU'. Returns a JSON "
    "list of rows in those countries; course_match=true rows share a word with the course, but check the others "
    "too, since programme names vary.",
    {"countries": {"type": "array", "items": _STR}, "course": _STR},
)
SUBMIT_LIST_TOOL = _strict(
    "submit_university_list",
    "Submit the final university shortlist. Call exactly once, when research is complete.",
    {"universities": {"type": "array", "items": UNIVERSITY_ITEM}, "founder_notes": _STR},
)
SUBMIT_REVISION_TOOL = _strict(
    "submit_revision",
    "Submit the complete revised university list. Call exactly once, when the changes are done.",
    {
        "universities": {"type": "array", "items": UNIVERSITY_ITEM},
        "refresh_scholarships_for": {"type": "array", "items": _STR},
        "scholarship_instructions": _STR,
        "summary_of_changes": _STR,
    },
)
SUBMIT_SCHOLARSHIPS_TOOL = _strict(
    "submit_scholarships",
    "Submit the scholarships for this university. Submit an empty list if none fit.",
    {"scholarships": {"type": "array", "items": SCHOLARSHIP_ITEM}},
)


def _web_tools(searches: int, fetches: int) -> list[dict]:
    return [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": searches},
        {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": fetches},
    ]


class ResearchAgent:
    def __init__(self, client: anthropic.Anthropic, model: str, partners_csv, effort: str = "medium"):
        self.client = client
        self.model = model
        self.effort = effort
        self.partners_csv = partners_csv
        self.universities_prompt = load_prompt("universities")
        self.revision_prompt = self.universities_prompt + load_prompt("revision")
        self.scholarships_prompt = load_prompt("scholarships")

    # ------------------------------------------------------------------ public API

    def draft(self, profile: StudentProfile, list_size: int, meter: UsageMeter) -> CaseDraft:
        brief = (
            f"Today's date: {date.today().isoformat()}\n"
            f"Number of universities required: {list_size}\n\n"
            f"Student profile:\n{profile.summary()}"
        )
        result = self._agent_loop(
            system=self.universities_prompt,
            user=brief,
            tools=[LOOKUP_TOOL, *_web_tools(20, 25), SUBMIT_LIST_TOOL],
            submit=SUBMIT_LIST_TOOL["name"],
            effort=self.effort,
            meter=meter,
        )
        universities = [UniversityRow(**u) for u in result["universities"]]
        scholarships = self._scholarships_for(profile, universities, meter)
        return CaseDraft(universities=universities, scholarships=scholarships, founder_notes=result["founder_notes"])

    def revise(
        self, profile: StudentProfile, current: CaseDraft, instruction: str, meter: UsageMeter
    ) -> tuple[CaseDraft, str]:
        brief = (
            f"Today's date: {date.today().isoformat()}\n\n"
            f"Student profile:\n{profile.summary()}\n\n"
            f"Current draft (JSON):\n{current.model_dump_json(indent=1)}\n\n"
            f"<founder_request>\n{instruction}\n</founder_request>"
        )
        result = self._agent_loop(
            system=self.revision_prompt,
            user=brief,
            tools=[LOOKUP_TOOL, *_web_tools(12, 15), SUBMIT_REVISION_TOOL],
            submit=SUBMIT_REVISION_TOOL["name"],
            effort=self.effort,
            meter=meter,
        )
        universities = [UniversityRow(**u) for u in result["universities"]]
        previous = {u.university.strip().lower() for u in current.universities}
        refresh = {name.strip().lower() for name in result["refresh_scholarships_for"]}
        to_research = [
            u for u in universities if u.university.strip().lower() not in previous or u.university.strip().lower() in refresh
        ]
        fresh = {
            row.university.strip().lower(): row
            for row in self._scholarships_for(profile, to_research, meter, result["scholarship_instructions"])
        }
        scholarships = [
            fresh.get(u.university.strip().lower())
            or ScholarshipRow(university=u.university, scholarships=current.scholarships_for(u.university))
            for u in universities
        ]
        draft = CaseDraft(universities=universities, scholarships=scholarships, founder_notes=current.founder_notes)
        return draft, result["summary_of_changes"]

    # ------------------------------------------------------------------ scholarships

    def _scholarships_for(
        self, profile: StudentProfile, universities: list[UniversityRow], meter: UsageMeter, hint: str = ""
    ) -> list[ScholarshipRow]:
        if not universities:
            return []
        with ThreadPoolExecutor(max_workers=SCHOLARSHIP_WORKERS) as pool:
            rows = list(pool.map(lambda u: self._one_university_scholarships(profile, u, meter, hint), universities))
        return rows

    def _one_university_scholarships(
        self, profile: StudentProfile, uni: UniversityRow, meter: UsageMeter, hint: str
    ) -> ScholarshipRow:
        sheet_hint = ""
        for row in load_partners(self.partners_csv):
            if row["university"].strip().lower() == uni.university.strip().lower() and row.get("scholarships"):
                sheet_hint = f"\nEduNXT's sheet lists these scholarships for this university: {row['scholarships']}"
        brief = (
            f"Today's date: {date.today().isoformat()}\n"
            f"University: {uni.university} ({uni.country})\nProgramme: {uni.course}\n"
            f"Tuition: {uni.fees}\n\nStudent profile:\n{profile.summary()}{sheet_hint}"
            + (f"\n\nFounder instructions: {hint}" if hint else "")
        )
        try:
            result = self._agent_loop(
                system=self.scholarships_prompt,
                user=brief,
                tools=[*_web_tools(4, 4), SUBMIT_SCHOLARSHIPS_TOOL],
                submit=SUBMIT_SCHOLARSHIPS_TOOL["name"],
                effort="low",
                meter=meter,
            )
            items = [Scholarship(**s) for s in result["scholarships"]]
        except ResearchError as exc:
            log.warning("Scholarship research failed for %s: %s", uni.university, exc)
            items = [Scholarship(name="(automatic search failed, please check manually)")]
        return ScholarshipRow(university=uni.university, scholarships=items)

    # ------------------------------------------------------------------ agent loop

    def _handle_client_tool(self, name: str, args: dict) -> str:
        if name == "lookup_partner_universities":
            rows = search_partners(load_partners(self.partners_csv), args.get("countries", []), args.get("course", ""))
            return json.dumps(rows[:40], ensure_ascii=False) if rows else "[] (no matching rows in the sheet)"
        raise ResearchError(f"unknown tool {name}")

    def _agent_loop(
        self,
        system: str,
        user: str,
        tools: list[dict],
        submit: str,
        effort: str,
        meter: UsageMeter,
        create: Callable[..., Any] | None = None,
    ) -> dict:
        create = create or self.client.beta.messages.create
        messages: list[dict] = [{"role": "user", "content": user}]
        nudges = 0
        for _ in range(MAX_TURNS):
            response = create(
                model=self.model,
                max_tokens=16000,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                tools=tools,
                messages=messages,
                thinking={"type": "adaptive"},
                output_config={"effort": effort},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
            meter.add(getattr(response, "model", self.model), response.usage)
            if response.stop_reason == "refusal":
                raise ResearchError("the model declined this request")
            messages.append({"role": "assistant", "content": response.content})
            if response.stop_reason == "pause_turn":
                continue  # server-side tool loop paused; resend to resume

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                if response.stop_reason == "max_tokens" or nudges >= 2:
                    raise ResearchError(f"no {submit} call (stop_reason={response.stop_reason})")
                nudges += 1
                messages.append({"role": "user", "content": f"Please call `{submit}` now with your final answer."})
                continue

            results, submitted = [], None
            for block in tool_uses:
                args = block.input if isinstance(block.input, dict) else json.loads(block.input)
                if block.name == submit:
                    try:
                        self._validate_submission(submit, args)
                        submitted = args
                        results.append({"type": "tool_result", "tool_use_id": block.id, "content": "Received."})
                    except (ValueError, KeyError, TypeError) as exc:  # pydantic errors are ValueErrors
                        results.append(
                            {"type": "tool_result", "tool_use_id": block.id, "is_error": True, "content": f"Invalid submission: {exc}"}
                        )
                else:
                    try:
                        content = self._handle_client_tool(block.name, args)
                        results.append({"type": "tool_result", "tool_use_id": block.id, "content": content})
                    except Exception as exc:  # report tool failures back to the model
                        results.append({"type": "tool_result", "tool_use_id": block.id, "is_error": True, "content": str(exc)})
            if submitted is not None:
                return submitted
            messages.append({"role": "user", "content": results})
        raise ResearchError(f"gave up after {MAX_TURNS} turns without {submit}")

    @staticmethod
    def _validate_submission(submit: str, args: dict) -> None:
        if submit == SUBMIT_SCHOLARSHIPS_TOOL["name"]:
            [Scholarship(**s) for s in args["scholarships"]]
            return
        rows = [UniversityRow(**u) for u in args["universities"]]
        if not rows:
            raise ValueError("the university list is empty")
