"""Skill taxonomy for the interview engine.

The engine addresses skills by stable id (`python`, `system_design`) and renders them
by name. HireIQ's JD extractor works in canonical display names, so this module is the
bridge: it derives ids from the extractor's lexicon and exposes the id -> name map the
engine expects.
"""
from __future__ import annotations

from app.services.skills import LEXICON


def _slug(name: str) -> str:
    out = []
    for ch in name.lower():
        out.append(ch if ch.isalnum() else "_")
    return "_".join(filter(None, "".join(out).split("_")))


#: skill_id -> display name, e.g. {"system_design": "System Design"}
SKILL_NAME: dict[str, str] = {_slug(name): name for name in LEXICON}

#: display name -> skill_id, for the inverse lookup the routers need
SKILL_ID: dict[str, str] = {name: sid for sid, name in SKILL_NAME.items()}


def to_ids(names: list[str]) -> list[str]:
    """Canonical display names (as stored on job_postings) -> skill ids."""
    return [SKILL_ID[n] for n in names or [] if n in SKILL_ID]


def to_names(skill_ids: list[str]) -> list[str]:
    return [SKILL_NAME[s] for s in skill_ids or [] if s in SKILL_NAME]
