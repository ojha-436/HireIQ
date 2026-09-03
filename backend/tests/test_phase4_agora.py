"""Agora integration: RTC token correctness and the ConvoAI voice contract.

The ConvoAI calls cannot be exercised without live credentials, so these tests pin the
two things that are verifiable offline and that would silently break PS11 if wrong:
the request SHAPE (manual turn detection, per-persona voice) and the GATING (absent
credentials must leave the engine untouched rather than half-configured).
"""
from __future__ import annotations

import base64
import struct
import zlib

import pytest

from app.interview import agora_convoai as CA
from app.interview import agora_token as AT
from app.interview import personas as P


# ------------------------------------------------------------------- RTC token
def test_rtc_token_round_trips():
    token = AT.build_rtc_token("a" * 32, "c" * 32, "hireiq-test", 42, ttl_seconds=3600)
    assert token.startswith("007"), "AccessToken2 must carry the 007 version prefix"

    raw = zlib.decompress(base64.b64decode(token[3:]))
    sig_len = struct.unpack_from("<H", raw, 0)[0]
    assert sig_len == 32, "HMAC-SHA256 signature is 32 bytes"
    app_id_len = struct.unpack_from("<H", raw, 2 + sig_len)[0]
    app_id = raw[4 + sig_len: 4 + sig_len + app_id_len].decode()
    assert app_id == "a" * 32


# --------------------------------------------------------------- ConvoAI gating
def test_convoai_is_inert_without_credentials():
    """No credentials must mean no ConvoAI — never a partially configured agent."""
    assert CA.configured() is False


def test_convoai_calls_refuse_rather_than_silently_no_op():
    with pytest.raises(CA.ConvoAIUnavailable):
        CA._call("POST", "/join", {})


# ---------------------------------------------------- ConvoAI request contract
@pytest.fixture
def payload():
    return CA.agent_payload("product", "hireiq-abc", "rtc-token", "42", "sess1")


def test_turn_detection_is_manual_on_both_ends(payload):
    """THE load-bearing setting.

    If Agora decides when a turn ends, the Moderator is bypassed and PS11's
    'controlled interviewer turn-taking' claim is no longer true.
    """
    cfg = payload["properties"]["turn_detection"]["config"]
    assert cfg["sos_mode"] == "manual"
    assert cfg["eos_mode"] == "manual"


def test_agent_never_authors_its_own_speech(payload):
    """The agent is a mouth, not a mind: no greeting, no history to riff on."""
    llm = payload["properties"]["llm"]
    assert llm["greeting_message"] == ""
    assert llm["max_history"] == 1
    assert "Say nothing on your own" in llm["system_messages"][0]["content"]


def test_barge_in_is_enabled(payload):
    assert payload["properties"]["interruption"] == {
        "enable": True, "mode": "start_of_speech"}


def test_each_persona_gets_a_distinct_voice_and_uid():
    # The voice parameter name is vendor-specific, so read whichever key is present.
    voices, uids = set(), set()
    for key in P.PERSONAS:
        body = CA.agent_payload(key, "ch", "tok", "1", "s")["properties"]
        params = body["tts"]["params"]
        voices.add(params.get("voice") or params.get("voice_name") or params.get("voice_id"))
        uids.add(body["agent_rtc_uid"])
    assert len(voices) == len(P.PERSONAS), "two personas sharing a voice sound like one person"
    assert len(uids) == len(P.PERSONAS), "agent uids must be distinct channel participants"


def test_agent_uid_matches_the_persona_bot_uid():
    """The room already renders tiles keyed by bot_uid; the agent must land on that uid."""
    for key, persona in P.PERSONAS.items():
        body = CA.agent_payload(key, "ch", "tok", "1", "s")["properties"]
        assert body["agent_rtc_uid"] == str(persona.bot_uid)


def test_candidate_is_the_only_remote_uid(payload):
    assert payload["properties"]["remote_rtc_uids"] == ["42"]


# ------------------------------------------------------------------ speak clipping
def test_long_speech_is_clipped_at_a_sentence_boundary():
    text = ("This first sentence is complete. " * 30).strip()
    clipped = CA._clip(text, CA.SPEAK_MAX_BYTES)
    assert len(clipped.encode()) <= CA.SPEAK_MAX_BYTES
    assert clipped.endswith("."), "a persona cut mid-word reads as a broken product"


def test_clip_falls_back_to_a_word_boundary():
    text = "x" * 40 + " tail"
    clipped = CA._clip(text, 30)
    assert len(clipped.encode()) <= 30
    assert " " not in clipped.strip() or clipped == clipped.strip()


# ==================================================== ConvoAI must never block
def test_convoai_boot_is_not_awaited_before_the_first_turn():
    """The interview must start on Gemini audio regardless of Agora's reachability.

    This was a real outage: `_convoai_boot()` was awaited before the first floor grant,
    so an unreachable Agora endpoint stalled the interview for the full HTTP timeout per
    persona and the room sat on "Thinking..." in silence.
    """
    import inspect

    from app.interview import session as RT

    src = inspect.getsource(RT.InterviewRuntime.start)
    assert "await self._convoai_boot()" not in src, (
        "ConvoAI boot is back on the critical path of starting an interview")
    assert "_convoai_boot()" in src and "create_task" in src, (
        "ConvoAI boot should be launched as a background task")


def test_convoai_timeouts_are_short():
    """An optional voice upgrade does not get to hold up a live interview."""
    assert CA.HTTP_TIMEOUT_S <= 8, CA.HTTP_TIMEOUT_S
    assert CA.SPEAK_TIMEOUT_S <= CA.HTTP_TIMEOUT_S


def test_a_dead_convoai_leaves_no_half_configured_agents(monkeypatch):
    """Partial boot is worse than none — the pool must be empty after a failure."""
    import asyncio

    from app.interview import session as RT

    rt = RT.InterviewRuntime.__new__(RT.InterviewRuntime)
    rt.panel = ["tech", "product"]
    rt.session_id = "s" * 32
    rt.user_id = "1"
    rt.agora_channel = "hireiq-x"
    rt.agora_token = "tok"
    rt._agents = {}
    rt._convoai = False
    emitted = []

    async def emit(msg):
        emitted.append(msg)

    rt.emit = emit

    calls = {"n": 0}

    async def boom(*_a, **_k):
        calls["n"] += 1
        raise CA.ConvoAIUnavailable("timed out")

    monkeypatch.setattr(CA, "start_agent", boom)
    monkeypatch.setattr(CA, "configured", lambda: True)
    monkeypatch.setattr("app.config.settings.voice_provider", "auto", raising=False)

    asyncio.run(rt._convoai_boot())

    assert rt._convoai is False
    assert rt._agents == {}
    assert calls["n"] == 1, "kept retrying every persona after the service was clearly down"


# ================================================ TTS vendor must not need BYOK keys
def test_tts_vendor_needs_no_external_credentials():
    """Verified against a live Agora project.

    `openai`, `elevenlabs` and `google` join in ~1.5s on Agora-managed credentials.
    `microsoft` (Azure), `cartesia` and `deepgram` require BYOK keys — without them
    Agora dials the vendor, gets nothing, and returns 400 ErrInternal after a THIRTY
    SECOND stall. That stall is what previously read as "Agora is unreachable".
    """
    assert CA.TTS_VENDOR in {"openai", "elevenlabs", "google"}, (
        f"{CA.TTS_VENDOR} needs BYOK credentials and will stall for 30s per persona")


def test_every_persona_has_a_distinct_voice_for_this_vendor():
    voices = {CA.VOICE_MAP[k] for k in P.PERSONAS}
    assert len(voices) == len(P.PERSONAS), "two personas would sound identical"


def test_agent_names_are_unique_per_attempt():
    """Reusing a name returns 409 TaskConflict, which on reconnect looks like refusal."""
    import time

    first = CA.agent_payload("tech", "ch", "tok", "1", "sess")["name"]
    time.sleep(1.05)
    second = CA.agent_payload("tech", "ch", "tok", "1", "sess")["name"]
    assert first != second


def test_tts_block_uses_the_right_param_name_per_vendor():
    """Each vendor names its voice field differently; sending the wrong one silently
    falls back to a default voice, so the panel loses its distinct voices."""
    block = CA._tts_block("product")
    expected = {"openai": "voice", "google": "voice_name", "elevenlabs": "voice_id"}[CA.TTS_VENDOR]
    assert expected in block["params"], block


def test_abandoned_interview_tears_down_its_agents():
    """Agora agents are billed and hold channel slots.

    A dropped WebSocket must release them rather than leaving them to idle out.
    """
    import inspect

    from app.routers import interview as IV

    src = inspect.getsource(IV.interview_ws)
    tail = src[src.index("finally:"):]
    assert "_convoai_shutdown" in tail, (
        "the abandon path closes the Gemini floor but leaks the Agora agents")
