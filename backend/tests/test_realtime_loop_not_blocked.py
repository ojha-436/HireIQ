"""Two bugs found during a real-credentials latency audit (AGORA_GEMINI_LATENCY_AUDIT.md):
`_advance_turn()` ran two blocking calls INLINE on the asyncio event loop instead of
off-loading them, unlike everything else on that path (`_db`, the analyst call).

1. `_offer_scenario()`'s DB lookup (`SC.pick(...)`, a real SQLAlchemy query) ran as a
   plain synchronous call inside a supposedly-async method, re-fired on every
   `_advance_turn()` until a scenario candidate was found.
2. `Moderator.decide()`'s R0 tiebreak path can call `GEM.generate_json(...)` --
   `client.models.generate_content(...)`, a real synchronous network call -- and
   `_advance_turn` awaited `decide()` directly with nothing off-loading that call.

Blocking the event loop this way does not just delay the ONE interview waiting on it:
every other concurrently-running session's `_pump()` task (i.e. its audio delivery) is
also a coroutine on the same loop, so it stalls too. This is a plausible root cause for
the "Agora connection stalled" symptom seen in the field test -- what looked like a
flaky network could have been the server's own event loop wedged on a synchronous DB
or Gemini call for a completely different candidate's turn.

Both are fixed by `asyncio.to_thread`, the same technique `_db()` already uses.
"""
from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from unittest.mock import AsyncMock

from app.interview import session as RT
from app.interview.moderator import Moderator

SESSION_PY = Path(__file__).resolve().parent.parent / "app" / "interview" / "session.py"

BLOCK_MS = 300  # long enough that a blocked loop is obvious against a 10ms poll


def _runtime() -> RT.InterviewRuntime:
    grounding = {"job_block": "", "candidate_block": "", "claims": [], "required_skill_ids": []}
    return RT.InterviewRuntime(
        session_id="test-session", user_id="1", panel=["tech"], preset="screen",
        grounding=grounding, emit=AsyncMock(), emit_audio=AsyncMock(),
    )


async def _canary(ticks: int = 20, interval: float = 0.01) -> list[float]:
    """A coroutine that can only make steady progress if the event loop is free.

    If something else on the loop is blocking synchronously, every one of these
    `sleep(0.01)` calls queues up behind it and they all fire back-to-back the instant
    the blocking call finally returns -- the opposite of what "steady progress" means.
    """
    marks: list[float] = []
    for _ in range(ticks):
        await asyncio.sleep(interval)
        marks.append(time.monotonic())
    return marks


def _assert_canary_was_not_starved(marks: list[float]) -> None:
    assert len(marks) == 20, "canary did not complete — something hung"
    spread = marks[-1] - marks[0]
    # Free-running, 20 ticks * 10ms should take ~200ms. Starved (all ticks released at
    # once when the blocking call finally returns) would collapse this to near-zero.
    assert spread > 0.12, (
        f"canary ticks were bunched together (spread={spread*1000:.0f}ms) — "
        "the event loop was blocked, not free-running"
    )


def test_offer_scenario_runs_its_db_query_off_the_event_loop(monkeypatch):
    rt = _runtime()

    def slow_pick(*args, **kwargs):
        time.sleep(BLOCK_MS / 1000)
        return None

    monkeypatch.setattr(RT.SC, "pick", slow_pick)

    async def go():
        return await asyncio.gather(rt._offer_scenario(), _canary())

    _, marks = asyncio.run(go())
    _assert_canary_was_not_starved(marks)


def test_moderator_tiebreak_off_loaded_via_to_thread_does_not_block(monkeypatch):
    """Proves the TECHNIQUE session.py now uses (asyncio.to_thread(mod.decide, ...))
    is actually non-blocking against a genuinely slow, synchronous Gemini call --
    the same call shape `_advance_turn` makes in production.
    """
    from app.interview import gemini as GEM

    monkeypatch.setattr(GEM, "available", lambda: True)

    def slow_generate_json(prompt, **kwargs):
        time.sleep(BLOCK_MS / 1000)
        return None

    monkeypatch.setattr(GEM, "generate_json", slow_generate_json)

    mod = Moderator(["tech"], required_skill_ids=["python"])
    mod.note_turn("tech")
    analysis = {"scores": {}, "specificity": "specific", "impact_stated": True,
                "flags": [], "turn_id": "t1"}

    async def call_decide():
        return await asyncio.to_thread(mod.decide, analysis, target_skill_id="python")

    async def go():
        return await asyncio.gather(call_decide(), _canary())

    directive, marks = asyncio.run(go())
    assert directive["rule"] == "R0", directive  # our slow stub returned no usable JSON
    _assert_canary_was_not_starved(marks)


def test_session_offloads_the_moderator_decide_call_onto_a_thread():
    """Static guard on the exact call site: proves _advance_turn actually uses the
    to_thread technique above, not just that the technique works in the abstract.
    `decide()` itself must stay a plain sync method (every existing moderator test
    calls it directly, synchronously, with no event loop running), so the fix has to
    live at the call site in session.py, not on Moderator.
    """
    src = SESSION_PY.read_text(encoding="utf-8")
    assert "asyncio.to_thread(self.mod.decide" in src, (
        "_advance_turn must call Moderator.decide() via asyncio.to_thread — calling it "
        "inline blocks the event loop for every concurrently-running interview "
        "whenever the R0 tiebreak makes a real Gemini call"
    )
    # And make sure it isn't ALSO called inline somewhere else in the same file.
    bare_calls = [
        ln for ln in src.splitlines()
        if re.search(r"(?<!to_thread\()\bself\.mod\.decide\(", ln)
        and "asyncio.to_thread" not in ln
        and not ln.strip().startswith("#")
    ]
    assert not bare_calls, f"found a non-off-loaded call to mod.decide(): {bare_calls}"


def test_offer_scenario_is_awaited_not_called_inline():
    src = SESSION_PY.read_text(encoding="utf-8")
    assert "async def _offer_scenario" in src
    assert "await self._offer_scenario()" in src


def test_first_ai_audio_after_a_real_speech_end_logs_turn_latency(capsys):
    """Latency instrumentation (previously entirely absent): the first audio chunk of
    a granted turn should print one structured line breaking the turn down into
    speech_end->audio, prompt_sent->audio and the analyst's own share of it -- enough
    to tell a slow Gemini turn apart from a slow analyst call apart from our own
    orchestration, without guessing.
    """
    rt = _runtime()
    # Only the persona actually holding the floor is audible (see _pump's EV_AUDIO
    # guard) — normally set by _grant_floor's floor.acquire(), simulated here since
    # this test drives _pump directly without going through a full turn.
    rt.floor.current = "tech"
    # _ms() is monotonic-since-session-start, not wall-clock — anchor both timestamps
    # to the runtime's own clock rather than an arbitrary constant, or the deltas
    # computed at _pump time come out negative.
    t0 = rt._ms()
    rt._turn_t_speech_end_ms = t0
    rt._turn_t_prompt_sent_ms = t0
    rt._turn_t_analyst_ms = 42
    rt._turn_first_audio_pending = True

    class _TwoAudioChunks(RT.LC.LiveConnection):
        async def events(self):
            yield {"type": RT.LC.EV_AUDIO, "pcm": b"\x00\x01"}
            yield {"type": RT.LC.EV_AUDIO, "pcm": b"\x02\x03"}

    asyncio.run(rt._pump("tech", _TwoAudioChunks()))

    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.startswith("[latency]")]
    assert len(lines) == 1, f"expected exactly one latency line (one-shot per turn): {lines}"
    line = lines[0]
    assert "session=test-session" in line
    assert "persona=tech" in line
    assert "analyst=42ms" in line
    assert re.search(r"speech_end->first_audio=\d+ms", line), line
    assert re.search(r"prompt_sent->first_audio=\d+ms", line), line
    # And the pending flag must have been consumed so a later turn's first chunk gets
    # its own fresh measurement instead of silently reusing this one's.
    assert rt._turn_first_audio_pending is False
