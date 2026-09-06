"""When Agora ConvoAI is active, its per-persona agent is already speaking the turn's
text into the Agora channel as a real participant (session.py's _convoai_speak, wired
through _flush_persona_turn). Gemini's own native-audio output for that same turn used
to be forwarded to the browser over the WebSocket regardless, so a candidate on the
ConvoAI path would hear the line spoken twice, a beat apart, by two different TTS
vendors. _pump()'s EV_AUDIO branch now suppresses the WebSocket emission while
`_convoai` is set — turn bookkeeping (_open_persona_turn) still runs every time, only
the redundant audio bytes are held back.
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Dict
from unittest.mock import AsyncMock

from app.interview import live_client as LC
from app.interview import session as RT


class _OneShotAudioConnection(LC.LiveConnection):
    """Yields exactly one EV_AUDIO event, then the stream ends."""

    async def events(self) -> AsyncIterator[Dict[str, Any]]:
        yield {"type": LC.EV_AUDIO, "pcm": b"\x01\x02"}


def _runtime() -> RT.InterviewRuntime:
    grounding = {"job_block": "", "candidate_block": "", "claims": [], "required_skill_ids": []}
    return RT.InterviewRuntime(
        session_id="test-session", user_id="1", panel=["tech"], preset="screen",
        grounding=grounding, emit=AsyncMock(), emit_audio=AsyncMock(),
    )


def _run(coro):
    return asyncio.run(coro)


def test_gemini_audio_reaches_the_browser_when_convoai_is_not_active():
    rt = _runtime()
    rt.floor.current = "tech"
    rt._open_persona_turn = AsyncMock()

    _run(rt._pump("tech", _OneShotAudioConnection()))

    rt.emit_audio.assert_awaited_once_with(b"\x01\x02")


def test_gemini_audio_is_suppressed_when_convoai_is_speaking_the_same_line():
    rt = _runtime()
    rt.floor.current = "tech"
    rt._convoai = True
    rt._open_persona_turn = AsyncMock()

    _run(rt._pump("tech", _OneShotAudioConnection()))

    rt.emit_audio.assert_not_awaited()


def test_turn_bookkeeping_still_runs_even_when_audio_is_suppressed():
    """Only the WebSocket bytes are held back — _open_persona_turn (which flushes the
    candidate's prior turn and marks the floor as genuinely speaking) is not a function
    of which voice the candidate hears, and must not be skipped."""
    rt = _runtime()
    rt.floor.current = "tech"
    rt._convoai = True
    rt._open_persona_turn = AsyncMock()

    _run(rt._pump("tech", _OneShotAudioConnection()))

    rt._open_persona_turn.assert_awaited_once()
