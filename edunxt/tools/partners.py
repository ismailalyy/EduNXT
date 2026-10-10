"""Founder-maintained sheet of universities (CSV export of a spreadsheet).

Rows marked ``partner=yes`` are universities that pay EduNXT commission, so the Research
Agent ranks them first when they fit the student.
"""

from __future__ import annotations

import csv
from pathlib import Path

COLUMNS = ["university", "country", "course", "degree_level", "fees", "requirements", "scholarships", "partner", "url"]
# Optional extra columns (e.g. fees_local, deadline, notes, sources) are passed to the agent unchanged.
# `fees` is the annual international (non-EU / overseas) fee; `fees_local` is the home / domestic / EU fee.

# Common aliases so "UK" or "England" in a profile still matches "United Kingdom" rows.
COUNTRY_ALIASES = {
    "uk": "united kingdom",
    "england": "united kingdom",
    "scotland": "united kingdom",
    "wales": "united kingdom",
    "britain": "united kingdom",
    "great britain": "united kingdom",
    "usa": "united states",
    "us": "united states",
    "america": "united states",
    "holland": "netherlands",
    "the netherlands": "netherlands",
}


EU_MEMBERS = {
    "austria", "belgium", "bulgaria", "croatia", "cyprus", "czechia", "denmark", "estonia", "finland",
    "france", "germany", "greece", "hungary", "ireland", "italy", "latvia", "lithuania", "luxembourg",
    "malta", "netherlands", "poland", "portugal", "romania", "slovakia", "slovenia", "spain", "sweden",
}
# Students often name a region rather than a country.
REGIONS = {
    "eu": EU_MEMBERS,
    "european union": EU_MEMBERS,
    "europe": EU_MEMBERS | {"united kingdom", "switzerland", "norway", "iceland", "serbia", "north macedonia", "albania"},
}


def _country_key(name: str) -> str:
    key = name.strip().lower()
    return COUNTRY_ALIASES.get(key, key)


def _wanted_countries(countries: list[str]) -> set[str]:
    wanted: set[str] = set()
    for country in countries:
        key = _country_key(country)
        wanted |= REGIONS.get(key, {key})
    return wanted


def load_partners(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    for row in rows:
        row["partner"] = (row.get("partner") or "").strip().lower() in {"yes", "y", "true", "1"}
    return rows


def validate_partners(path: Path) -> list[str]:
    """Return a list of problems with the CSV (empty list means it is fine)."""
    if not path.exists():
        return [f"{path} does not exist"]
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        problems = [f"missing column '{c}'" for c in COLUMNS if c not in header]
        for i, row in enumerate(reader, start=2):
            for col in ("university", "country", "course"):
                if not (row.get(col) or "").strip():
                    problems.append(f"line {i}: empty '{col}'")
    return problems


def search_partners(rows: list[dict], countries: list[str], course: str = "") -> list[dict]:
    """Rows in any of ``countries``. Rows whose course shares a word with ``course`` come first
    and carry ``course_match=True``; the rest are included because names differ ("Digital
    Systems & Design" is an electrical-engineering option). The Research Agent decides what fits.
    """
    wanted = _wanted_countries([c for c in countries if c.strip()])
    words = {w for w in course.lower().replace("/", " ").split() if len(w) > 3}
    results = []
    for row in rows:
        if wanted and _country_key(row.get("country", "")) not in wanted:
            continue
        row_words = set(row.get("course", "").lower().replace("/", " ").split())
        results.append({**row, "course_match": not words or bool(words & row_words)})
    results.sort(key=lambda r: (not r["course_match"], not r["partner"]))
    return results
