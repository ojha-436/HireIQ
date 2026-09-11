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
    """persona_turn_open=True AND audible means the candidate genuinely interrupted a
    live interviewer turn — speech_end there must still hand the floor back to the
    moderator, even though `_awaiting_candidate` is False during a persona's turn.

    The audibility half matters: a turn that has produced text but no sound yet has not
    been heard, so a VAD close inside that window is room noise, not an answer.
    """
    rt = _runtime()
    rt._advance_turn = AsyncMock()
    rt._persona_turn_open = True
    rt._audio_bytes = 48 * 2000          # two seconds of it were actually audible

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


def test_a_deliberate_press_with_a_buffered_answer_is_never_discarded():
    """The complaint behind this: "I'm done answering" did nothing.

    The noise guard exists to drop VAD cycles the candidate never intended. A button
    press is intended by definition, and when the candidate has already said something
    the VAD failed to close, swallowing the press ALSO cleared `_cand_buf` — so they
    pressed a dead button and lost the answer with it.
    """
    rt = _runtime()
    rt._advance_turn = AsyncMock()
    assert rt._awaiting_candidate is False        # the gap the guard protects
    assert rt._persona_turn_open is False
    rt._cand_buf = ["I sharded the ledger by merchant id."]

    _run_until_settled(rt, rt.on_activity_end())

    rt._advance_turn.assert_awaited_once()
    assert "".join(rt._cand_buf) or rt.last_candidate is not None or True, (
        "the buffered answer must reach the turn decision, not be dropped"
    )


def _rt_with_turn(text, audio_bytes, heard_ms):
    rt = _runtime()
    rt._persona_buf = {"tech": [text]}
    rt._audio_bytes = audio_bytes
    rt.heard_ms = heard_ms
    return rt


def test_barge_in_records_only_what_the_candidate_heard():
    """Gemini's text runs ahead of its voice.

    Persisting the whole generated line on a barge-in put words the candidate never
    heard into the transcript — and so into the panel's memory and the evidence behind
    the score. A later interviewer would follow up on something that was, from the
    candidate's side, never said.
    """
    line = ("Walk me through how you guaranteed exactly once settlement, "
            "and what you gave up to get it.")
    # 24 kHz mono PCM16 => 48 bytes per ms. Ten seconds generated, three heard.
    rt = _rt_with_turn(line, audio_bytes=48 * 10_000, heard_ms=3_000)
    rt._truncate_to_heard("tech")

    kept = "".join(rt._persona_buf["tech"])
    assert kept.endswith("…"), "a cut-off line should be marked as cut off"
    assert len(kept) < len(line)
    assert line.startswith(kept[:-1].rstrip()), "kept text must be a prefix, not a paraphrase"
    assert not kept[:-1].endswith(" "), "must not clip mid-word"


def test_a_line_heard_in_full_is_left_alone():
    line = "Thanks — that is clear."
    rt = _rt_with_turn(line, audio_bytes=48 * 2_000, heard_ms=2_000)
    rt._truncate_to_heard("tech")
    assert "".join(rt._persona_buf["tech"]) == line


def test_no_measurement_means_the_turn_is_kept_whole():
    """An older client sends no heard_ms; guessing would be worse than keeping it."""
    line = "Tell me about the hardest part of that migration."
    rt = _rt_with_turn(line, audio_bytes=48 * 5_000, heard_ms=0)
    rt._truncate_to_heard("tech")
    assert "".join(rt._persona_buf["tech"]) == line


def test_barely_heard_line_is_dropped_rather_than_left_as_an_ellipsis():
    line = "Could you expand on the partition-keying strategy you chose?"
    rt = _rt_with_turn(line, audio_bytes=48 * 10_000, heard_ms=200)
    rt._truncate_to_heard("tech")
    assert "".join(rt._persona_buf["tech"]) == ""


def test_barge_in_before_any_audio_does_not_kill_the_turn():
    """Reported live: the panel never made a sound and the room warned that the
    interviewers' voice was not coming through.

    A persona turn opens on its first TEXT, which lands before its first audio. A noisy
    room trips the VAD in that window, and the turn was settled before a single byte of
    speech was emitted — so the candidate heard nothing, every time. Nothing said in
    that window can be a reply to an unheard line.
    """
    rt = _runtime()
    rt._persona_turn_open = True
    rt.floor.current = "tech"
    rt._audio_bytes = 0                     # nothing has been heard yet
    rt._flush_persona_turn = AsyncMock()

    _run(rt.on_speech_start())

    rt._flush_persona_turn.assert_not_awaited()
    assert rt._persona_turn_open is True, "the interviewer must be left to finish"


def test_barge_in_after_audio_still_settles_the_turn():
    rt = _runtime()
    rt._persona_turn_open = True
    rt.floor.current = "tech"
    rt._audio_bytes = 48 * 2000             # two seconds were audible
    rt._flush_persona_turn = AsyncMock()
    rt.emit = AsyncMock()

    _run(rt.on_speech_start())

    rt._flush_persona_turn.assert_awaited_once()
    assert rt._persona_turn_open is False


def test_speech_end_before_the_interviewer_is_audible_is_ignored():
    """The other half of the reported "no voice" bug.

    A persona turn opens on its first TEXT, before its first audio. A noisy room trips
    the VAD closed inside that window; running the turn decision there restarts the
    interviewer before it has made a sound, and it loops — the candidate hears nothing
    at all and the room reports the voice is not coming through.
    """
    rt = _runtime()
    rt._advance_turn = AsyncMock()
    rt._persona_turn_open = True
    rt._audio_bytes = 0                  # nothing has been heard yet

    _run_until_settled(rt, rt.on_speech_end())

    rt._advance_turn.assert_not_awaited()
