"""Research Agent: keeps EduNXT's university and scholarship lists up to date, on a founder's request.

It is the only part of the system that uses the web. Student drafts are built from the lists
by the Drafting Agent, so research cost is paid once per list update, not once per student.
Every web search and page it reads is recorded, so its sources can be reviewed and tuned.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

import anthropic

from ..tools.partners import (
    load_partners,
    load_scholarships,
    scholarships_for,
    search_partners,
    upsert_scholarships,
    upsert_universities,
)
from .llm import UsageMeter, load_prompt

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TURNS = 40


class ResearchError(RuntimeError):
    pass


def _strict(name: str, description: str, properties: dict) -> dict:
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False},
    }


_STR = {"type": "string"}
UNIVERSITY_FIELDS = ("university", "country", "course", "degree_level", "fees", "fees_local", "requirements", "url", "deadline", "notes", "sources")
UNIVERSITY_UPDATE = {
    "type": "object",
    "properties": {k: _STR for k in UNIVERSITY_FIELDS},
    "required": list(UNIVERSITY_FIELDS),
    "additionalProperties": False,
}
SCHOLARSHIP_UPDATE = {
    "type": "object",
    "properties": {k: _STR for k in ("university", "name", "value", "eligibility", "deadline", "url")},
    "required": ["university", "name", "value", "eligibility", "deadline", "url"],
    "additionalProperties": False,
}

LOOKUP_UNIVERSITIES = _strict(
    "lookup_universities",
    "Show the rows EduNXT's university list already holds for some countries (regions such as 'Europe' work) "
    "and a course. Returns JSON rows; course_match=false rows are other courses in those countries.",
    {"countries": {"type": "array", "items": _STR}, "course": _STR},
)
LOOKUP_SCHOLARSHIPS = _strict(
    "lookup_scholarships",
    "Show the scholarships EduNXT's list already holds for one university (exact name from the university list).",
    {"university": _STR},
)
SUBMIT_UPDATES = _strict(
    "submit_updates",
    "Save new or corrected university rows and scholarships to EduNXT's lists. Call exactly once, at the end. "
    "Rows are matched on (university, course) and scholarships on (university, name); empty fields never "
    "overwrite known values.",
    {
        "universities": {"type": "array", "items": UNIVERSITY_UPDATE},
        "scholarships": {"type": "array", "items": SCHOLARSHIP_UPDATE},
        "summary": _STR,
    },
)


@dataclass
class ResearchOutcome:
    summary: str
    universities_added: int = 0
    universities_updated: int = 0
    scholarships_added: int = 0
    scholarships_updated: int = 0
    sources: list[dict] = field(default_factory=list)  # every web search / fetch the agent made

    def describe(self) -> str:
        return (
            f"{self.universities_added} universities added, {self.universities_updated} updated; "
            f"{self.scholarships_added} scholarships added, {self.scholarships_updated} updated"
        )


class ResearchAgent:
    def __init__(
        self,
        client: anthropic.Anthropic,
        model: str,
        universities_csv: Path,
        scholarships_csv: Path,
        effort: str = "medium",
        max_searches: int = 15,
        max_fetches: int = 20,
        fetch_max_tokens: int = 8000,
        allowed_domains: list[str] | None = None,
        log_path: Path | None = None,
    ):
        self.client = client
        self.model = model
        self.effort = effort
        self.universities_csv = Path(universities_csv)
        self.scholarships_csv = Path(scholarships_csv)
        self.max_searches = max_searches
        self.max_fetches = max_fetches
        self.fetch_max_tokens = fetch_max_tokens
        self.allowed_domains = [d for d in (allowed_domains or []) if d]
        self.log_path = Path(log_path) if log_path else None
        self.system = load_prompt("research")

    def _web_tools(self) -> list[dict]:
        search = {"type": "web_search_20260209", "name": "web_search", "max_uses": self.max_searches}
        fetch = {
            "type": "web_fetch_20260209",
            "name": "web_fetch",
            "max_uses": self.max_fetches,
            # Long pages are the main cost driver: each page is re-read on every later step.
            "max_content_tokens": self.fetch_max_tokens,
        }
        if self.allowed_domains:
            search["allowed_domains"] = fetch["allowed_domains"] = self.allowed_domains
        return [search, fetch]

    # ------------------------------------------------------------------ public API

    def update(self, request: str, meter: UsageMeter, create: Callable[..., Any] | None = None) -> ResearchOutcome:
        brief = f"Today's date: {date.today().isoformat()}\n\n<founder_request>\n{request}\n</founder_request>"
        result, sources = self._agent_loop(brief, meter, create)
        ua, uu = upsert_universities(self.universities_csv, result["universities"]) if result["universities"] else (0, 0)
        sa, su = upsert_scholarships(self.scholarships_csv, result["scholarships"]) if result["scholarships"] else (0, 0)
        outcome = ResearchOutcome(result["summary"], ua, uu, sa, su, sources)
        self._write_log(request, outcome, meter)
        return outcome

    # ------------------------------------------------------------------ internals

    def _write_log(self, request: str, outcome: ResearchOutcome, meter: UsageMeter) -> None:
        if not self.log_path:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "request": request,
            "result": outcome.describe(),
            "summary": outcome.summary,
            "cost_usd": round(meter.cost_usd, 4),
            "web_searches": meter.web_searches,
            "sources": outcome.sources,
        }
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _handle_client_tool(self, name: str, args: dict) -> str:
        if name == "lookup_universities":
            rows = search_partners(load_partners(self.universities_csv), args.get("countries", []), args.get("course", ""))
            return json.dumps(rows[:60], ensure_ascii=False, default=str) if rows else "[] (nothing in the list yet)"
        if name == "lookup_scholarships":
            rows = scholarships_for(load_scholarships(self.scholarships_csv), args.get("university", ""))
            return json.dumps(rows, ensure_ascii=False) if rows else "[] (no scholarships recorded)"
        raise ResearchError(f"unknown tool {name}")

    @staticmethod
    def _record_sources(content, sources: list[dict]) -> None:
        for block in content:
            if getattr(block, "type", None) == "server_tool_use":
                inp = block.input if isinstance(block.input, dict) else {}
                if block.name == "web_search":
                    sources.append({"tool": "web_search", "query": inp.get("query", "")})
                elif block.name == "web_fetch":
                    sources.append({"tool": "web_fetch", "url": inp.get("url", "")})

    def _agent_loop(self, brief: str, meter: UsageMeter, create: Callable[..., Any] | None = None) -> tuple[dict, list[dict]]:
        create = create or self.client.beta.messages.create
        tools = [LOOKUP_UNIVERSITIES, LOOKUP_SCHOLARSHIPS, *self._web_tools(), SUBMIT_UPDATES]
        messages: list[dict] = [{"role": "user", "content": brief}]
        sources: list[dict] = []
        nudges = 0
        for _ in range(MAX_TURNS):
            response = create(
                model=self.model,
                max_tokens=16000,
                system=[{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}],
                tools=tools,
                messages=messages,
                thinking={"type": "adaptive"},
                output_config={"effort": self.effort},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
            meter.add(getattr(response, "model", self.model), response.usage)
            self._record_sources(response.content, sources)
            if response.stop_reason == "refusal":
                raise ResearchError("the model declined this request")
            messages.append({"role": "assistant", "content": response.content})
            if response.stop_reason == "pause_turn":
                continue  # server-side tool loop paused; resend to resume

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                if response.stop_reason == "max_tokens" or nudges >= 2:
                    raise ResearchError(f"no submit_updates call (stop_reason={response.stop_reason})")
                nudges += 1
                messages.append({"role": "user", "content": "Please call `submit_updates` now with your results."})
                continue

            results, submitted = [], None
            for block in tool_uses:
                args = block.input if isinstance(block.input, dict) else json.loads(block.input)
                if block.name == SUBMIT_UPDATES["name"]:
                    problem = self._check_submission(args)
                    if problem:
                        results.append({"type": "tool_result", "tool_use_id": block.id, "is_error": True, "content": problem})
                    else:
                        submitted = args
                        results.append({"type": "tool_result", "tool_use_id": block.id, "content": "Saved."})
                else:
                    try:
                        results.append({"type": "tool_result", "tool_use_id": block.id, "content": self._handle_client_tool(block.name, args)})
                    except Exception as exc:  # report tool failures back to the model
                        results.append({"type": "tool_result", "tool_use_id": block.id, "is_error": True, "content": str(exc)})
            if submitted is not None:
                return submitted, sources
            messages.append({"role": "user", "content": results})
        raise ResearchError(f"gave up after {MAX_TURNS} turns without submit_updates")

    @staticmethod
    def _check_submission(args: dict) -> str:
        try:
            for u in args["universities"]:
                if not (u["university"].strip() and u["country"].strip() and u["course"].strip()):
                    return "Every university row needs university, country and course."
            for s in args["scholarships"]:
                if not (s["university"].strip() and s["name"].strip()):
                    return "Every scholarship needs university and name."
            if not args["summary"].strip():
                return "Please include a short summary for the founder."
        except (KeyError, TypeError, AttributeError) as exc:
            return f"Invalid submission: {exc}"
        return ""
