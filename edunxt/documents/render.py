"""Render the University Options Form and the Scholarships document as PDFs."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from ..agents.schemas import CaseDraft, StudentProfile

STATIC = Path(__file__).parent / "static"

_env = Environment(loader=PackageLoader("edunxt.documents", "templates"), autoescape=select_autoescape(["html"]))

_LABEL = re.compile(r"^([A-Z][\w .'/&-]{0,24}):\s+(.*)$", re.S)


def points(text: str | None) -> list[tuple[str, str]]:
    """Split list text such as "IB: 36; English: IELTS 6.5 (min 6.0; any band)" into (label, text) bullets.

    Parts are separated by "; " outside brackets; a short leading "Label:" becomes the bullet's label.
    """
    parts, depth, start, text = [], 0, 0, (text or "").strip()
    for i, ch in enumerate(text):
        depth += ch in "([" and 1 or ch in ")]" and -1 or 0
        if ch == ";" and depth <= 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    out = []
    for part in (p.strip() for p in parts):
        if part:
            m = _LABEL.match(part)
            out.append((m.group(1), m.group(2)) if m else ("", part))
    return out


_env.filters["points"] = points


def _fees_shown(draft: CaseDraft) -> str:
    bases = {u.fee_basis for u in draft.universities}
    if bases == {"local"}:
        return "Local (home / domestic / EU)"
    if "local" in bases:
        return "Local where eligible, otherwise international"
    return "International"


def _student_fields(profile: StudentProfile, draft: CaseDraft, phone: str | None, today: date) -> list[tuple[str, str]]:
    return [
        ("Student Name", profile.student_name or ""),
        ("Date", today.strftime("%d %B %Y")),
        ("Desired Major / Field of Study", profile.intended_course or ""),
        ("Degree Level", profile.degree_level or ""),
        ("Nationality", profile.nationality or ""),
        ("Fees Shown", _fees_shown(draft)),
        ("Preferred Country", ", ".join(profile.countries)),
        ("Budget Range (if applicable)", profile.budget or ""),
        ("Contact Email", profile.email or ""),
        ("Phone Number", f"+{phone}" if phone and phone.isdigit() else (phone or "")),
    ]


def _short_curriculum(curriculum: str | None) -> str:
    if not curriculum:
        return ""
    return curriculum if len(curriculum) <= 18 else ""


def render_html(kind: str, profile: StudentProfile, draft: CaseDraft, phone: str | None = None, today: date | None = None) -> str:
    today = today or date.today()
    common = {
        "logo": (STATIC / "logo.svg").as_uri(),
        "student_fields": _student_fields(profile, draft, phone, today),
    }
    if kind == "universities":
        return _env.get_template("university_options.html").render(
            badge="UNIVERSITY OPTIONS FORM",
            universities=draft.universities,
            curriculum=_short_curriculum(profile.curriculum),
            **common,
        )
    if kind == "scholarships":
        rows = [
            {"university": u.university, "scholarships": draft.scholarships_for(u.university)} for u in draft.universities
        ]
        return _env.get_template("scholarships.html").render(badge="SCHOLARSHIP OPTIONS", rows=rows, **common)
    raise ValueError(kind)


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_") or "Student"


def render_pdfs(
    profile: StudentProfile, draft: CaseDraft, out_dir: Path, case_ref: str, version: int, phone: str | None = None
) -> tuple[Path, Path]:
    from weasyprint import HTML  # imported lazily: heavy, and not needed by most tests

    out_dir.mkdir(parents=True, exist_ok=True)
    name = _slug(profile.student_name or case_ref)
    paths = []
    for kind, label in (("universities", "University_Options"), ("scholarships", "Scholarships")):
        path = out_dir / f"EduNXT_{label}_{name}_{case_ref}_v{version}.pdf"
        HTML(string=render_html(kind, profile, draft, phone), base_url=str(STATIC)).write_pdf(path)
        paths.append(path)
    return paths[0], paths[1]
