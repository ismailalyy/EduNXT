"""EduNXT's university list and scholarship list (CSV files, editable in any spreadsheet).

These two files are the only source the Drafting Agent uses for student documents. Founders
edit them directly, or ask the Research Agent to add or refresh entries.

Rows marked ``partner=yes`` are universities that pay EduNXT commission, so the Drafting
Agent ranks them first when they fit the student.
"""

from __future__ import annotations

import csv
import os
import tempfile
from datetime import date
from pathlib import Path

COLUMNS = ["university", "country", "course", "degree_level", "fees", "requirements", "scholarships", "partner", "url"]
# Optional extra columns are passed to the agents unchanged; these are written by the Research Agent.
# `fees` is the annual international (non-EU / overseas) fee; `fees_local` is the home / domestic / EU fee.
# A `notes` entry starting "Unverified:" names values not confirmed on an official page.
EXTRA_COLUMNS = ["fees_local", "deadline", "notes", "sources", "last_checked"]
SCHOLARSHIP_COLUMNS = ["university", "name", "value", "eligibility", "deadline", "url", "last_checked"]

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


# ---------------------------------------------------------------------------- scholarships


def load_scholarships(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return [r for r in csv.DictReader(fh) if (r.get("university") or "").strip() and (r.get("name") or "").strip()]


def scholarships_for(rows: list[dict], university: str) -> list[dict]:
    key = university.strip().casefold()
    return [r for r in rows if r["university"].strip().casefold() == key]


# ---------------------------------------------------------------------------- updates (Research Agent)


def _write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    """Write atomically, so a crash never leaves a half-written list."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)


def _read_raw(path: Path, default_fields: list[str]) -> tuple[list[str], list[dict]]:
    if not path.exists():
        return list(default_fields), []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        return list(reader.fieldnames or default_fields), list(reader)


def upsert_universities(path: Path, updates: list[dict], today: date | None = None) -> tuple[int, int]:
    """Add or update rows keyed by (university, course). Returns (added, updated).

    New rows are never marked as partners: that is a commercial fact only founders set.
    """
    today = today or date.today()
    fields, rows = _read_raw(path, COLUMNS + EXTRA_COLUMNS)
    for col in COLUMNS + EXTRA_COLUMNS:
        if col not in fields:
            fields.append(col)
    index = {(r["university"].strip().casefold(), r["course"].strip().casefold()): r for r in rows}
    added = updated = 0
    for u in updates:
        key = (u["university"].strip().casefold(), u["course"].strip().casefold())
        values = {k: (v.strip() if isinstance(v, str) else v) for k, v in u.items() if k in fields and k != "partner"}
        values = {k: v for k, v in values.items() if v not in (None, "")}  # never blank out a known value
        values["last_checked"] = today.isoformat()
        if key in index:
            index[key].update(values)
            updated += 1
        else:
            row = {f: "" for f in fields}
            row.update(values)
            row["partner"] = "no"
            rows.append(row)
            index[key] = row
            added += 1
    _write_csv(path, fields, rows)
    return added, updated


def upsert_scholarships(path: Path, updates: list[dict], today: date | None = None) -> tuple[int, int]:
    """Add or update scholarships keyed by (university, name). Returns (added, updated)."""
    today = today or date.today()
    fields, rows = _read_raw(path, SCHOLARSHIP_COLUMNS)
    for col in SCHOLARSHIP_COLUMNS:
        if col not in fields:
            fields.append(col)
    index = {(r["university"].strip().casefold(), r["name"].strip().casefold()): r for r in rows}
    added = updated = 0
    for s in updates:
        key = (s["university"].strip().casefold(), s["name"].strip().casefold())
        values = {k: v.strip() for k, v in s.items() if k in fields and isinstance(v, str) and v.strip()}
        values["last_checked"] = today.isoformat()
        if key in index:
            index[key].update(values)
            updated += 1
        else:
            row = {f: "" for f in fields}
            row.update(values)
            rows.append(row)
            index[key] = row
            added += 1
    _write_csv(path, fields, rows)
    return added, updated
