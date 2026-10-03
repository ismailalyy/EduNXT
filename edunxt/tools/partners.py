"""Founder-maintained sheet of universities (CSV export of a spreadsheet).

Rows marked ``partner=yes`` are universities that pay EduNXT commission, so the Research
Agent ranks them first when they fit the student.
"""

from __future__ import annotations

import csv
from pathlib import Path

COLUMNS = ["university", "country", "course", "degree_level", "fees", "requirements", "scholarships", "partner", "url"]

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


def _country_key(name: str) -> str:
    key = name.strip().lower()
    return COUNTRY_ALIASES.get(key, key)


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
    """Rows in any of ``countries`` whose course shares a word with ``course``.

    Matching is deliberately loose; the Research Agent decides what actually fits.
    """
    wanted = {_country_key(c) for c in countries if c.strip()}
    words = {w for w in course.lower().replace("/", " ").split() if len(w) > 3}
    results = []
    for row in rows:
        if wanted and _country_key(row.get("country", "")) not in wanted:
            continue
        if words and not words & set(row.get("course", "").lower().replace("/", " ").split()):
            continue
        results.append(row)
    results.sort(key=lambda r: not r["partner"])
    return results
