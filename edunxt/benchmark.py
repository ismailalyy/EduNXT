"""`python -m edunxt benchmark`: run real cases end to end and measure cost, time and quality.

Two modes, matching the two agents:

* default (per student): intake on the student's two WhatsApp messages, a draft from EduNXT's
  lists, then one founder revision. This is the cost of every new lead.
* ``--research`` (per list update): the case's universities are removed from a copy of the
  list, then the Research Agent is asked to add that kind of programme. Cost, searches and
  pages read are recorded, and the rows it adds are compared with the consultants' picks.
"""

from __future__ import annotations

import csv
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .agents.llm import UsageMeter
from .agents.schemas import CaseDraft
from .benchmark_profiles import CASES, BenchmarkCase
from .tools.partners import COLUMNS

log = logging.getLogger(__name__)

STOPWORDS = {"university", "of", "the", "de", "di", "college", "school", "and", "&", "—", "-", "la"}


def _name_tokens(name: str, keep_brackets: bool = True) -> set[str]:
    name = name.lower()
    # "(UBC)" or "(Vancouver)" may or may not be part of how a list names a university
    name = re.sub(r"\(([^)]*)\)", r" \1 " if keep_brackets else " ", name)
    return {t for t in re.findall(r"[a-z0-9]+", name) if t not in STOPWORDS}


def _tokens_match(ta: set[str], tb: set[str]) -> bool:
    if not ta or not tb:
        return False
    return ta <= tb or tb <= ta or len(ta & tb) / len(ta | tb) >= 0.6


def same_university(a: str, b: str) -> bool:
    return any(
        _tokens_match(_name_tokens(a, ka), _name_tokens(b, kb)) for ka in (True, False) for kb in (True, False)
    )


@dataclass
class StepResult:
    case: str
    step: str
    seconds: float
    meter: UsageMeter
    error: str = ""

    def row(self) -> dict:
        m = self.meter
        return {
            "case": self.case,
            "step": self.step,
            "seconds": round(self.seconds, 1),
            "input_tokens": m.input_tokens,
            "output_tokens": m.output_tokens,
            "cache_read_tokens": m.cache_read_tokens,
            "cache_write_tokens": m.cache_write_tokens,
            "web_searches": m.web_searches,
            "cost_usd": round(m.cost_usd, 4),
            "error": self.error,
        }


def _timed(case: str, step: str, fn: Callable[[UsageMeter], object]) -> tuple[StepResult, object]:
    meter = UsageMeter()
    start = time.monotonic()
    try:
        value, error = fn(meter), ""
    except Exception as exc:  # recorded, not raised: one failed case shouldn't stop the run
        log.exception("%s/%s failed", case, step)
        value, error = None, repr(exc)[:300]
    return StepResult(case, step, time.monotonic() - start, meter, error), value


def partners_without(case: BenchmarkCase, source: Path, dest: Path) -> Path:
    """Copy the partner sheet minus every row for a university on this case's answer list."""
    picks = {u.lower() for u, _ in case.consultant_picks}
    with source.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        fields = reader.fieldnames or COLUMNS
        rows = [r for r in reader if r["university"].lower() not in picks]
    with dest.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return dest


def overlap_names(case: BenchmarkCase, names: list[str]) -> tuple[list[str], list[str], list[str]]:
    """(matched, agent-only, consultant-only) university names."""
    consultant = list(dict.fromkeys(u for u, _ in case.consultant_picks))
    agent = list(dict.fromkeys(names))
    matched = [a for a in agent if any(same_university(a, c) for c in consultant)]
    agent_only = [a for a in agent if a not in matched]
    consultant_only = [c for c in consultant if not any(same_university(a, c) for a in agent)]
    return matched, agent_only, consultant_only


def overlap(case: BenchmarkCase, draft: CaseDraft) -> tuple[list[str], list[str], list[str]]:
    return overlap_names(case, [u.university for u in draft.universities])


def research_request(case: BenchmarkCase) -> str:
    p = case.profile
    budget = f", budget {p.budget}" if p.budget else ""
    return (
        f"Add {case.list_size} {p.degree_level or 'bachelor'} programmes in {p.intended_course} in "
        f"{', '.join(p.countries)} for a student with {p.curriculum} ({p.predicted_grades}){budget}. "
        f"Include annual international fees, entry requirements for {p.curriculum} and Thanaweya Amma, "
        "application deadlines, and each university's own scholarships."
    )


def run_case(case: BenchmarkCase, intake, make_drafter, render, out_dir: Path, universities_csv: Path) -> tuple[list[StepResult], list[str] | None]:
    """Per-student cost: intake, draft from EduNXT's lists, one revision."""
    case_dir = out_dir / case.id
    case_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for i, message in enumerate(case.messages, 1):
        res, analysis = _timed(case.id, f"intake_msg{i}", lambda m, t=message: intake.analyze(t, m))
        results.append(res)
        if analysis is not None:
            (case_dir / f"intake_msg{i}.json").write_text(analysis.model_dump_json(indent=2), encoding="utf-8")

    drafter = make_drafter(universities_csv)
    res, draft = _timed(case.id, "draft", lambda m: drafter.draft(case.profile, case.list_size, m))
    results.append(res)
    if draft is None:
        return results, None
    (case_dir / "draft_v1.json").write_text(draft.model_dump_json(indent=2), encoding="utf-8")
    render(case.profile, draft, case_dir, case.id, 1)

    res, revised = _timed(case.id, "revise", lambda m: drafter.revise(case.profile, draft, case.revision, m))
    results.append(res)
    if revised is not None:
        draft_v2, summary = revised
        (case_dir / "draft_v2.json").write_text(draft_v2.model_dump_json(indent=2), encoding="utf-8")
        (case_dir / "revision_summary.txt").write_text(summary, encoding="utf-8")
        render(case.profile, draft_v2, case_dir, case.id, 2)
    return results, [u.university for u in draft.universities]


def run_research_case(case: BenchmarkCase, make_researcher, out_dir: Path, universities_csv: Path, scholarships_csv: Path) -> tuple[list[StepResult], list[str] | None]:
    """Per-update cost: research into a copy of the list that lacks this case's universities."""
    case_dir = out_dir / case.id
    case_dir.mkdir(parents=True, exist_ok=True)
    blind = partners_without(case, universities_csv, case_dir / "universities_after_research.csv")
    before = {(r["university"], r["course"]) for r in csv.DictReader(blind.open(encoding="utf-8"))}
    sch_copy = case_dir / "scholarships_after_research.csv"
    sch_copy.write_bytes(scholarships_csv.read_bytes() if scholarships_csv.exists() else b"")
    researcher = make_researcher(blind, sch_copy, case_dir / "research_log.jsonl")
    request = research_request(case)
    (case_dir / "research_request.txt").write_text(request, encoding="utf-8")
    res, outcome = _timed(case.id, "research", lambda m: researcher.update(request, m))
    if outcome is None:
        return [res], None
    after = list(csv.DictReader(blind.open(encoding="utf-8")))
    added = [r["university"] for r in after if (r["university"], r["course"]) not in before]
    return [res], added


def write_reports(out_dir: Path, results: list[StepResult], picks: dict[str, list[str] | None], cases: list[BenchmarkCase], research: bool) -> None:
    with (out_dir / "results.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(results[0].row()))
        writer.writeheader()
        writer.writerows(r.row() for r in results)

    def per_step(step_prefix: str) -> list[StepResult]:
        return [r for r in results if r.step.startswith(step_prefix) and not r.error]

    mode = (
        "research (each case's universities removed from a copy of the list, then the Research Agent adds that kind of programme)"
        if research
        else "per student (draft from EduNXT's lists, no web)"
    )
    lines = [
        "# Benchmark results",
        "",
        f"Mode: {mode}",
        "",
        "## Cost and time per step (successful runs)",
        "",
        "| Step | Runs | Avg cost (USD) | Min | Max | Avg seconds | Avg web searches |",
        "|---|---|---|---|---|---|---|",
    ]
    for step in ("intake", "draft", "revise", "research"):
        rs = per_step(step)
        if rs:
            costs = [r.meter.cost_usd for r in rs]
            lines.append(
                f"| {step} | {len(rs)} | {sum(costs) / len(rs):.4f} | {min(costs):.4f} | {max(costs):.4f} | "
                f"{sum(r.seconds for r in rs) / len(rs):.0f} | {sum(r.meter.web_searches for r in rs) / len(rs):.1f} |"
            )
    errors = [r for r in results if r.error]
    lines += ["", f"Total cost of this run: ${sum(r.meter.cost_usd for r in results):.2f}", f"Failed steps: {len(errors)}"]
    for r in errors:
        lines.append(f"- {r.case}/{r.step}: {r.error}")

    heading = "Universities the Research Agent added vs consultants' picks" if research else "Draft vs consultants' picks"
    lines += ["", f"## {heading}", ""]
    if not research:
        lines += ["_The list already holds the consultants' picks, so this checks the drafting agent chooses them._", ""]
    for case in cases:
        names = picks.get(case.id)
        lines.append(f"### {case.title}")
        if case.assumptions:
            lines.append(f"_Assumptions: {case.assumptions}_")
        if names is None:
            lines += ["Step failed.", ""]
            continue
        matched, agent_only, consultant_only = overlap_names(case, names)
        n_consultant = len(set(u for u, _ in case.consultant_picks))
        lines += [
            f"- Matched {len(matched)} of {n_consultant} consultant universities: {', '.join(matched) or 'none'}",
            f"- Agent-only: {', '.join(agent_only) or 'none'}",
            f"- Consultant picks missed: {', '.join(consultant_only) or 'none'}",
            "",
        ]
    if research:
        lines += ["Every search and page read is in each case folder's `research_log.jsonl`."]
    (out_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def run_benchmark(settings, intake, make_drafter, render, case_ids: list[str] | None = None, parallel: int = 2, make_researcher=None) -> Path:
    """Default: per-student cost. With ``make_researcher``: research mode."""
    cases = [c for c in CASES if not case_ids or c.id in case_ids]
    research = make_researcher is not None
    out_dir = settings.output_dir / ("benchmark_research" if research else "benchmark")
    out_dir.mkdir(parents=True, exist_ok=True)

    def one(case):
        if research:
            return run_research_case(case, make_researcher, out_dir, settings.partners_csv, settings.scholarships_csv)
        return run_case(case, intake, make_drafter, render, out_dir, settings.partners_csv)

    with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
        outcomes = list(pool.map(one, cases))
    results = [r for case_results, _ in outcomes for r in case_results]
    picks = {c.id: names for c, (_, names) in zip(cases, outcomes)}
    write_reports(out_dir, results, picks, cases, research)
    (out_dir / "cases.json").write_text(
        json.dumps([{"id": c.id, "title": c.title, "assumptions": c.assumptions, "revision": c.revision} for c in cases], indent=2),
        encoding="utf-8",
    )
    return out_dir
