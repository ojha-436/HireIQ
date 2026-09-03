"""Candidate profile -> the typed sections the interview engine grounds on.

Raw résumé prose never reaches a prompt (see interview/context.py). Only these
canonical fields do.
"""
from __future__ import annotations

from typing import Any


def resolve_effective_profile(candidate: Any, **_: Any) -> dict[str, Any]:
    sections = getattr(candidate, "profile_sections_json", None) or {}
    return {
        "full_name": getattr(candidate, "full_name", "") or "",
        "headline": sections.get("headline", "") or "",
        "summary": sections.get("summary", "") or "",
        "skills": sections.get("skills", []) or [],
        "experience": sections.get("experience", []) or [],
        "education": sections.get("education", []) or [],
        "projects": sections.get("projects", []) or [],
    }
