"""A VAD cycle outside the candidate's actual turn must not be treated as an answer.

Reported bug: mid-interview, a noise burst (room noise, a click, the tail of the
candidate's own breath) between "the moderator just decided who speaks next" and "that
persona has actually started talking" was accepted as a real candidate turn. Two things
followed from that: a spurious `<noise>`-only turn got persisted, and a *second* moderator
decision fired in quick succession — regranting the floor to a persona whose PREVIOUS
turn had not yet settled (session.py's TURN_SETTLE_S debounce), so the two generations'
text landed in the same accumulator and were displayed as one merged, double-question
interviewer turn.

`_awaiting_candidate` (session.py) closes that gap: on_speech_start/on_speech_end now do
nothing unless it is either genuinely the candidate's turn, or the candidate is barging
into a persona turn that is actively producing output (`_persona_turn_open`).

Written against plain asyncio (no pytest-asyncio dependency — it is not installed in
every environment this suite runs in, and nothing else here relies on it).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.interview import session as RT


def _runtime() -> RT.InterviewRuntime:
    grounding = {"job_block": "", "candidate_block": "", "claims": [], "required_skill_ids": []}
    return RT.InterviewRuntime(
        session_id="test-session", user_id="1", panel=["tech"], preset="screen",
        grounding=grounding, emit=AsyncMock(), emit_audio=AsyncMock(),
    )


def _run(coro):
    return asyncio.run(coro)


def _run_until_settled(rt, coro):
    """Run `coro`, then drain the turn-decision task it schedules.

    `on_speech_end` no longer awaits `_advance_turn` inline. It waits for the tail of
    the candidate's transcription and then runs the analyst — seconds of work — and
    doing that inline parked the WebSocket read loop, so the transport stopped
    answering the browser's keepalive pings and dropped the candidate mid-answer with
    a 1011. The decision now runs in `_settle_task`, which means a test that asserts
    on it has to wait for it rather than assume it already happened.
    """
    async def go():
        await coro
        task = getattr(rt, "_settle_task", None)
        if task is not None:
            await asyncio.wait_for(task, timeout=5)
    return asyncio.run(go())


def test_speech_end_in_the_gap_is_ignored_and_clears_the_buffer():
    """Neither awaiting the candidate nor mid persona-turn: a stray VAD cycle here is
    room noise, not an answer."""
    rt = _runtime()
    rt._advance_turn = AsyncMock()
    assert rt._awaiting_candidate is False
    assert rt._persona_turn_open is False

    rt._cand_buf = ["<noise>", " <noise>"]
    _run(rt.on_speech_end())

    rt._advance_turn.assert_not_awaited()
    assert rt._cand_buf == []
    assert rt._cand_turn_started_ms == 0


def test_speech_end_during_the_candidates_real_turn_still_advances():
    rt = _runtime()
    rt._advance_turn = AsyncMock()
    rt._awaiting_candidate = True

    _run_until_settled(rt, rt.on_speech_end())

    rt._advance_turn.assert_awaited_once()


def test_speech_end_mid_barge_in_still_advances():
    """persona_turn_open=True means the candidate genuinely interrupted a live
    interviewer turn — speech_end there must still hand the floor back to the
    moderator, even though `_awaiting_candidate` is False during a persona's turn."""
    rt = _runtime()
    rt._advance_turn = AsyncMock()
    rt._persona_turn_open = True

    _run_until_settled(rt, rt.on_speech_end())

    rt._advance_turn.assert_awaited_once()


def test_speech_start_in_the_gap_does_not_signal_or_interrupt():
    rt = _runtime()
    rt.emit = AsyncMock()
    assert rt._awaiting_candidate is False
    assert rt._persona_turn_open is False

    _run(rt.on_speech_start())

    rt.emit.assert_not_awaited()
    assert rt._cand_turn_started_ms == 0


def test_speech_start_while_awaiting_candidate_proceeds():
    rt = _runtime()
    rt.emit = AsyncMock()
    rt._awaiting_candidate = True

    _run(rt.on_speech_start())

    rt.emit.assert_awaited_with({"type": "interrupted", "reason": "candidate_speaking"})


def test_activity_end_in_the_gap_is_also_ignored():
    """The explicit 'I'm done answering' button goes through the same guard."""
    rt = _runtime()
    rt._advance_turn = AsyncMock()

    _run(rt.on_activity_end())

    rt._advance_turn.assert_not_awaited()


def test_your_turn_emit_reopens_the_awaiting_window():
    """The other half of the fix: _awaiting_candidate must flip back to True exactly
    when `your_turn` is emitted, or a legitimate answer right after it would itself be
    dropped by the new guard."""
    rt = _runtime()
    rt._cancel_turn_deadline = lambda: None
    rt._maybe_summarise = AsyncMock()

    _run(rt._after_persona_turn("tech"))

    assert rt._awaiting_candidate is True
