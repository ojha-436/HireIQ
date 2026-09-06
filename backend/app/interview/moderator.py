"""Turn-taking (plan-v3.md §5.3).

A deterministic state machine with an LLM tiebreak — not a free-running agent. "Controlled
turn-taking" is a PS11 requirement, and a model asked "who should speak next?" every turn
is neither controlled nor reproducible.

Rules fire in priority order. R2 is the PS11 example scenario, and it is a HARD RULE
precisely because the whole demo turns on it: a technically correct answer with no story
about customer impact must move the floor to the product interviewer. A prompt hoping for
that behaviour would work most of the time, which is the worst possible property for the
centrepiece of a submission.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from app.config import settings
from app.interview import gemini as GEM
from app.interview import personas as P

INTENTS = ("open", "followup", "challenge", "scenario", "clarify", "wrap")

# EWMA weight for the per-skill difficulty signal. 0.4 tracks the last couple of answers
# without letting one bad turn collapse the band.
_EWMA_ALPHA = 0.4
# Difficulty moves at most one band per turn so it ramps instead of lurching.
_MAX_STEP = 1

#: A scenario needs room; opening one with three minutes left produces a stub.
SCENARIO_MIN_SECONDS = 240
#: Turns a role-play consumes, reserved before opening so it cannot run out mid-scene.
SCENARIO_TURN_COST = 3

#: The HR/intro persona (personas.py) opens every interview, but is not a co-equal seat in
#: the ongoing rotation — see Moderator._next_unserved.
INTRO_PERSONA = "hr"


class Moderator:
    """Owns the floor, the difficulty bands, and the unresolved-flag store.

    The flag store is the "shared candidate context" of plan-v3.md §3.4 in concrete form:
    one dict, written by the analyst, read by whichever persona next holds the floor. An
    unresolved `vague` raised by `tech` is still visible to `hiring_manager` three turns
    later — that is what makes the panel feel like one panel.
    """

    def __init__(self, panel: List[str], *, required_skill_ids: Optional[List[str]] = None,
                 max_turns: int = 14) -> None:
        self.panel = list(panel) or ["tech"]
        self.required = list(required_skill_ids or [])
        self.max_turns = max_turns

        self.current: Optional[str] = None
        self.turns_by_persona: Dict[str, int] = {k: 0 for k in self.panel}
        self.open_flags: List[Dict[str, Any]] = []
        self.resolved_flag_keys: set = set()
        self.difficulty: Dict[str, float] = {s: 3.0 for s in self.required}
        self.global_difficulty = 3.0
        self.asked_question_ids: List[str] = []
        self.established: Dict[str, Any] = {"numbers": {}, "facts": []}
        self.history: List[Dict[str, Any]] = []      # decisions, for the trace panel
        self._impact_challenges = 0
        # Role-play state (PS11 #6). `scenario` is None until R7 opens one, and is set
        # back to None when it closes so R7 cannot re-fire in the same interview.
        self.scenario: Optional[Any] = None
        self.scenario_done = False
        self.exchanges_on_target = 0
        self.seconds_remaining = 9999
        # Each persona gets roughly a fair share of the interview.
        self._budget = max(2, max_turns // max(1, len(self.panel)))

    # -- turn budget ------------------------------------------------------

    @property
    def turns_taken(self) -> int:
        return sum(self.turns_by_persona.values())

    def _next_unserved(self) -> Optional[str]:
        """The panel member furthest behind its share, in panel order.

        The HR/intro persona is excluded here once it has opened the interview: its role
        is a one-time warm welcome, not an equal seat in the substantive rotation. Without
        this, every other panelist eventually catches up to its turn count and R5 hands the
        floor back to small talk for the rest of the session.
        """
        best, best_count = None, None
        for k in self.panel:
            if k == INTRO_PERSONA and self.turns_by_persona.get(k, 0) > 0:
                continue
            c = self.turns_by_persona.get(k, 0)
            if best_count is None or c < best_count:
                best, best_count = k, c
        return best

    # -- state ingestion --------------------------------------------------

    def note_turn(self, persona: str) -> None:
        self.turns_by_persona[persona] = self.turns_by_persona.get(persona, 0) + 1
        self.current = persona
        if self.scenario is not None:
            self.scenario.note_turn()
            if self.scenario.finished:
                self.scenario = None
                self.scenario_done = True

    def ingest(self, analysis: Dict[str, Any], *, target_skill_id: Optional[str] = None) -> None:
        """Fold one AnswerAnalysis into difficulty, flags and established facts."""
        self.exchanges_on_target += 1
        scores = analysis.get("scores") or {}
        signal = [v for k, v in scores.items()
                  if k in ("correctness", "depth", "impact", "structure", "ownership")]
        if signal:
            observed = sum(signal) / float(len(signal))
            self.global_difficulty = (1 - _EWMA_ALPHA) * self.global_difficulty + _EWMA_ALPHA * observed
            if target_skill_id:
                prev = self.difficulty.get(target_skill_id, 3.0)
                self.difficulty[target_skill_id] = (1 - _EWMA_ALPHA) * prev + _EWMA_ALPHA * observed

        for f in (analysis.get("flags") or []):
            key = (f.get("type"), tuple(f.get("turn_ids") or ()))
            if key in self.resolved_flag_keys:
                continue
            if not any((g.get("type"), tuple(g.get("turn_ids") or ())) == key
                       for g in self.open_flags):
                self.open_flags.append(dict(f))

        # Remember numbers so a later answer can be caught contradicting them.
        turn_id = analysis.get("turn_id")
        for token in (analysis.get("numbers") or []):
            key = "".join(ch for ch in token if not (ch.isdigit() or ch in ".,")).strip().lower()
            if key and key not in self.established["numbers"]:
                self.established["numbers"][key] = {"value": token, "turn_id": turn_id}

    def resolve(self, flag_type: str) -> None:
        """Mark the oldest open flag of this type handled, so the panel stops re-asking."""
        for i, f in enumerate(self.open_flags):
            if f.get("type") == flag_type:
                self.resolved_flag_keys.add((f.get("type"), tuple(f.get("turn_ids") or ())))
                self.open_flags.pop(i)
                return

    # -- role-play ---------------------------------------------------------
    #: Set by the runtime before decide(), which owns DB access. The moderator stays a
    #: pure function of its own state — it never queries for a scenario itself.
    pending_scenario_persona: str = ""

    def scenario_owner_available(self) -> bool:
        return bool(self.pending_scenario_persona
                    and self.pending_scenario_persona in self.panel)

    def open_scenario(self, state: Any) -> None:
        self.scenario = state

    def band_for(self, skill_id: Optional[str]) -> int:
        raw = self.difficulty.get(skill_id, self.global_difficulty) if skill_id else self.global_difficulty
        # Difficulty tracks performance: answer well, get harder questions.
        target = 1 + (raw / 5.0) * 4.0
        cur = 3 if self.current is None else int(round(target))
        step = max(-_MAX_STEP, min(_MAX_STEP, int(round(target)) - cur))
        return max(1, min(5, int(round(target)) if abs(step) <= _MAX_STEP else cur + step))

    # -- the decision -----------------------------------------------------

    def decide(self, analysis: Optional[Dict[str, Any]] = None, *,
               target_skill_id: Optional[str] = None) -> Dict[str, Any]:
        """Return a TurnDirective. Rules are checked in priority order; the LLM only sees
        the cases none of them cover, and even then its answer is clamped to legal moves."""
        if self.turns_taken >= self.max_turns:
            return self._directive("close", "wrap", target_skill_id,
                                   reason="Turn budget exhausted.")

        a = analysis or {}
        scores = a.get("scores") or {}
        specificity = a.get("specificity")
        impact_stated = bool(a.get("impact_stated"))
        flag_types = {f.get("type") for f in (a.get("flags") or [])}
        open_types = {f.get("type") for f in self.open_flags}
        cur = self.current or self.panel[0]

        # R1 — a contradiction outranks everything: resolve it before moving on.
        if "contradiction" in flag_types or "contradiction" in open_types:
            f = next((f for f in self.open_flags if f.get("type") == "contradiction"), None)
            return self._directive(cur, "clarify", target_skill_id,
                                   reason="Contradiction raised — same interviewer resolves it.",
                                   must_reference=(f or {}).get("turn_ids") or [],
                                   rule="R1")

        # R2 — THE PS11 SCENARIO. Technically sound, no customer/business story, and a
        # product interviewer is on the panel: hand over and make them answer for impact.
        if (scores.get("correctness", 0) >= 3 and not impact_stated
                and "product" in self.panel and cur != "product"
                and self._impact_challenges < 3):
            self._impact_challenges += 1
            f = next((f for f in self.open_flags if f.get("type") == "impact_gap"), None)
            return self._directive(
                "product", "challenge", target_skill_id,
                reason="Answer is technically correct but states no impact — the product "
                       "interviewer challenges the business implications.",
                must_reference=(f or {}).get("turn_ids") or ([a["turn_id"]] if a.get("turn_id") else []),
                rule="R2")

        # R3 — vague: stay put and push for specifics rather than moving on politely.
        if specificity == "vague" or "vague" in flag_types:
            f = next((f for f in self.open_flags if f.get("type") == "vague"), None)
            return self._directive(cur, "followup", target_skill_id,
                                   reason="Answer was vague — press for specifics.",
                                   must_reference=(f or {}).get("turn_ids") or [],
                                   difficulty_delta=0, rule="R3")

        # R4 — a résumé claim the candidate cannot substantiate is the hiring manager's job.
        if a.get("claims_unsupported") or "unsupported_claim" in flag_types:
            if "hiring_manager" in self.panel and cur != "hiring_manager":
                return self._directive("hiring_manager", "challenge", target_skill_id,
                                       reason="A profile claim went unsubstantiated.",
                                       rule="R4")

        # R6 — heavy unexplained jargon, with a customer on the panel to call it out.
        if "jargon" in flag_types and "customer" in self.panel and cur != "customer":
            return self._directive("customer", "clarify", target_skill_id,
                                   reason="Dense jargon — the customer asks for plain language.",
                                   rule="R6")

        # R7 — nothing urgent is outstanding and the panel has established enough to
        # role-play against. Hand the floor to the scenario owner, in character.
        # Fires at most once per interview: a second role-play would eat the time the
        # panel needs to cover the remaining skills.
        if (self.scenario is None and not self.scenario_done
                and self.exchanges_on_target >= 2
                and self.seconds_remaining >= SCENARIO_MIN_SECONDS
                and self.turns_taken + SCENARIO_TURN_COST <= self.max_turns
                and self.scenario_owner_available()):
            return self._directive(
                self.pending_scenario_persona, "scenario", target_skill_id,
                reason="Enough established to role-play — opening a scenario.",
                rule="R7")

        # A scenario in progress keeps the floor: breaking character mid-exchange
        # destroys the exercise.
        if self.scenario is not None:
            return self._directive(
                self.scenario.persona, "scenario", target_skill_id,
                reason="Role-play in progress — the owner keeps the floor.",
                rule="R7")

        # R5 — nobody is owed anything urgent: give the floor to whoever is furthest behind.
        nxt = self._next_unserved() or cur
        if nxt != cur:
            return self._directive(nxt, "open", target_skill_id,
                                   reason="Rotating the floor to keep the panel balanced.",
                                   rule="R5")

        # Nothing decided it — ask the model, then clamp whatever it says.
        return self._tiebreak(a, target_skill_id, cur)

    def _directive(self, speaker: str, intent: str, target_skill_id: Optional[str], *,
                   reason: str = "", must_reference: Optional[List[str]] = None,
                   difficulty_delta: Optional[int] = None, rule: str = "") -> Dict[str, Any]:
        band = self.band_for(target_skill_id)
        if difficulty_delta is not None:
            band = max(1, min(5, band + difficulty_delta))
        d = {
            "next_speaker": speaker if speaker in self.panel or speaker == "close" else self.panel[0],
            "intent": intent if intent in INTENTS else "followup",
            "difficulty": band,
            "target_skill_id": target_skill_id,
            "must_reference_turn_ids": list(must_reference or []),
            "reason": reason,
            "rule": rule or "tiebreak",
        }
        self.history.append(d)
        return d

    def _tiebreak(self, analysis: Dict[str, Any], target_skill_id: Optional[str],
                  cur: str) -> Dict[str, Any]:
        legal = list(self.panel)
        if not GEM.available():
            return self._directive(cur, "followup", target_skill_id,
                                   reason="No rule fired; continuing with the same interviewer.",
                                   rule="R0")
        prompt = (
                "You are the moderator of an interview panel. Choose who speaks next.\n"
                "Legal speakers: {legal}\nCurrent speaker: {cur}\n"
                "Turns each has taken: {taken}\n"
                "Analysis of the last answer: {a}\n"
                "Open unresolved flags: {flags}\n\n"
                'Return ONLY JSON: {{"next_speaker":"...","intent":"open|followup|challenge|'
                'scenario|clarify","reason":"one short sentence"}}'
        ).format(legal=", ".join(legal), cur=cur,
                 taken=json.dumps(self.turns_by_persona),
                 a=json.dumps({k: analysis.get(k) for k in
                               ("scores", "specificity", "impact_stated")})[:800],
                 flags=json.dumps([f.get("type") for f in self.open_flags]))

        data = GEM.generate_json(prompt, temperature=0.2)
        if not isinstance(data, dict):
            return self._directive(cur, "followup", target_skill_id,
                                   reason="Tiebreak unavailable; same interviewer continues.",
                                   rule="R0")

        speaker = data.get("next_speaker")
        intent = data.get("intent")
        # Clamped to legal moves: the model advises, it does not get to invent a persona.
        return self._directive(
            speaker if speaker in legal else cur,
            intent if intent in INTENTS else "followup",
            target_skill_id,
            reason=str(data.get("reason") or "")[:200] or "Model tiebreak.",
            rule="tiebreak")

    # -- prompt block -----------------------------------------------------

    def directive_block(self, directive: Dict[str, Any], turn_text: Dict[str, str]) -> str:
        """Render the directive for the incoming persona, including the shared flag store
        so a persona inherits what the panel already noticed."""
        lines = [
            "You have the floor. Intent: {}. Difficulty: {}/5.".format(
                directive["intent"], directive["difficulty"]),
        ]
        if directive.get("target_skill_id"):
            lines.append("Focus skill: {}.".format(directive["target_skill_id"]))
        for tid in directive.get("must_reference_turn_ids") or []:
            quote = (turn_text.get(tid) or "").strip()
            if quote:
                lines.append('You MUST refer to what they said earlier: "{}"'.format(quote[:240]))
        if self.open_flags:
            lines.append("Unresolved concerns the panel has raised (any of you may pursue these):")
            for f in self.open_flags[:4]:
                lines.append("  - {}: {}".format(f.get("type"), f.get("note", "")))
        if directive["intent"] == "wrap":
            lines.append("Close the interview politely in one or two sentences. Ask nothing further.")
        return "\n".join(lines)
