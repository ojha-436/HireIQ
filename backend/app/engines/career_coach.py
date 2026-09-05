"""Post-practice career coaching (Phase 13).

Coaching is deterministic and evidence-bound, exactly like the assessment it reads: the
weakest dimension is picked from the SAME per-dimension scores the report already computed
(never a fresh model judgement), and the one piece of prose it carries — the "observed"
line — is built from the evidence quote the assessment already cited. This module never
runs for a HIRING session; only `session.py`'s practice branch calls it.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# One concrete, practiceable tip per rubric dimension (personas.py.RUBRIC_DIMENSIONS).
_TIPS: Dict[str, str] = {
    "correctness": "Redo a problem of the same shape and narrate your reasoning out loud "
                   "before you give the answer.",
    "depth": "Pick one system you know well and go two levels deeper than your first "
             "instinct: what happens under load, and what fails first.",
    "tradeoffs": "For your last three technical decisions, write down the alternative you "
                 "rejected and the reason.",
    "impact": "For each project on your résumé, add one sentence: who benefited, and what "
              "changed for them — a number if you have one.",
    "prioritisation": "Practise explaining why you built X before Y, tied to a metric or a "
                       "deadline, not a preference.",
    "user_insight": "Before answering a product question, restate who the user is and what "
                     "they were trying to do.",
    "ownership": "Rewrite three of your stories replacing 'we' with 'I' everywhere you "
                 "personally made the call.",
    "scope": "Quantify what you owned: team size, budget, users affected, timeline.",
    "seniority": "Prepare one story about a decision you made under real ambiguity, with no "
                 "one to defer to.",
    "clarity": "Explain your last project to a non-technical friend in under 60 seconds — "
               "no jargon allowed.",
    "empathy": "Before answering a pushback, acknowledge the concern in one sentence before "
               "you address it.",
    "expectation_setting": "Practise saying plainly what you will NOT commit to, before "
                            "saying what you will.",
    "structure": "Tell three behavioural stories using Situation, Action, Result — out loud, "
                 "in that order.",
    "conflict": "Prepare one real story about disagreeing with a teammate or manager, and "
                "how it was resolved.",
    "self_awareness": "Prepare one honest failure story and what you would do differently — "
                       "no spin.",
    "communication": "Record yourself answering one question, then cut every filler word on "
                      "replay.",
}

_GENERIC_TIP = "Practise answering out loud, with a number or a named example in every answer."


def _weakest(per_dimension: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The lowest-scoring dimension that was actually probed — never an untested one."""
    probed = [d for d in per_dimension if d.get("times_probed")]
    if not probed:
        return None
    return min(probed, key=lambda d: d.get("score", 5))


def _evidence_quote(dim: Dict[str, Any]) -> str:
    for line in dim.get("evidence") or []:
        if line.get("quote"):
            return line["quote"]
    return ""


def build_plan(report: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A 7-day improvement plan grounded in this session's own weakest dimension.

    Returns None when there is nothing to coach on (no scored answers) — a coaching
    plan built on zero evidence would be generic advice wearing a costume.
    """
    per_dimension = report.get("per_dimension") or []
    weak = _weakest(per_dimension)
    if weak is None:
        return None

    dim_name = str(weak["dimension"])
    tip = _TIPS.get(dim_name, _GENERIC_TIP)
    quote = _evidence_quote(weak)
    label = dim_name.replace("_", " ")

    observed = (
        "Across this session, {label} averaged {score}/5 — your lowest scored area."
        .format(label=label, score=weak.get("score"))
    )
    if quote:
        observed += ' You said: "{}"'.format(quote[:200])

    plan = [
        {"day": 1, "focus": label.title(), "task": tip},
        {"day": 2, "focus": "STAR storytelling", "task": "Rebuild two of your stories in "
         "strict Situation / Action / Result order and time them under 90 seconds each."},
        {"day": 3, "focus": "Scenario practice", "task": "Run a role-play scenario against "
         "this same panel and stay in the details when it escalates."},
        {"day": 4, "focus": "Stakeholder communication", "task": "Explain one technical "
         "decision to an imagined non-technical stakeholder, out loud, twice."},
        {"day": 5, "focus": "Mock panel", "task": "Retake a full practice panel interview "
         "and try to name a metric in every answer."},
        {"day": 6, "focus": "{} drill".format(label.title()), "task": tip},
        {"day": 7, "focus": "Full interview", "task": "Retake this practice interview and "
         "compare your dimension scores turn for turn."},
    ]

    return {
        "weakest_dimension": dim_name,
        "weakest_score": weak.get("score"),
        "observed": observed,
        "plan": plan,
        "next_recommendation": "Practice {} again in about a week, once the drills above "
                                "are done.".format(label),
    }
