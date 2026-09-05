"""Resume ingestion and job matching.

Deliberately model-free: skills come from the same lexicon the JD extractor uses, so a
candidate's skills and a job's requirements are drawn from one vocabulary and are
therefore actually comparable. A match percentage computed across two different
vocabularies would be a number that looks precise and means nothing.
"""
from __future__ import annotations

import io
import re
from typing import Any

from app.services.skills import extract_skills

MAX_BYTES = 5 * 1024 * 1024   # 5 MB


class ResumeError(Exception):
    pass


# --------------------------------------------------------------------- text extraction
def extract_text(filename: str, blob: bytes) -> str:
    """Plain text from a .txt/.md or .pdf upload. Anything else is refused explicitly."""
    if len(blob) > MAX_BYTES:
        raise ResumeError("That file is larger than 5 MB. Upload a smaller PDF or paste the text.")

    lower = (filename or "").lower()

    if lower.endswith((".txt", ".md", ".text")):
        return blob.decode("utf-8", errors="replace")

    if lower.endswith(".pdf"):
        try:
            from pypdf import PdfReader
        except ImportError as exc:   # pragma: no cover - dependency is declared
            raise ResumeError(
                "PDF parsing is unavailable on this server. Paste your resume text instead."
            ) from exc
        try:
            reader = PdfReader(io.BytesIO(blob))
            pages = [(page.extract_text() or "") for page in reader.pages[:20]]
        except Exception as exc:  # noqa: BLE001
            raise ResumeError("That PDF could not be read. Paste your resume text instead.") from exc
        text = "\n".join(pages).strip()
        if not text:
            raise ResumeError(
                "No text found in that PDF — it looks like a scan. Paste your resume text instead."
            )
        return text

    raise ResumeError("Upload a PDF or a .txt file, or paste your resume text.")


# ------------------------------------------------------------------- experience parsing
_YEAR_PATTERNS = (
    # "7+ years", "7 yrs", "over 7 years"
    re.compile(r"(?:over\s+|more\s+than\s+)?(\d{1,2})\s*\+?\s*(?:years?|yrs?)\b", re.I),
    # "3-5 years" -> take the lower bound as the floor
    re.compile(r"(\d{1,2})\s*[-–to]+\s*(\d{1,2})\s*(?:years?|yrs?)\b", re.I),
)


def parse_years(text: str) -> float | None:
    """Best-effort years-of-experience from free text. Returns None when unstated.

    None means "unknown", never zero — a filter must not silently treat a resume it
    could not parse as a candidate with no experience.
    """
    if not text:
        return None
    best: float | None = None
    for pattern in _YEAR_PATTERNS:
        for match in pattern.finditer(text):
            groups = [int(g) for g in match.groups() if g and g.isdigit()]
            if not groups:
                continue
            value = float(min(groups))
            if 0 < value <= 50 and (best is None or value > best):
                best = value
    return best


def parse_resume(filename: str, blob: bytes) -> dict[str, Any]:
    text = extract_text(filename, blob)
    return {
        "filename": filename,
        "chars": len(text),
        "skills": extract_skills(text, limit=30),
        "years": parse_years(text),
        "text": text,
        "profile": parse_profile_sections(text),
    }


# ------------------------------------------------------------- profile-section parsing
#: Header aliases, matched against a whole line (case-insensitive, punctuation stripped).
#: Deliberately a fixed lexicon rather than a model call — same reasoning as skill
#: extraction: free, offline, and its failure mode is an empty section, never a
#: fabricated one.
_HEADER_ALIASES: dict[str, set[str]] = {
    "summary": {"summary", "profile", "objective", "about", "about me", "professional summary"},
    "experience": {"experience", "work experience", "employment", "employment history",
                   "professional experience", "work history"},
    "education": {"education", "academic background", "education and training"},
    "projects": {"projects", "personal projects", "academic projects", "key projects"},
    "skills": {"skills", "technical skills", "core competencies", "skills and tools"},
}

_TITLE_WORDS = ("engineer", "developer", "manager", "analyst", "designer", "lead",
                "architect", "director", "consultant", "specialist", "scientist",
                "intern", "founder", "administrator", "programmer")

_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_DATE_RANGE = re.compile(
    rf"(?:{_MONTH}\s*)?(?:19|20)\d{{2}}\s*(?:-|to|–|—)\s*"
    rf"(?:(?:{_MONTH}\s*)?(?:19|20)\d{{2}}|present|current)", re.I,
)


def _match_header(line: str) -> str | None:
    low = line.strip().strip(":-—–").lower()
    if not low or len(low) > 40:
        return None
    return next((key for key, names in _HEADER_ALIASES.items() if low in names), None)


def _split_sections(text: str) -> dict[str, list[str]]:
    """One pass over the resume, bucketing lines under the last header seen."""
    sections: dict[str, list[str]] = {"_header": []}
    current = "_header"
    for raw in text.splitlines():
        key = _match_header(raw)
        if key:
            current = key
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(raw)
    return sections


def _blocks(lines: list[str]) -> list[list[str]]:
    """Split a section's lines into blank-line-separated blocks — one block per entry."""
    blocks: list[list[str]] = []
    cur: list[str] = []
    for line in lines:
        if not line.strip():
            if cur:
                blocks.append(cur)
                cur = []
            continue
        cur.append(line.strip())
    if cur:
        blocks.append(cur)
    return blocks


def _split_head(head: str) -> tuple[str, str, str]:
    """'Senior Engineer — Acme Corp (2021-2023)' -> ('Senior Engineer', 'Acme Corp', dates)."""
    m = _DATE_RANGE.search(head)
    dates = m.group(0) if m else ""
    rest = (head[: m.start()] if m else head).strip(" ,.-–—|()")
    parts = re.split(r"\s+[-|–—@]\s+|,\s+", rest, maxsplit=1)
    first = parts[0].strip()
    second = parts[1].strip() if len(parts) > 1 else ""
    return first, second, dates


def _guess_headline(header_lines: list[str]) -> str:
    """The first short, title-shaped line after the name — never the name itself."""
    for line in header_lines[1:8]:
        s = line.strip()
        if not s or "@" in s or re.search(r"\d{3}", s):
            continue   # contact info: email, phone, address
        if len(s) <= 80 and any(w in s.lower() for w in _TITLE_WORDS):
            return s
    return ""


def _guess_experience(lines: list[str], limit: int = 6) -> list[dict[str, str]]:
    out = []
    for block in _blocks(lines)[:limit]:
        title, org, dates = _split_head(block[0])
        if not title:
            continue
        out.append({"title": title[:120], "org": org[:120], "dates": dates[:40],
                    "detail": " ".join(block[1:]).strip()[:500]})
    return out


def _guess_education(lines: list[str], limit: int = 3) -> list[dict[str, str]]:
    out = []
    for block in _blocks(lines)[:limit]:
        degree, org, dates = _split_head(block[0])
        if not degree:
            continue
        out.append({"degree": degree[:120], "org": org[:120], "dates": dates[:40]})
    return out


def _guess_projects(lines: list[str], limit: int = 4) -> list[dict[str, str]]:
    out = []
    for block in _blocks(lines)[:limit]:
        title = block[0].strip(" -–—")
        if not title:
            continue
        out.append({"title": title[:120], "detail": " ".join(block[1:]).strip()[:400]})
    return out


def parse_profile_sections(text: str) -> dict[str, Any]:
    """Best-effort, deterministic resume -> profile-shape extraction.

    Mirrors the shape `profile.js` already writes by hand (headline/summary/skills/
    experience/education/projects) so a parsed résumé and a hand-typed profile merge
    without a translation step. Every field's failure mode is empty, never invented —
    this is heuristics over section headers and date ranges, not a language model.
    """
    sections = _split_sections(text)
    return {
        "headline": _guess_headline(sections.get("_header", [])),
        "summary": " ".join(l.strip() for l in sections.get("summary", []) if l.strip())[:600],
        "experience": _guess_experience(sections.get("experience", [])),
        "education": _guess_education(sections.get("education", [])),
        "projects": _guess_projects(sections.get("projects", [])),
    }


# ---------------------------------------------------------------------------- matching
def match_percent(candidate_skills: list[str], job_skills: list[str]) -> int | None:
    """Share of a job's required skills the candidate evidences. None when incomparable.

    Returns None rather than 0 when either side is empty — a job with no extracted
    requirements should read as "not scored", not as a 0% match.
    """
    if not job_skills or not candidate_skills:
        return None
    have = {s.lower() for s in candidate_skills}
    hits = sum(1 for s in job_skills if s.lower() in have)
    return round(100 * hits / len(job_skills))


def missing_skills(candidate_skills: list[str], job_skills: list[str]) -> list[str]:
    have = {s.lower() for s in candidate_skills}
    return [s for s in job_skills if s.lower() not in have]
