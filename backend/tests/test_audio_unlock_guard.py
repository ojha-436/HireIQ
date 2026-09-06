"""Static guard for the silent-AI-voice bug: a candidate reported hearing no
interviewer at all, with real Gemini + real Agora credentials confirmed working and
Gemini's audio confirmed unconditional (see AGORA_GEMINI_LATENCY_AUDIT.md).

Root cause: browsers only let AudioContext.resume() actually take effect within (or
shortly after) a genuine user gesture. By the time room.js calls it, the click on
"start the interview" has already gone through a consent POST, a ~1.3 MB Agora script
load, and a dynamic import -- real seconds on a slow connection. If the browser
decided that gap was too long, resume() resolves without error but the context stays
suspended, and every persona's audio plays into a context that never produces sound --
total silence, with nothing in the UI to explain why.

`start()` must check `ctx.state` after resuming (not just assume success) and, if
still suspended, both warn the candidate and retry on their next interaction rather
than requiring a page reload.

See test_responsive_guards.py for the same static-guard pattern applied elsewhere in
this repo -- there is no browser-based test harness for AudioContext state in this
project.
"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
ROOM_JS = (ROOT / "frontend" / "js" / "interview" / "room.js").read_text()


def test_start_checks_whether_the_audio_context_is_still_suspended():
    assert "bot.ctx.state === 'suspended'" in ROOM_JS, (
        "resume() can resolve without error while the context stays suspended -- "
        "start() must check ctx.state explicitly rather than assume resume() worked"
    )


def test_a_suspended_context_warns_the_candidate():
    assert "_armAudioUnlock" in ROOM_JS, (
        "a suspended AudioContext must be surfaced to the candidate, not fail silently "
        "-- total silence with no explanation is indistinguishable from the app being broken"
    )


def test_the_next_interaction_retries_unlocking_audio():
    assert "addEventListener('click', unlock)" in ROOM_JS, (
        "the candidate must be able to recover by clicking/tapping anywhere in the "
        "room, not be stuck requiring a page reload"
    )
