"""Static guard for a candidate-facing transcript-duplication bug.

The backend settles a late transcription tail by merging it into the SAME turn_id and
re-emitting it already `final: true` (session.py `_append_to_turn`). Both frontend
transcript renderers (the candidate room and the employer monitor) used to key their
merge purely on "same speaker as the last row, and that row isn't final yet" — which is
false the second time, since the first message already set `final: true`. The result
was the candidate's own answer appearing twice: once truncated, once fully merged.

A full browser pass is the real check; this catches the exact regression cheaply, in
the suite, with the reason attached — see test_responsive_guards.py for the same
pattern applied to a different bug.
"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
ROOM_JS = (ROOT / "frontend" / "js" / "interview" / "room.js").read_text()
MONITOR_JS = (ROOT / "frontend" / "js" / "views" / "monitor.js").read_text()


def test_candidate_room_merges_transcript_updates_by_turn_id():
    assert "turn_id === msg.turn_id" in ROOM_JS or "t.turn_id === msg.turn_id" in ROOM_JS, (
        "_pushTurn must match an incoming transcript update against an existing row by "
        "turn_id before falling back to the speaker+final heuristic, or a late-settled "
        "candidate answer renders as a duplicate row"
    )


def test_employer_monitor_merges_transcript_updates_by_turn_id():
    assert "t.turn_id === ev.turn_id" in MONITOR_JS, (
        "the monitor's transcript case must match by turn_id first, same reasoning as "
        "room.js's _pushTurn"
    )
