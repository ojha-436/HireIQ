"""Static guard for the Agora media-path fix (AGORA_GEMINI_LATENCY_AUDIT.md).

Before this fix, `room.js` joined the Agora channel and then never published or
subscribed to anything on it — the candidate's mic, camera and the AI's voice all
travelled over a plain WebSocket instead, and a dead `this.bot.attachAgora?.(...)` call
(attachAgora was never defined on BotAudio) was the only trace of the intended design.
These guards catch a regression back to that state cheaply, in the suite — see
test_responsive_guards.py for the same static-guard pattern applied to a different bug.
"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
ROOM_JS = (ROOT / "frontend" / "js" / "interview" / "room.js").read_text()
BOT_AUDIO_JS = (ROOT / "frontend" / "js" / "interview" / "bot-audio.js").read_text()


def test_candidate_mic_is_published_to_agora():
    assert "createCustomAudioTrack" in ROOM_JS and "agora.publish(" in ROOM_JS, (
        "the candidate's mic must be published into the Agora channel as a real track, "
        "not just implied by joining it"
    )


def test_room_subscribes_to_remote_agora_participants():
    assert "'user-published'" in ROOM_JS and ".subscribe(" in ROOM_JS, (
        "without a 'user-published' handler that subscribes, nothing published by "
        "another channel participant (e.g. an Agora ConvoAI persona agent) is ever heard"
    )


def test_the_dead_attach_agora_hook_is_gone():
    assert "attachAgora" not in ROOM_JS, (
        "attachAgora was a call to a method BotAudio never defined -- a silent no-op. "
        "The AI's Agora identity now comes from the ConvoAI agents in "
        "agora_convoai.py, not a client-side republish of local playback audio"
    )
    assert "attachAgora(" not in BOT_AUDIO_JS, (
        "BOT_AUDIO_JS may explain the old dead hook in prose (for context), but must "
        "not define the method itself again"
    )


def test_mute_silences_the_published_agora_track_too():
    assert "agoraMicTrack?.setEnabled" in ROOM_JS, (
        "muting must disable the published Agora track as well as the WebSocket path, "
        "or a 'muted' candidate is still audible to any other channel participant"
    )
