"""Reported bug: without a real GEMINI_API_KEY (the offline `_LocalConnection` stand-in
never transcribes candidate audio at all — see send_audio), a candidate who only speaks
and never types looks silent forever. Each silent turn fires session.py's
`_nudge_silence()`, which sends its nudge instruction through the SAME `send_text()` the
persona's real turns use. `_LocalConnection._speak()` used to treat every send_text call
identically -- cycling `_LINES[persona_key]` regardless of why it was called -- so two
silent nudges after an opening line (index 0) landed back on index 0 % 2 and replayed
the interviewer's OPENING LINE VERBATIM. In the room, that reads as the panel talking to
itself in a loop rather than a live interview.

Nudges must get their own short, generic reply and must never advance or repeat the
persona's real line cycle.
"""
from __future__ import annotations

import asyncio

from app.interview import live_client as LC


def _run(coro):
    return asyncio.run(coro)


def test_a_nudge_does_not_repeat_or_advance_the_personas_real_lines():
    conn = LC._LocalConnection("hr")

    async def drain_transcripts(n: int) -> list[str]:
        out = []
        for _ in range(n):
            while True:
                ev = await conn._queue.get()
                if ev["type"] == LC.EV_OUTPUT_TRANSCRIPT:
                    out.append(ev["text"])
                if ev["type"] == LC.EV_TURN_COMPLETE:
                    break
        return out

    async def go():
        # Turn 1: the real opening line (first_turn goes through send_text with the
        # normal turn prompt, not the nudge marker).
        await conn.send_text("normal turn prompt", end_of_turn=True)
        first = await drain_transcripts(1)

        # Two silent-turn nudges, exactly as _nudge_silence sends them.
        await conn.send_text(LC.NUDGE_MARKER + " invite them to answer", end_of_turn=True)
        await conn.send_text(LC.NUDGE_MARKER + " invite them to answer", end_of_turn=True)
        nudges = await drain_transcripts(2)
        return first, nudges

    first, nudges = _run(go())

    opening_line = LC._LocalConnection._LINES["hr"][0]
    assert first == [opening_line]
    # Neither nudge is the opening line repeated, and the persona's own turn counter
    # never moved (still on its second real line, not wrapped back to the first).
    assert opening_line not in nudges
    assert conn._turn == 1
