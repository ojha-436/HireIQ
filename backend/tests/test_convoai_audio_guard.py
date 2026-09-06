"""Gemini's own native-audio output reaches the browser unconditionally, even when
Agora ConvoAI is also active and speaking the same line into the Agora channel.

This was NOT always true: a earlier pass suppressed Gemini's WebSocket audio whenever
`_convoai` was set, on the theory that the candidate would hear the ConvoAI agent's
voice over its own Agora-published track instead — avoiding two TTS vendors narrating
the same line a beat apart. The first field test against real Gemini + real Agora
credentials showed the opposite: the candidate heard NOTHING. The backend has no
signal for whether the browser's Agora join/subscribe actually succeeded (real
network/firewall/WebRTC conditions were never exercised before that test), so
suppressing the one PROVEN, always-working audio path on the assumption that an
unverified one had replaced it was the wrong trade — a silent interview is worse than
one with an occasional doubled voice. Gemini's audio is unconditional again; see
session.py `_pump`'s EV_AUDIO branch.
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


def test_gemini_audio_also_reaches_the_browser_when_convoai_is_active():
    """Regression: this used to assert the opposite (suppressed). It was reverted
    because a real candidate on the ConvoAI path heard nothing at all -- see the
    module docstring."""
    rt = _runtime()
    rt.floor.current = "tech"
    rt._convoai = True
    rt._open_persona_turn = AsyncMock()

    _run(rt._pump("tech", _OneShotAudioConnection()))

    rt.emit_audio.assert_awaited_once_with(b"\x01\x02")


def test_turn_bookkeeping_runs_regardless_of_convoai_state():
    """_open_persona_turn (which flushes the candidate's prior turn and marks the
    floor as genuinely speaking) is not a function of which voice the candidate
    hears, and must never be skipped."""
    rt = _runtime()
    rt.floor.current = "tech"
    rt._convoai = True
    rt._open_persona_turn = AsyncMock()

    _run(rt._pump("tech", _OneShotAudioConnection()))

    rt._open_persona_turn.assert_awaited_once()
