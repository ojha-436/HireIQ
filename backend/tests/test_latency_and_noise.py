"""Two more fixes from the same incident as test_vad_turn_guard.py:

1. Noise tokens Gemini's own input-audio transcription can emit ("<noise>") must
   never reach the candidate-facing transcript, the analyst, or the report as if the
   candidate had said them.
2. A persona's Gemini Live connection is a real handshake with real latency.
   Previously it only ever opened lazily, synchronously, in the middle of a handoff
   — the "pre-warm the next persona" design was documented but never implemented.
   `_Floor.warm()` opens a connection ahead of time without granting it the floor,
   and `start()` now calls it for the rest of the panel in the background.

Both are backend-observable without a live Gemini key: warming exercises the same
`LocalVoiceProvider` offline fallback the rest of the suite already runs against, and
the noise filter is pure string processing.
"""
from __future__ import annotations

import asyncio

from unittest.mock import AsyncMock

import pytest

from app.db import Base, engine
from app.interview import live_client as LC
from app.interview import session as RT


@pytest.fixture(scope="module", autouse=True)
def _schema():
    """`_grant_floor`'s real prompt-building path queries the question bank table —
    only the tests that exercise that path (not the mocked-out ones) need it, but
    creating it for the whole module is simplest and matches every other test file."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


def _run(coro):
    return asyncio.run(coro)


async def _noop_pump(persona_key, conn):  # noqa: ARG001 — matches the pump signature
    return None


def _runtime() -> RT.InterviewRuntime:
    grounding = {"job_block": "", "candidate_block": "", "claims": [], "required_skill_ids": []}
    return RT.InterviewRuntime(
        session_id="test-session", user_id="1", panel=["tech", "product"], preset="panel",
        grounding=grounding, emit=AsyncMock(), emit_audio=AsyncMock(),
    )


# =========================================================================== noise filter
def test_clean_candidate_speech_strips_bracketed_and_angle_noise_tokens():
    cases = [
        ("Hello Hi . <noise> <noise>", "Hello Hi."),
        ("I built a [noise] pipeline", "I built a pipeline"),
        ("<SILENCE> okay go ahead", "okay go ahead"),
        ("Ready. <Background Noise> Let's continue.", "Ready. Let's continue."),
        ("No noise tokens here at all.", "No noise tokens here at all."),
        ("", ""),
    ]
    for raw, expected in cases:
        assert RT.clean_candidate_speech(raw) == expected


def test_clean_candidate_speech_discards_mostly_non_latin_transcriptions():
    """Reported live: a candidate speaking English got transcribed as a stray
    Malayalam character, and later as a run of Arabic-script text. Input
    transcription is pinned to English (language_codes=["en-US"], live_client.py) —
    a hint the model usually follows, but not a hard constraint against audio too
    degraded (an unstable connection, packet loss) to transcribe at all. When the
    signal is bad enough, a multilingual ASR model can emit confident-looking text in
    an entirely different script instead of a placeholder <noise> token. That is not
    a real answer in another language; it must be discarded the same way a noise
    token is, not scored as the candidate's response."""
    reported = "صunu صند صون صunu صون صاحب سند صغیر صنف اور صنف صاحب صبح شام"
    assert RT.clean_candidate_speech(reported) == ""

    # A handful of foreign/accented letters in an otherwise English answer (a name, a
    # loanword) must NOT trip this — only a transcription that is mostly a different
    # script is the failure mode being guarded against.
    assert RT.clean_candidate_speech(
        "My manager was Renée, and we shipped a Kubernetes migration together."
    ) != ""


def test_flush_candidate_turn_strips_noise_before_it_is_saved_or_quoted():
    rt = _runtime()
    rt._save_turn = AsyncMock(return_value="turn-1")

    rt._cand_buf = ["Hello Hi", " . ", "<noise>", " <noise>"]
    _run(rt._flush_candidate_turn())

    saved_speaker, saved_text = rt._save_turn.call_args.args
    assert saved_speaker == "candidate"
    assert "<noise>" not in saved_text
    assert saved_text == "Hello Hi."
    # Held for must_reference quoting on the NEXT persona turn — must be clean too,
    # or a quoted-back noise token would leak into a generated question.
    assert rt.last_candidate["text"] == "Hello Hi."


def test_flush_candidate_turn_drops_a_turn_that_is_noise_only():
    """Room noise with no real speech must not become an empty/junk candidate turn."""
    rt = _runtime()
    rt._save_turn = AsyncMock(return_value="turn-1")

    rt._cand_buf = ["<noise>", " <noise> "]
    _run(rt._flush_candidate_turn())

    rt._save_turn.assert_not_called()


# =========================================================================== pre-warming
def test_warm_opens_a_connection_without_taking_the_floor():
    floor = RT._Floor(LC.get_provider())
    _run(floor.warm("product", _noop_pump))

    assert "product" in floor._conns
    assert floor.current is None   # warming a persona must never grant them the floor
    _run(floor.close_all())


def test_acquire_after_warm_reuses_the_warmed_connection_instead_of_reconnecting():
    floor = RT._Floor(LC.get_provider())
    calls: list[str] = []
    real_connect = floor._provider.connect

    async def counting_connect(persona_key):
        calls.append(persona_key)
        return await real_connect(persona_key)

    floor._provider.connect = counting_connect

    _run(floor.warm("product", _noop_pump))
    _run(floor.acquire("product", _noop_pump))

    assert calls == ["product"]          # connect() happened exactly once, at warm time
    assert floor.current == "product"
    _run(floor.close_all())


def test_concurrent_warm_and_acquire_for_the_same_persona_do_not_double_connect():
    """The exact race a background warm() introduces: the moderator grants the floor
    to a persona whose warm-up is still in flight. Both paths must land on the SAME
    connection, not each open their own."""
    floor = RT._Floor(LC.get_provider())
    calls: list[str] = []
    real_connect = floor._provider.connect

    async def slow_connect(persona_key):
        calls.append(persona_key)
        await asyncio.sleep(0.02)
        return await real_connect(persona_key)

    floor._provider.connect = slow_connect

    async def race():
        await asyncio.gather(
            floor.warm("product", _noop_pump),
            floor.acquire("product", _noop_pump),
        )

    _run(race())
    assert calls == ["product"]
    assert floor.current == "product"
    _run(floor.close_all())


def test_start_warms_the_rest_of_the_panel_in_the_background():
    rt = _runtime()
    # Isolate the warming behaviour from the rest of start()'s side effects (DB
    # writes, recording, ConvoAI boot) — those are exercised by the full WS tests
    # elsewhere; this test is only about the pre-warm wiring.
    rt._mark_live = lambda db: None
    rt._watchdog = AsyncMock()
    rt._start_recording = AsyncMock()
    rt._convoai_boot = AsyncMock()

    async def go():
        await rt.start()
        await asyncio.sleep(0.05)   # let the background warm task actually run

    _run(go())

    assert rt.floor.current == "tech"        # the first turn's floor grant, unaffected
    assert "product" in rt.floor._conns      # warmed in the background
    _run(rt.floor.close_all())
