"""Agora Conversational AI Engine — voice for the panel.

WHY THIS EXISTS, AND WHY IT IS SHAPED THIS WAY
----------------------------------------------
PS11's differentiator is that a deterministic server-side Moderator owns the floor
(rules R1-R6). Agora's ConvoAI agent will happily run the whole loop itself — ASR, LLM,
TTS, turn detection — and if it does, the Moderator is bypassed and the differentiator
is gone.

So this integration deliberately uses ConvoAI in a narrow mode:

  * `turn_detection` is set to MANUAL for both start- and end-of-speech, so Agora never
    decides when a turn ended. Our client-side VAD still reports that, and the Moderator
    still decides who speaks next.
  * The agent's own LLM is never used to author a reply. We push each persona's line
    through `POST /speak`, which broadcasts text verbatim through the TTS module.
  * One agent per persona, each with its own `agent_rtc_uid` and its own `tts.params`
    voice, so the five interviewers are five distinct channel participants with five
    distinct voices — which is what makes the panel legible in the room.
  * `priority=INTERRUPT` on speak, plus `interruption.enable`, gives barge-in.

The result: Agora carries the media AND the speech, while turn-taking stays ours.

Docs: https://docs.agora.io/en/api-reference/api-ref/conversational-ai
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

from app.config import settings
from app.interview import personas as P

log = logging.getLogger("hireiq.convoai")

BASE = "https://api.agora.io/api/conversational-ai-agent/v2/projects"

#: TTS vendor. Verified against a live project: `openai`, `elevenlabs` and `google`
#: join in ~1.5s on Agora-managed credentials. `microsoft` (Azure) and `cartesia` /
#: `deepgram` require BYOK keys — without them Agora dials the vendor, gets nothing,
#: and returns 400 ErrInternal after a 30-SECOND stall. That stall is what previously
#: looked like "Agora is unreachable".
TTS_VENDOR = os.getenv("AGORA_TTS_VENDOR", "openai")

#: One voice per persona, chosen for distinguishability — two similar voices make a
#: panel sound like one person changing their mind.
VOICE_MAP: dict[str, str] = {
    "hr": "coral",              # clear, polished — the recruiter opening the call
    "tech": "onyx",             # deep, measured
    "product": "nova",          # bright, quick
    "hiring_manager": "echo",   # even, senior
    "customer": "shimmer",      # warm, non-technical
    "behavioural": "fable",     # distinct accent
}

#: Vendors that need no external credentials, and how each names its voice parameter.
_VOICE_PARAM = {"openai": "voice", "google": "voice_name", "elevenlabs": "voice_id"}


def _tts_block(persona_key: str) -> dict[str, Any]:
    voice = VOICE_MAP.get(persona_key, VOICE_MAP["tech"])
    params: dict[str, Any] = {_VOICE_PARAM.get(TTS_VENDOR, "voice"): voice}
    if TTS_VENDOR == "openai":
        params["model"] = "tts-1"
    return {"vendor": TTS_VENDOR, "params": params}

SPEAK_MAX_BYTES = 512   # hard API limit on `text`


class ConvoAIUnavailable(RuntimeError):
    """Raised when ConvoAI is not configured. Callers fall back to Gemini Live audio."""


def configured() -> bool:
    return bool(
        settings.AGORA_APP_ID
        and getattr(settings, "AGORA_CUSTOMER_ID", "")
        and getattr(settings, "AGORA_CUSTOMER_SECRET", "")
    )


def _auth_header() -> str:
    raw = "{}:{}".format(settings.AGORA_CUSTOMER_ID, settings.AGORA_CUSTOMER_SECRET)
    return "Basic " + base64.b64encode(raw.encode()).decode()


#: Connect/read timeout for ConvoAI calls. Short on purpose: this is an optional voice
#: upgrade, and a long timeout here previously stalled the interview itself.
HTTP_TIMEOUT_S = 6

#: `speak` is on the critical path of a persona actually being heard, so it gets even
#: less patience — a late line is worse than a missing one.
SPEAK_TIMEOUT_S = 3


def _call(method: str, path: str, body: dict[str, Any] | None = None,
          timeout: float = HTTP_TIMEOUT_S) -> dict[str, Any]:
    if not configured():
        raise ConvoAIUnavailable("AGORA_APP_ID / CUSTOMER_ID / CUSTOMER_SECRET not set")

    url = "{}/{}{}".format(BASE, settings.AGORA_APP_ID, path)
    data = json.dumps(body).encode() if body is not None else None
    req = urlrequest.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", _auth_header())
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read() or b"{}")
    except urlerror.HTTPError as exc:
        detail = (exc.read() or b"").decode()[:400]
        raise ConvoAIUnavailable(
            "Agora ConvoAI {} {} -> {} {}".format(method, path, exc.code, detail)
        ) from exc
    except OSError as exc:
        raise ConvoAIUnavailable("Agora ConvoAI unreachable: {}".format(exc)) from exc


# --------------------------------------------------------------------------- agent spec
def agent_payload(persona_key: str, channel: str, rtc_token: str,
                  candidate_uid: str, session_label: str) -> dict[str, Any]:
    """The join body for one persona.

    Note what is absent: no system prompt worth speaking of, and `greeting_message` is
    empty. This agent is a mouth, not a mind — every word it says arrives via /speak.
    """
    persona = P.get(persona_key)
    return {
        # Unique per attempt. Reusing a name returns 409 TaskConflict, which on a
        # reconnect looked like the service refusing to work.
        "name": "hireiq-{}-{}-{}".format(session_label, persona_key, int(time.time())),
        "properties": {
            "channel": channel,
            "token": rtc_token,
            "agent_rtc_uid": str(persona.bot_uid),
            "remote_rtc_uids": [str(candidate_uid)],
            "idle_timeout": 120,
            "advanced_features": {"enable_aivad": False},

            # MANUAL on both ends: Agora reports audio, our VAD reports boundaries,
            # our Moderator decides the next speaker. This is the load-bearing setting.
            "turn_detection": {
                "config": {"sos_mode": "manual", "eos_mode": "manual"},
            },
            "interruption": {"enable": True, "mode": "start_of_speech"},

            "asr": {"vendor": "ares", "language": "en-US"},
            "tts": _tts_block(persona_key),
            # Required by the API, but never exercised: we do not call the agent's LLM.
            # A blank system message plus /speak-only usage keeps authorship server-side.
            "llm": {
                "url": "https://api.openai.com/v1/chat/completions",
                "vendor": "openai",
                "params": {"model": "gpt-4o-mini"},
                "system_messages": [
                    {"role": "system",
                     "content": "Say nothing on your own. Wait for broadcast messages."},
                ],
                "greeting_message": "",
                "max_history": 1,
            },
        },
    }


# ----------------------------------------------------------------------------- lifecycle
async def start_agent(persona_key: str, *, channel: str, rtc_token: str,
                      candidate_uid: str, session_label: str) -> str:
    """Join one persona agent to the channel. Returns its agent_id."""
    body = agent_payload(persona_key, channel, rtc_token, candidate_uid, session_label)
    result = await asyncio.to_thread(_call, "POST", "/join", body)
    agent_id = result.get("agent_id") or result.get("agentId") or ""
    if not agent_id:
        raise ConvoAIUnavailable("join returned no agent_id: {}".format(result))
    log.info("convoai agent %s joined as %s", agent_id, persona_key)
    return agent_id


async def stop_agent(agent_id: str) -> None:
    try:
        await asyncio.to_thread(_call, "POST", "/agents/{}/leave".format(agent_id))
    except ConvoAIUnavailable as exc:
        # A leaked agent idles out on its own; never fail an interview teardown on this.
        log.warning("convoai leave failed for %s: %s", agent_id, exc)


async def speak(agent_id: str, text: str, *, interrupt: bool = True) -> None:
    """Broadcast one persona line through Agora TTS, verbatim.

    Text over the 512-byte API limit is truncated at a sentence boundary rather than
    mid-word — a persona cut off mid-syllable reads as a broken product.
    """
    payload = text.strip()
    if not payload:
        return
    if len(payload.encode()) > SPEAK_MAX_BYTES:
        payload = _clip(payload, SPEAK_MAX_BYTES)

    await asyncio.to_thread(
        _call, "POST", "/agents/{}/speak".format(agent_id), {
            "text": payload,
            "priority": "INTERRUPT" if interrupt else "APPEND",
            "interruptable": True,  # the candidate can always talk over an interviewer
        }, SPEAK_TIMEOUT_S)


async def interrupt(agent_id: str) -> None:
    """Stop a persona mid-sentence. Called the instant client VAD reports speech."""
    try:
        await asyncio.to_thread(_call, "POST", "/agents/{}/interrupt".format(agent_id))
    except ConvoAIUnavailable as exc:
        log.debug("convoai interrupt failed for %s: %s", agent_id, exc)


def _clip(text: str, limit: int) -> str:
    out = text.encode()[:limit].decode("utf-8", errors="ignore")
    for stop in (". ", "? ", "! "):
        cut = out.rfind(stop)
        if cut > limit // 2:
            return out[: cut + 1].strip()
    return out.rsplit(" ", 1)[0].strip()
