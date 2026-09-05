"""Test environment.

`app.config` now loads `backend/.env`, which on a developer machine holds REAL keys.
Without this file the suite would quietly start making live Gemini and Agora calls:
slow, non-deterministic, billable, and — worst of all — it would mean the offline
deterministic paths that carry PS11's guarantees stop being exercised at all.

`_load_dotenv` uses `os.environ.setdefault`, so setting these to empty strings HERE
(before any app import) wins over the file.
"""
from __future__ import annotations

import os
import tempfile

# Force the offline paths. Individual tests that want a live model must opt in
# explicitly via the `live_gemini` fixture below.
os.environ.setdefault("HIREIQ_TEST", "1")
for key in ("GEMINI_API_KEY", "AGORA_APP_ID", "AGORA_APP_CERTIFICATE",
            "AGORA_CUSTOMER_ID", "AGORA_CUSTOMER_SECRET"):
    os.environ[key] = ""

os.environ["JWT_SECRET"] = "test-candidate-secret"
os.environ["EMPLOYER_JWT_SECRET"] = "test-employer-secret"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mktemp(suffix='.db')}")

# The turn-settle debounce is real wall-clock. Production wants ~0.9s so a spoken
# sentence is not split; tests want it near zero so the suite stays fast. The local
# voice fallback emits one turn_complete per turn, so shortening it changes nothing
# about what is being verified.
os.environ["INTERVIEW_TURN_SETTLE_S"] = "0.02"
# Same reasoning for the other direction: the candidate-transcription settle window is
# real wall-clock in production (Gemini keeps transcribing for seconds after the mic
# stops), and no test needs to sit through it.
os.environ["INTERVIEW_CAND_SETTLE_S"] = "0.02"
os.environ["INTERVIEW_CAND_SETTLE_MAX_S"] = "0.2"
os.environ["INTERVIEW_CAND_FIRST_WORD_S"] = "0.05"

import pytest  # noqa: E402


@pytest.fixture
def live_gemini(monkeypatch):
    """Opt in to the real Gemini API, or skip when no key is available.

    Reads the key straight from .env rather than the (deliberately blanked) environment.
    """
    import pathlib

    from app.config import settings
    from app.interview import gemini as GEM

    key = ""
    env = pathlib.Path(__file__).resolve().parent.parent / ".env"
    if env.is_file():
        for line in env.read_text().splitlines():
            if line.strip().startswith("GEMINI_API_KEY="):
                key = line.split("=", 1)[1].strip()
                break
    if not key:
        pytest.skip("no GEMINI_API_KEY in backend/.env — live model tests skipped")

    monkeypatch.setattr(settings, "gemini_api_key", key, raising=False)
    GEM._reset_for_tests()
    yield GEM
    GEM._reset_for_tests()
