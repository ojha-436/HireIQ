"""JD -> canonical skill ids, for the interview engine's grounding block."""
from __future__ import annotations

from typing import Any

from app.engines import datasets as ds
from app.services.skills import extract_skills


def parse_jd(jd_text: str, **_: Any) -> dict[str, Any]:
    names = extract_skills(jd_text or "")
    return {
        "required_skill_ids": ds.to_ids(names),
        "required_skill_names": names,
        "seniority": "",
    }
