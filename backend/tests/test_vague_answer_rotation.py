"""R3 (vague-answer) used to keep the same speaker indefinitely: nothing capped how
many consecutive times it could fire. A run of short/unclear answers -- exactly what a
bad connection or a mic issue produces -- meant one persona (visibly, whichever one
opens the interview, since it is always `cur` for the first exchange) ended up asking
every question for the rest of the session. Reported live: "only Donna is asking all
questions, both intro and technical."

MAX_VAGUE_FOLLOWUPS caps it. After that many consecutive vague answers with the same
interviewer, the panel is guaranteed to move on regardless of how many more vague
answers arrive.
"""
from __future__ import annotations

from app.interview.moderator import MAX_VAGUE_FOLLOWUPS, Moderator

VAGUE = {"scores": {}, "specificity": "vague", "impact_stated": False, "flags": [],
         "turn_id": "t1"}


def _mod(panel, **kw):
    m = Moderator(panel, required_skill_ids=["python"], **kw)
    m.note_turn(panel[0])
    return m


def test_vague_streak_forces_rotation_after_the_cap():
    m = _mod(["hr", "tech", "product", "hiring_manager"], max_turns=14)

    seen_speakers = []
    speaker = "hr"
    for _ in range(MAX_VAGUE_FOLLOWUPS + 2):
        d = m.decide(VAGUE, target_skill_id="python")
        seen_speakers.append(d["next_speaker"])
        speaker = d["next_speaker"]
        m.note_turn(speaker)

    # The first MAX_VAGUE_FOLLOWUPS decisions may legitimately keep "hr" for a
    # followup; strictly after that many, it must have moved on.
    assert seen_speakers[MAX_VAGUE_FOLLOWUPS] != "hr", (
        f"expected rotation away from hr after {MAX_VAGUE_FOLLOWUPS} vague followups, "
        f"got {seen_speakers}"
    )
    # And once it has moved on, hr (excluded from ongoing rotation regardless — see
    # Moderator._next_unserved) must not be selected again by a fresh vague streak.
    assert "hr" not in seen_speakers[MAX_VAGUE_FOLLOWUPS:], seen_speakers


def test_a_clear_answer_resets_the_streak():
    m = _mod(["tech", "product", "hiring_manager"], max_turns=14)
    clear = {"scores": {"correctness": 4}, "specificity": "specific",
             "impact_stated": True, "flags": [], "turn_id": "t1"}

    # One vague answer keeps "tech" (streak=1) — note_turn("tech") here is a genuine
    # same-speaker repeat (current is already "tech" via _mod), not a fresh assignment,
    # so it must NOT reset the streak on its own.
    d = m.decide(VAGUE, target_skill_id="python")
    assert d["next_speaker"] == "tech"
    m.note_turn("tech")
    assert m._vague_streak == 1

    # A clear answer must reset it regardless of who ends up speaking next.
    d = m.decide(clear, target_skill_id="python")
    m.note_turn(d["next_speaker"])
    assert m._vague_streak == 0
