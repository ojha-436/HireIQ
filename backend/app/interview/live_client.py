"""Live voice providers for the interview panel (plan-v3.md §5.2).

Follows the `engines/providers.py` contract: a real provider that activates on config,
and a deterministic local provider so the app runs — and the whole audio path stays
testable — with no API key and no network.

Audio contract, fixed on both sides:
    candidate -> model : 16-bit signed PCM, 16 kHz, mono, little-endian
    model -> candidate : 16-bit signed PCM, 24 kHz, mono, little-endian

The rate difference is not a bug; it is what the Live API emits. The browser resamples
on playback (frontend/js/interview/bot-audio.js).
"""
from __future__ import annotations

import asyncio
import math
import struct
from typing import Any, AsyncIterator, Dict, List, Optional

from app.config import settings
from app.interview import personas as P

INPUT_SAMPLE_RATE = 16000
OUTPUT_SAMPLE_RATE = 24000

# Event types yielded by LiveConnection.events()
EV_AUDIO = "audio"                      # {'pcm': bytes}  model speech, 24 kHz
EV_INPUT_TRANSCRIPT = "input_transcript"    # {'text': str}  what the candidate said
EV_OUTPUT_TRANSCRIPT = "output_transcript"  # {'text': str}  what the model said
EV_INTERRUPTED = "interrupted"          # candidate barged in; stop playback now
EV_TURN_COMPLETE = "turn_complete"      # model finished its turn
EV_ERROR = "error"                      # {'detail': str}


class LiveConnection:
    """One persona's live audio channel. Exactly one connection holds the floor."""

    async def send_audio(self, pcm16: bytes) -> None:
        raise NotImplementedError

    async def send_text(self, text: str, end_of_turn: bool = True) -> None:
        """Inject context or a moderator directive as a text turn."""
        raise NotImplementedError

    async def signal_activity_start(self) -> None:
        """Tell the model the candidate has begun speaking (explicit start-of-turn)."""
        return None

    async def signal_activity_end(self) -> None:
        """Tell the model the candidate has finished speaking (explicit end-of-turn)."""
        return None

    def events(self) -> AsyncIterator[Dict[str, Any]]:
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError


class LiveVoiceProvider:
    name = "base"
    available = False

    async def connect(self, persona_key: str, *, session_label: str = "") -> LiveConnection:
        raise NotImplementedError


# --------------------------------------------------------------------------
# Gemini Live
# --------------------------------------------------------------------------

class _GeminiLiveConnection(LiveConnection):
    def __init__(self, ctx_manager, session, types_mod):
        self._ctx = ctx_manager
        self._session = session
        self._types = types_mod
        self._closed = False

    async def send_audio(self, pcm16: bytes) -> None:
        if self._closed:
            return
        t = self._types
        await self._session.send_realtime_input(
            audio=t.Blob(data=pcm16, mime_type="audio/pcm;rate={}".format(INPUT_SAMPLE_RATE))
        )

    async def send_text(self, text: str, end_of_turn: bool = True) -> None:
        if self._closed:
            return
        t = self._types
        await self._session.send_client_content(
            turns=[t.Content(role="user", parts=[t.Part(text=text)])],
            turn_complete=end_of_turn,
        )

    async def signal_activity_start(self) -> None:
        if self._closed:
            return
        try:
            await self._session.send_realtime_input(activity_start=self._types.ActivityStart())
        except (AttributeError, TypeError):
            return

    async def signal_activity_end(self) -> None:
        if self._closed:
            return
        try:
            await self._session.send_realtime_input(activity_end=self._types.ActivityEnd())
        except (AttributeError, TypeError):
            return

    async def events(self) -> AsyncIterator[Dict[str, Any]]:
        """Yield every event for the LIFE of the connection, not just one turn.

        `session.receive()` is per-TURN: the SDK's generator returns as soon as the
        model finishes a turn. Iterating it once meant the pump task exited after the
        opening question, so nothing was ever read from the socket again — no candidate
        input transcript, no further model audio, and the interview sat on "Listening"
        for ever while the socket died of a keepalive timeout. Re-enter it until the
        connection is actually closed.
        """
        try:
            while not self._closed:
                produced = False
                async for msg in self._session.receive():
                    produced = True
                    sc = getattr(msg, "server_content", None)
                    if sc is None:
                        continue
                    it = getattr(sc, "input_transcription", None)
                    if it is not None and getattr(it, "text", None):
                        yield {"type": EV_INPUT_TRANSCRIPT, "text": it.text}
                    ot = getattr(sc, "output_transcription", None)
                    if ot is not None and getattr(ot, "text", None):
                        yield {"type": EV_OUTPUT_TRANSCRIPT, "text": ot.text}
                    mt = getattr(sc, "model_turn", None)
                    if mt is not None:
                        for part in (getattr(mt, "parts", None) or []):
                            blob = getattr(part, "inline_data", None)
                            if blob is not None and getattr(blob, "data", None):
                                yield {"type": EV_AUDIO, "pcm": blob.data}
                            elif getattr(part, "text", None):
                                yield {"type": EV_OUTPUT_TRANSCRIPT, "text": part.text}
                    if getattr(sc, "interrupted", False):
                        yield {"type": EV_INTERRUPTED}
                    if getattr(sc, "turn_complete", False):
                        yield {"type": EV_TURN_COMPLETE}
                if not produced:
                    # receive() returned without a single message: idle socket. Yield to
                    # the loop so re-entering cannot spin at 100% CPU.
                    await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface upstream failures as events
            yield {"type": EV_ERROR, "detail": "{}: {}".format(type(exc).__name__, exc)}

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._ctx.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001 - closing a dead socket must not raise
            pass


class GeminiLiveProvider(LiveVoiceProvider):
    name = "gemini_live"

    def __init__(self) -> None:
        self.available = bool(settings.GEMINI_API_KEY)

    async def connect(self, persona_key: str, *, session_label: str = "") -> LiveConnection:
        from google import genai                     # noqa: PLC0415 - optional dep
        from google.genai import types               # noqa: PLC0415

        persona = P.get(persona_key)
        client = genai.Client(api_key=settings.GEMINI_API_KEY)

        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=types.Content(
                parts=[types.Part(text=P.system_prompt(persona_key))]
            ),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=persona.voice)
                )
            ),
            # Both directions transcribed: input gives us the candidate's answer text for
            # the analyst and the transcript; output gives us the interviewer's words to
            # cite in the report.
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            # NO THINKING. The 12-2025 native-audio model emits its reasoning into the
            # output transcript, and the candidate then hears it: "**Initiating The
            # Interview** I've begun by reviewing the candidate's profile [c1]...".
            # It also leaks internal claim ids and, because the tokens never stop
            # arriving, the turn-settle debounce never fires and no turn is persisted.
            # An interviewer thinks before speaking; it does not narrate the thought.
            thinking_config=types.ThinkingConfig(
                include_thoughts=False,
                thinking_level=types.ThinkingLevel.MINIMAL,
            ),
            # Without this, an audio-only session caps around 15 minutes and a 25-40 min
            # interview dies mid-way (plan-v3.md §4).
            context_window_compression=types.ContextWindowCompressionConfig(
                sliding_window=types.SlidingWindow(),
            ),
            # MANUAL turn control. With automatic activity detection on, the model replies
            # the instant the candidate stops talking — which hands turn-taking back to
            # the model and makes the moderator decorative. PS11 asks for *controlled*
            # turn-taking, so end-of-turn is decided by the browser's VAD
            # (frontend/js/interview/mic-worklet.js), the moderator then picks who answers,
            # and only that persona is prompted to speak.
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(disabled=True),
            ),
        )
        ctx = client.aio.live.connect(model=settings.GEMINI_LIVE_MODEL, config=config)
        session = await ctx.__aenter__()
        return _GeminiLiveConnection(ctx, session, types)


# --------------------------------------------------------------------------
# Local fallback — no key, no network
# --------------------------------------------------------------------------

def _tone(freq_hz: float, ms: int, rate: int = OUTPUT_SAMPLE_RATE, amp: float = 0.16) -> bytes:
    """A short shaped tone. The fallback needs to emit *something* audible so the
    browser audio path (worklet -> WS -> custom Agora track) can be verified without a
    Gemini key, and a per-persona frequency keeps the personas distinguishable."""
    n = int(rate * ms / 1000)
    out = bytearray()
    for i in range(n):
        # Raised-cosine envelope so it does not click at the edges.
        env = 0.5 - 0.5 * math.cos(2 * math.pi * min(i, n - i) / max(n, 1))
        v = int(amp * env * 32767 * math.sin(2 * math.pi * freq_hz * i / rate))
        out += struct.pack("<h", max(-32768, min(32767, v)))
    return bytes(out)


_VOICE_FREQ = {"Charon": 196.0, "Kore": 262.0, "Orus": 220.0, "Aoede": 330.0, "Leda": 294.0,
               "Zephyr": 349.0}


class _LocalConnection(LiveConnection):
    """Deterministic stand-in. Replies with a canned, persona-appropriate line whenever
    the candidate stops speaking, so turn-taking, transcript persistence, the report and
    the whole browser audio path are exercisable offline and in CI."""

    _LINES: Dict[str, List[str]] = {
        "hr": [
            "Hi, I'm Donna, an AI interviewer here to kick things off. Tell me a little "
            "about yourself and what drew you to this role?",
            "Thanks for sharing that. I'll hand you over to the rest of the panel now.",
        ],
        "tech": [
            "I'm an AI technical interviewer. Walk me through the hardest part of that project.",
            "Understood. What happens when that scales a hundred times?",
            "How did you test that it was actually correct?",
        ],
        "product": [
            "I'm an AI product interviewer. That works, but what did it change for the customer?",
            "Which metric moved, and by how much?",
            "Why did you build that before anything else?",
        ],
        "hiring_manager": [
            "I'm an AI hiring manager on this panel. What was your part of that, specifically?",
            "What would you do differently if you ran it again?",
            "Who did you have to convince, and how?",
        ],
        "customer": [
            "I'm an AI standing in for a customer. Explain that to me as if I'm the buyer.",
            "I don't follow the jargon. Try again in plain language?",
            "What would you promise me, and what wouldn't you?",
        ],
        "behavioural": [
            "I'm an AI behavioural interviewer. Tell me about a time a decision went badly.",
            "What did you actually do about it?",
            "What did you take away from that?",
        ],
    }

    def __init__(self, persona_key: str) -> None:
        self.persona_key = persona_key
        self._queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue()
        self._turn = 0
        self._closed = False
        self._heard = 0
        self._freq = _VOICE_FREQ.get(P.get(persona_key).voice, 220.0)

    async def send_audio(self, pcm16: bytes) -> None:
        # Track that we received audio so the fallback only "answers" after real input.
        self._heard += len(pcm16)

    async def send_text(self, text: str, end_of_turn: bool = True) -> None:
        if end_of_turn:
            await self._speak()

    async def signal_activity_start(self) -> None:
        return None

    async def signal_activity_end(self) -> None:
        # Deliberately silent. Speech happens only when the moderator grants the floor and
        # sends a directive (send_text) — mirroring the manual-activity Gemini config.
        return None

    async def _speak(self) -> None:
        if self._closed:
            return
        lines = self._LINES.get(self.persona_key) or ["Tell me more about that."]
        line = lines[self._turn % len(lines)]
        self._turn += 1
        self._heard = 0
        await self._queue.put({"type": EV_OUTPUT_TRANSCRIPT, "text": line})
        # One tone burst per ~12 characters, so longer lines "speak" for longer.
        for _ in range(max(2, len(line) // 12)):
            await self._queue.put({"type": EV_AUDIO, "pcm": _tone(self._freq, 90)})
        await self._queue.put({"type": EV_TURN_COMPLETE})

    async def events(self) -> AsyncIterator[Dict[str, Any]]:
        while not self._closed:
            try:
                ev = await asyncio.wait_for(self._queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            if ev.get("type") == "__close__":
                break
            yield ev

    async def close(self) -> None:
        self._closed = True
        await self._queue.put({"type": "__close__"})


class LocalVoiceProvider(LiveVoiceProvider):
    name = "local"
    available = True

    async def connect(self, persona_key: str, *, session_label: str = "") -> LiveConnection:
        P.get(persona_key)          # validate
        return _LocalConnection(persona_key)


# --------------------------------------------------------------------------

_gemini: Optional[GeminiLiveProvider] = None


def get_provider() -> LiveVoiceProvider:
    global _gemini
    if settings.GEMINI_API_KEY:
        if _gemini is None:
            _gemini = GeminiLiveProvider()
        try:
            import google.genai  # noqa: F401,PLC0415
            return _gemini
        except ImportError:
            # Key set but the SDK is not installed — fall back rather than 500 on connect.
            pass
    return LocalVoiceProvider()


def provider_status() -> str:
    return get_provider().name
