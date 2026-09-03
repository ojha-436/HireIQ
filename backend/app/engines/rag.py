"""Course grounding was a PathFinder feature and is not part of PS11.

The interview engine calls `courses_for_skills` when building a report's remediation
block. Returning nothing is correct here rather than fabricating course recommendations
a hiring product has no business making.
"""
from __future__ import annotations

from typing import Any


def courses_for_skills(skill_ids: list[str], limit: int = 3) -> list[dict[str, Any]]:
    return []
