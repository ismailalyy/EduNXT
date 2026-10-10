from __future__ import annotations

import csv

from edunxt.agents.schemas import CaseDraft, UniversityRow
from edunxt.benchmark import overlap, partners_without, run_benchmark, same_university
from edunxt.benchmark_profiles import CASES

from .conftest import FakeIntake, FakeResearch, fake_render


def test_same_university():
    assert same_university("University of British Columbia (UBC)", "UBC")
    assert same_university("McGill University", "McGill University, Montreal")
    assert same_university("Universidad Europea de Valencia", "Universidad Europea")
    assert not same_university("University of Toronto", "University of Ottawa")
    assert not same_university("Politecnico di Milano", "Politecnico di Torino")
    # seen in the first live run: same university, different bracketed suffix
    assert same_university("University of British Columbia (Vancouver)", "University of British Columbia (UBC)")
    assert not same_university("University of Toronto (St. George)", "University of Ottawa (Main)")


def test_overlap():
    case = next(c for c in CASES if c.id == "canada_ee")
    draft = CaseDraft(universities=[
        UniversityRow(university=name, country="Canada", course="EE", fees="", requirements="")
        for name in ("University of Toronto", "UBC", "Concordia University")
    ])
    matched, agent_only, consultant_only = overlap(case, draft)
    assert matched == ["University of Toronto", "UBC"]
    assert agent_only == ["Concordia University"]
    assert len(consultant_only) == 8


def test_partners_without_removes_the_case_answer(settings, tmp_path):
    case = next(c for c in CASES if c.id == "medicine_eu")
    path = partners_without(case, settings.partners_csv, tmp_path / "p.csv")
    names = {r["university"] for r in csv.DictReader(path.open(encoding="utf-8"))}
    assert "University of Bologna" not in names and "Trakia University" not in names
    assert "University of Toronto" in names


def test_run_benchmark_with_fakes(settings):
    out = run_benchmark(settings, FakeIntake(), lambda _csv: FakeResearch(), fake_render, case_ids=["canada_ee", "medicine_eu"])
    rows = list(csv.DictReader((out / "results.csv").open(encoding="utf-8")))
    assert {r["step"] for r in rows} == {"intake_msg1", "intake_msg2", "draft", "revise"}
    assert len(rows) == 8 and not any(r["error"] for r in rows)
    summary = (out / "summary.md").read_text(encoding="utf-8")
    assert "Khaled: Electrical Engineering, Canada" in summary and "Matched 0 of 10" in summary
    assert (out / "canada_ee" / "draft_v2.json").exists()
