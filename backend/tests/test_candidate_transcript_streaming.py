"""Reported bug: a candidate's live transcript showed only the last word of what they
said, even though the full answer was clearly heard and scored correctly (the
interviewer's next question referenced details from the whole answer).

Root cause: `_pump()`'s EV_INPUT_TRANSCRIPT branch emitted only `ev["text"]` -- the
single incremental fragment Gemini just streamed -- instead of the accumulated buffer,
unlike the EV_OUTPUT_TRANSCRIPT branch just below it, which already joins
`self._persona_buf[...]` correctly. The frontend replaces a row's displayed text on
every non-final update (it has no way to know a fragment is partial), so each new
word-or-two fragment overwrote the last, and whatever fragment happened to arrive
right before the turn settled is the only thing a candidate ever saw. Scoring was
unaffected the whole time -- it already read the joined `_cand_buf` at flush time via
`_flush_candidate_turn` -- only the live display was wrong.
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Dict
from unittest.mock import AsyncMock

from app.interview import live_client as LC
from app.interview import session as RT


class _StreamedInputConnection(LC.LiveConnection):
    """Yields several EV_INPUT_TRANSCRIPT fragments, as Gemini streams them."""

    async def events(self) -> AsyncIterator[Dict[str, Any]]:
        for word in ("I ", "am ", "a ", "backend ", "engineer."):
            yield {"type": LC.EV_INPUT_TRANSCRIPT, "text": word}


def _runtime() -> RT.InterviewRuntime:
    grounding = {"job_block": "", "candidate_block": "", "claims": [], "required_skill_ids": []}
    return RT.InterviewRuntime(
        session_id="test-session", user_id="1", panel=["tech"], preset="screen",
        grounding=grounding, emit=AsyncMock(), emit_audio=AsyncMock(),
    )


def _run(coro):
    return asyncio.run(coro)


def test_each_streamed_update_carries_the_full_text_so_far():
    rt = _runtime()

    _run(rt._pump("tech", _StreamedInputConnection()))

    texts = [
        call.args[0]["text"]
        for call in rt.emit.await_args_list
        if call.args[0].get("speaker") == "candidate"
    ]
    # Trailing whitespace is stripped by clean_candidate_speech (see below) -- a
    # cosmetic side effect of reusing the same cleaner the final flush already used,
    # invisible in the rendered UI either way.
    assert texts == [
        "I",
        "I am",
        "I am a",
        "I am a backend",
        "I am a backend engineer.",
    ], texts


def test_the_final_emitted_fragment_is_not_just_the_last_word():
    rt = _runtime()

    _run(rt._pump("tech", _StreamedInputConnection()))

    last_candidate_text = [
        call.args[0]["text"]
        for call in rt.emit.await_args_list
        if call.args[0].get("speaker") == "candidate"
    ][-1]
    assert last_candidate_text == "I am a backend engineer."
    assert last_candidate_text != "engineer."


class _GarbledFragmentConnection(LC.LiveConnection):
    """A single ASR fragment that is pure non-Latin-script noise.

    Reported live: a real-mic field test over a degraded connection showed a bare
    "うん" as its own transcript entry, from a candidate speaking English the whole
    time. clean_candidate_speech() already discarded this at the FINAL flush (so
    scoring was never affected) -- but the live per-fragment display emitted the raw,
    uncleaned ev["text"] straight from Gemini, so the candidate still saw it flash by.
    """

    async def events(self) -> AsyncIterator[Dict[str, Any]]:
        yield {"type": LC.EV_INPUT_TRANSCRIPT, "text": "うん"}


def test_a_standalone_non_latin_fragment_does_not_reach_the_live_transcript():
    rt = _runtime()

    _run(rt._pump("tech", _GarbledFragmentConnection()))

    texts = [
        call.args[0]["text"]
        for call in rt.emit.await_args_list
        if call.args[0].get("speaker") == "candidate"
    ]
    assert texts == [""], texts
