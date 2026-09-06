"""Gemini wiring: the right SDK, the right models, and honest fallbacks.

These exist because the original defect was silent — the deprecated SDK was never
installed, so every text call fell through to a heuristic while looking configured.
"""
from __future__ import annotations

import asyncio
import pathlib

import pytest

from app.config import settings
from app.interview import gemini as GEM


# -------------------------------------------------------------------- the SDK
def test_deprecated_sdk_is_gone():
    """`google.generativeai` is deprecated and was never in requirements.

    Any reintroduction means that call site silently stops using a real model.
    """
    root = pathlib.Path(__file__).resolve().parent.parent / "app"
    offenders = [
        str(f.relative_to(root))
        for f in root.rglob("*.py")
        if "import google.generativeai" in f.read_text()
    ]
    assert offenders == [], f"deprecated SDK still imported in: {offenders}"


def test_current_sdk_is_pinned_recent():
    req = (pathlib.Path(__file__).resolve().parent.parent / "requirements.txt").read_text()
    assert "google-genai>=2.22.0" in req, "google-genai must be a current release"
    assert "google-generativeai" not in req


# ------------------------------------------------------------------ the models
def test_live_model_is_native_audio():
    """THE load-bearing model choice.

    A half-cascade Live model runs STT -> LLM -> TTS internally, reintroducing the loop
    this architecture claims to bypass and adding latency to barge-in. Upgrading to a
    'newer' half-cascade id would silently regress the interruption experience.
    """
    assert "native-audio" in settings.GEMINI_LIVE_MODEL, (
        f"{settings.GEMINI_LIVE_MODEL} is not a native-audio model")
    assert "flash-live" not in settings.GEMINI_LIVE_MODEL, "that id is half-cascade"


def test_text_model_is_not_a_stale_2x_release():
    assert not settings.GEMINI_MODEL.startswith("gemini-2."), (
        f"{settings.GEMINI_MODEL} is a superseded generation")
    assert settings.GEMINI_MODEL == "gemini-3.8-flash"


# ---------------------------------------------------------------- fallbacks
def test_unavailable_without_a_key(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "", raising=False)
    GEM._reset_for_tests()
    assert GEM.available() is False
    assert GEM.generate_json("anything") is None
    assert GEM.generate_text("anything") is None
    GEM._reset_for_tests()


def test_a_failing_call_returns_none_rather_than_raising(monkeypatch):
    """An interview must not die because a scoring call timed out."""
    class Boom:
        class models:
            @staticmethod
            def generate_content(**_):
                raise RuntimeError("upstream 503")

    monkeypatch.setattr(GEM, "_get_client", lambda: Boom())
    assert GEM.generate_json("x") is None
    assert GEM.generate_text("x") is None


def test_non_object_json_is_rejected(monkeypatch):
    class Resp:
        text = '["not", "an", "object"]'

    class Client:
        class models:
            @staticmethod
            def generate_content(**_):
                return Resp()

    monkeypatch.setattr(GEM, "_get_client", lambda: Client())
    assert GEM.generate_json("x") is None, "a JSON array must not pass as a result dict"


def test_analyst_and_moderator_still_work_without_gemini():
    """The deterministic paths must carry PS11 on their own — they are what the
    offline R2 gate exercises."""
    from app.interview.analyst import analyse
    from app.interview.moderator import Moderator

    GEM._reset_for_tests()
    result = analyse(
        answer=("I added a Redis write-through cache and partitioned the Kafka topic by "
                "tenant, cutting p99 from 800ms to 90ms."),
        persona="tech", turn_id="t1", target_skill_id="python",
        claims=[], established={}, required_skill_names=["Python"], recent="")
    assert result["scores"], "the local analyst must still score"

    mod = Moderator(["tech", "product"], required_skill_ids=["python"])
    mod.note_turn("tech")
    directive = mod.decide(result, target_skill_id="python")
    assert directive["rule"], "a rule must always be attributed"


# ---------------------------------------------------------------------------
# Regression: the Live pump must outlive a single turn.
#
# `session.receive()` in google-genai is a PER-TURN generator: it returns as soon as the
# model finishes speaking. `events()` iterated it exactly once, so the pump task that
# forwards audio and transcripts exited after the opening question. Everything after
# that was silence — no candidate input transcript, no further model audio — and the
# room sat on "Listening..." until the socket died of a keepalive timeout.
# ---------------------------------------------------------------------------

class _FakeTurnSession:
    """Mimics the SDK: receive() yields one turn's messages, then returns."""

    def __init__(self, turns):
        self._turns = list(turns)
        self.receive_calls = 0

    async def receive(self):
        self.receive_calls += 1
        if not self._turns:
            return                      # idle: no messages, generator just ends
        for msg in self._turns.pop(0):
            yield msg


class _Msg:
    def __init__(self, *, input_text=None, output_text=None, turn_complete=False):
        self.server_content = self
        self.input_transcription = type("T", (), {"text": input_text})() if input_text else None
        self.output_transcription = type("T", (), {"text": output_text})() if output_text else None
        self.model_turn = None
        self.interrupted = False
        self.turn_complete = turn_complete


@pytest.mark.asyncio
async def test_events_survives_more_than_one_turn():
    """Two model turns with a candidate answer between them must all be delivered."""
    from app.interview.live_client import _GeminiLiveConnection

    sess = _FakeTurnSession([
        [_Msg(output_text="Opening question?"), _Msg(turn_complete=True)],
        [_Msg(input_text="my answer"), _Msg(output_text="Follow-up?"), _Msg(turn_complete=True)],
    ])
    conn = _GeminiLiveConnection(ctx_manager=None, session=sess, types_mod=None)

    seen = []
    async def drain():
        async for ev in conn.events():
            seen.append((ev["type"], ev.get("text")))
            # Stop once the SECOND turn has completed; a single-turn pump never gets here.
            if len([t for t, _ in seen if t == "turn_complete"]) == 2:
                return

    await asyncio.wait_for(drain(), timeout=5)

    assert ("output_transcript", "Opening question?") in seen
    assert ("input_transcript", "my answer") in seen, (
        "the candidate's words never arrived: events() stopped after the first turn"
    )
    assert ("output_transcript", "Follow-up?") in seen
    assert sess.receive_calls >= 2, "receive() must be re-entered for each turn"


@pytest.mark.asyncio
async def test_events_stops_when_the_connection_closes():
    """The re-entry loop must not spin forever once the connection is closed."""
    from app.interview.live_client import _GeminiLiveConnection

    sess = _FakeTurnSession([])          # always idle
    conn = _GeminiLiveConnection(ctx_manager=None, session=sess, types_mod=None)
    conn._closed = True

    seen = [ev async for ev in conn.events()]
    assert seen == []
