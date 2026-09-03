"""Question-bank retrieval (plan-v3.md §3.3).

Deliberately NOT a vector store. The corpus is a few dozen to a few thousand rows, and at
that size metadata filtering plus term overlap beats embeddings on every axis that matters
here: no model call in the turn loop, no index to keep warm, and a retrieval decision you
can read off the row. Reach for embeddings when the bank is large enough that lexical
overlap actually misses — not before.

The bank is a SEED POOL, not a script: retrieved rows are labelled SUGGESTED in the prompt
and the moderator directive outranks them (plan-v3.md §2 precedence).
"""
from __future__ import annotations

import csv
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Set

from sqlalchemy.orm import Session

from app.models import InterviewQuestion

MAX_CANDIDATES = 5
MAX_QUESTION_CHARS = 200

_STOP = {
    "the", "a", "an", "and", "or", "but", "if", "then", "than", "that", "this", "these",
    "those", "is", "are", "was", "were", "be", "been", "being", "to", "of", "in", "on",
    "for", "with", "at", "by", "from", "as", "it", "its", "i", "we", "you", "they", "he",
    "she", "my", "our", "your", "how", "what", "why", "when", "do", "did", "does",
    "have", "has", "had", "would", "could", "should", "will", "can", "about", "so",
}


def _terms(text: str) -> Set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(w) > 2 and w not in _STOP}


def seed_from_csv(db: Session, path: Optional[str] = None) -> int:
    """Idempotent load of the curated bank. Returns the number of rows inserted.

    Ships in-repo so the bank works offline and reproducibly. A third-party dataset (e.g.
    a Kaggle interview-question set) can be ingested through the same table, but each row
    must carry its own `source` and `licence` — that is why those columns exist, and why a
    dataset whose licence forbids redistribution gets ingested at deploy time rather than
    vendored here.
    """
    path = path or os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                "data", "interview_questions.csv")
    if not os.path.exists(path):
        return 0
    existing = {q for (q,) in db.query(InterviewQuestion.question).all()}
    added = 0
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            question = (row.get("question") or "").strip()
            if not question or question in existing:
                continue
            try:
                difficulty = max(1, min(5, int(row.get("difficulty") or 3)))
            except (TypeError, ValueError):
                difficulty = 3
            db.add(InterviewQuestion(
                skill_id=(row.get("skill_id") or "").strip(),
                persona=(row.get("persona") or "any").strip() or "any",
                difficulty=difficulty,
                kind=(row.get("kind") or "open").strip() or "open",
                question=question,
                source=(row.get("source") or "curated").strip(),
                licence=(row.get("licence") or "CC0").strip(),
            ))
            existing.add(question)
            added += 1
    if added:
        db.commit()
    return added


def retrieve(db: Session, *, persona: str, required_skill_ids: Iterable[str],
             target_skill_id: Optional[str] = None, difficulty: int = 3,
             asked_ids: Optional[Iterable[str]] = None,
             hint_text: str = "", limit: int = MAX_CANDIDATES) -> List[Dict[str, Any]]:
    """Filter on metadata in SQL, then rank in Python by term overlap.

    Rows with an empty `skill_id` are persona-generic openers and stay eligible — without
    them the behavioural and hiring-manager personas would have almost nothing to draw on,
    since their questions are not skill-scoped.
    """
    asked = set(asked_ids or ())
    skills = [s for s in (required_skill_ids or []) if s]
    band = {max(1, difficulty - 1), difficulty, min(5, difficulty + 1)}

    q = db.query(InterviewQuestion).filter(
        InterviewQuestion.persona.in_([persona, "any"]),
        InterviewQuestion.difficulty.in_(sorted(band)),
    )
    if skills:
        q = q.filter(InterviewQuestion.skill_id.in_(list(skills) + [""]))
    else:
        q = q.filter(InterviewQuestion.skill_id == "")

    rows = [r for r in q.limit(200).all() if r.id not in asked]
    if not rows:
        return []

    want = _terms(hint_text)
    if target_skill_id:
        want |= _terms(target_skill_id.replace("_", " "))

    def score(r: InterviewQuestion) -> tuple:
        overlap = len(want & _terms(r.question)) if want else 0
        on_target = 1 if (target_skill_id and r.skill_id == target_skill_id) else 0
        exact_band = 1 if r.difficulty == difficulty else 0
        skill_scoped = 1 if r.skill_id else 0
        return (on_target, overlap, exact_band, skill_scoped)

    rows.sort(key=score, reverse=True)
    return [{
        "id": r.id, "question": r.question[:MAX_QUESTION_CHARS], "kind": r.kind,
        "skill_id": r.skill_id, "difficulty": r.difficulty,
    } for r in rows[:limit]]


def render_block(candidates: List[Dict[str, Any]]) -> str:
    """The prompt block. The SUGGESTED label is load-bearing: it is what stops a retrieved
    row from competing with the moderator directive (plan-v3.md §7 Clash)."""
    if not candidates:
        return ""
    lines = ["SUGGESTED questions from the bank — the MODERATOR DIRECTIVE outranks these.",
             "Adapt or discard them freely; never read one out verbatim if the "
             "conversation has moved on."]
    for c in candidates:
        lines.append("  - ({}/5, {}) {}".format(c["difficulty"], c["kind"], c["question"]))
    return "\n".join(lines)
