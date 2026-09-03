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
