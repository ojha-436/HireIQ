"""One place that talks to Gemini for text generation.

WHY THIS MODULE EXISTS
----------------------
The ported engine called `google.generativeai` — the DEPRECATED SDK, which was not even
in requirements.txt. Every one of those call sites therefore raised ImportError and fell
through to a local heuristic. The effect was quiet and bad: setting GEMINI_API_KEY looked
like it enabled real scoring, and it did not.

Consolidating here means the SDK, the model id, and the JSON-mode config are decided once,
and `available()` gives callers an honest answer about whether a real model is reachable.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.config import settings

log = logging.getLogger("hireiq.gemini")

_client: Any = None
_probed = False


def available() -> bool:
    """True only when a key is set AND the current SDK actually imports."""
    if not settings.GEMINI_API_KEY:
        return False
    return _get_client() is not None


def _get_client() -> Any:
    global _client, _probed
    if _probed:
        return _client
    _probed = True
    if not settings.GEMINI_API_KEY:
        return None
    try:
        from google import genai  # noqa: PLC0415
    except ImportError:
        log.warning("google-genai is not installed; Gemini calls will use local fallbacks")
        return None
    try:
        _client = genai.Client(api_key=settings.GEMINI_API_KEY)
    except Exception as exc:  # noqa: BLE001
        log.warning("Gemini client init failed: %s", exc)
        _client = None
    return _client


def generate_json(prompt: str, *, temperature: float = 0.1,
                  model: str | None = None) -> dict[str, Any] | None:
    """Generate and parse a JSON object, or None on any failure.

    Returning None rather than raising is deliberate: every caller has a deterministic
    fallback, and an interview must not die because a scoring call timed out.
    """
    client = _get_client()
    if client is None:
        return None
    try:
        resp = client.models.generate_content(
            model=model or settings.GEMINI_MODEL,
            contents=prompt,
            config={"temperature": temperature, "response_mime_type": "application/json"},
        )
        data = json.loads((resp.text or "").strip())
    except Exception as exc:  # noqa: BLE001
        log.warning("Gemini JSON call failed: %s", exc)
        return None
    return data if isinstance(data, dict) else None


def generate_text(prompt: str, *, temperature: float = 0.2,
                  model: str | None = None) -> str | None:
    client = _get_client()
    if client is None:
        return None
    try:
        resp = client.models.generate_content(
            model=model or settings.GEMINI_MODEL,
            contents=prompt,
            config={"temperature": temperature},
        )
        return (resp.text or "").strip() or None
    except Exception as exc:  # noqa: BLE001
        log.warning("Gemini text call failed: %s", exc)
        return None


def _reset_for_tests() -> None:
    global _client, _probed
    _client, _probed = None, False
