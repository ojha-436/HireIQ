"""Role-play scenarios — PS11 requirement #6, "role-play or scenario-based questions".

WHY A SEPARATE ENGINE
---------------------
A scenario is not just a harder question. Three things make it different, and each one
needs state the question bank cannot hold:

  1. The persona STAYS IN CHARACTER across several turns. It is no longer an interviewer
     asking about a customer; it IS the customer.
  2. It ESCALATES with difficulty. At level 3 the customer is puzzled; at level 5 they
     are threatening the renewal. The escalation is content, not tone.
  3. It is scored on different dimensions. A role-play measures user insight, clarity
     and ownership — not correctness. Scoring it like a technical answer would punish
     candidates for the thing the exercise is actually testing.

Scenarios are seeded rows (see scripts/seed_scenarios.py) selected by role family and
the skills the job actually requires, so a backend candidate does not get handed a
pricing negotiation.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Scenario

log = logging.getLogger("hireiq.scenarios")

#: Turns a scenario holds the floor before the moderator takes it back. Long enough to
#: force a real exchange, short enough to leave room for the rest of the panel.
MAX_SCENARIO_TURNS = 3

#: A scenario needs room to breathe; below this we do not open one at all.
MIN_SECONDS_REMAINING = 240


class ScenarioState:
    """Live state for one running scenario. Serialised into session.state_json."""

    def __init__(self, scenario_id: str, persona: str, title: str,
                 escalations: dict[str, str], success_signals: list[str]) -> None:
        self.scenario_id = scenario_id
        self.persona = persona
        self.title = title
        self.escalations = escalations or {}
        self.success_signals = success_signals or []
        self.turns = 0
        self.phase = "open"          # open | escalate | close
        self.escalated_at: list[int] = []

    # -- lifecycle -------------------------------------------------------
    def note_turn(self) -> None:
        self.turns += 1
        self.phase = "escalate" if self.turns < MAX_SCENARIO_TURNS else "close"

    @property
    def finished(self) -> bool:
        return self.turns >= MAX_SCENARIO_TURNS

    def escalation_for(self, difficulty: int) -> Optional[str]:
        """The inject for this difficulty band, once. Escalations do not repeat."""
        key = str(int(difficulty))
        if key in self.escalated_at_keys():
            return None
        text = self.escalations.get(key)
        if text:
            self.escalated_at.append(int(difficulty))
        return text

    def escalated_at_keys(self) -> set[str]:
        return {str(d) for d in self.escalated_at}

    # -- serialisation ---------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id, "persona": self.persona,
            "title": self.title, "escalations": self.escalations,
            "success_signals": self.success_signals, "turns": self.turns,
            "phase": self.phase, "escalated_at": self.escalated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScenarioState":
        state = cls(data["scenario_id"], data["persona"], data.get("title", ""),
                    data.get("escalations") or {}, data.get("success_signals") or [])
        state.turns = int(data.get("turns", 0))
        state.phase = data.get("phase", "open")
        state.escalated_at = list(data.get("escalated_at") or [])
        return state


# --------------------------------------------------------------------------- selection
def pick(db: Session, *, panel: list[str], skill_ids: list[str], difficulty: int,
         role_family: str = "") -> Optional[Scenario]:
    """Best scenario for this panel and job, or None.

    Ordered by: an owner actually sitting on the panel (a Customer scenario with no
    Customer interviewer is unplayable), then skill overlap, then how close the
    scenario's floor is to the candidate's current level.
    """
    rows = db.scalars(select(Scenario)).all()
    if not rows:
        return None

    # Callers may pass skill ids ("system_design") or display names ("System Design").
    # Normalise both sides to a common form rather than trusting the caller — the same
    # vocabulary mismatch has bitten job matching and scenario ranking already.
    def norm(value: str) -> str:
        return "".join(ch for ch in value.lower() if ch.isalnum())

    wanted = {norm(s) for s in skill_ids}
    seated = set(panel)

    def score(sc: Scenario) -> tuple[int, int, int]:
        if sc.persona_owner not in seated:
            return (-1, 0, 0)
        overlap = len({norm(s) for s in (sc.skills_json or [])} & wanted)
        family = 1 if (role_family and sc.role_family == role_family) else 0
        # Prefer a floor at or just below the candidate's level.
        closeness = -abs((sc.difficulty_floor or 3) - difficulty)
        return (family * 10 + overlap, closeness, 0)

    best = max(rows, key=score)
    if best.persona_owner not in seated:
        log.info("no scenario owner on this panel; skipping role-play")
        return None
    return best


def start(scenario: Scenario) -> ScenarioState:
    return ScenarioState(
        scenario_id=str(scenario.id), persona=scenario.persona_owner,
        title=scenario.title, escalations=scenario.escalations_json or {},
        success_signals=scenario.success_signals_json or [])


# ------------------------------------------------------------------------- prompt block
def opening_block(scenario: Scenario) -> str:
    """The in-character brief handed to the owning persona when the scenario opens."""
    injects = scenario.injects_json or []
    lines = [
        "ROLE-PLAY. You are no longer interviewing. You ARE the person described below,",
        "speaking to the candidate in the situation described. Stay in character.",
        "",
        "SITUATION: {}".format(scenario.setup_text.strip()),
    ]
    if injects:
        lines.append("Details you know and may reveal if pushed: {}".format("; ".join(injects[:4])))
    lines += [
        "",
        "Open with the situation in your own words, in under 40 spoken words. Do not",
        "announce that this is a role-play, do not narrate, do not break character to",
        "explain the exercise. Ask what they will do. Then stop and let them answer.",
    ]
    return "\n".join(lines)


def continuation_block(state: ScenarioState, difficulty: int) -> str:
    """The brief for each subsequent in-character turn."""
    lines = [
        "ROLE-PLAY CONTINUES. You are still the person from the situation, not an",
        "interviewer. Respond as they would to what the candidate just said.",
    ]
    escalation = state.escalation_for(difficulty)
    if escalation:
        lines += ["", "The situation has escalated: {}".format(escalation.strip())]
    if state.phase == "close":
        lines += ["", "This is your last turn in character. React briefly, then stop."]
    lines += [
        "",
        "Under 40 spoken words. Stay in character. Do not score, coach, or summarise.",
    ]
    return "\n".join(lines)


#: A role-play measures whether the candidate can read a person and act, so these are
#: the dimensions the analyst is told to weigh. Correctness is deliberately absent.
SCENARIO_DIMENSIONS = ["user_insight", "clarity", "ownership", "prioritisation"]
