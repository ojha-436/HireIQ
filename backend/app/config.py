"""Runtime configuration. Everything is env-driven; no secret has a usable default."""
import os
from functools import lru_cache
from pathlib import Path


def _load_dotenv() -> None:
    """Read backend/.env into os.environ if present.

    Stdlib only, on purpose: this repo already avoids third-party crypto and the same
    principle applies here. Real environment variables always win over the file, so a
    container or Cloud Run config is never silently overridden by a stray local .env.
    """
    path = Path(__file__).resolve().parent.parent / ".env"
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        # Strip one layer of matching quotes so KEY="value with spaces" works.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


_load_dotenv()


class Settings:
    def __init__(self) -> None:
        self.jwt_secret = os.getenv("JWT_SECRET", "dev-candidate-secret-change-me")
        self.employer_jwt_secret = os.getenv("EMPLOYER_JWT_SECRET", "dev-employer-secret-change-me")
        self.admin_jwt_secret = os.getenv("ADMIN_JWT_SECRET", "dev-admin-secret-change-me")
        self.jwt_ttl_hours = int(os.getenv("JWT_TTL_HOURS", "12"))

        self.database_url = os.getenv("DATABASE_URL", "sqlite:///./dev.db")

        self.gemini_api_key = os.getenv("GEMINI_API_KEY", "")
        self.gemini_live_model = os.getenv(
            # NATIVE-AUDIO on purpose. gemini-3.1-flash-live-preview is newer but is
            # HALF-CASCADE (STT -> LLM -> TTS internally), which reintroduces exactly the
            # loop this architecture claims to bypass and adds latency to barge-in.
            # Only swap this for another *native-audio* id.
            "GEMINI_LIVE_MODEL", "gemini-2.5-flash-native-audio-preview-12-2025"
        )
        self.agora_app_id = os.getenv("AGORA_APP_ID", "")
        self.agora_app_certificate = os.getenv("AGORA_APP_CERTIFICATE", "")

        self.turn_ttl_days = int(os.getenv("INTERVIEW_TURN_TTL_DAYS", "60"))
        self.digest_token = os.getenv("DIGEST_TOKEN", "dev-cron-token")

        raw = os.getenv("CORS_ORIGINS", "http://localhost:5500,http://127.0.0.1:5500")
        self.cors_origins = [o.strip() for o in raw.split(",") if o.strip()]

        # Interview engine tunables
        self.interview_preset = os.getenv("INTERVIEW_PRESET", "panel")
        self.interview_max_minutes = int(os.getenv("INTERVIEW_MAX_MINUTES", "18"))
        self.agora_token_ttl_s = int(os.getenv("AGORA_TOKEN_TTL_S", "3600"))
        # Text reasoning: analyst scoring, report prose, moderator tiebreak, summaries.
        self.gemini_model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
        # Agora RESTful API credentials (Console -> RESTful API). Distinct from the
        # RTC app certificate, which signs channel tokens.
        self.agora_customer_id = os.getenv("AGORA_CUSTOMER_ID", "")
        self.agora_customer_secret = os.getenv("AGORA_CUSTOMER_SECRET", "")
        self.voice_provider = os.getenv("VOICE_PROVIDER", "auto")  # auto|gemini|agora
        self.agora_recording_key = os.getenv("AGORA_RECORDING_KEY", "")
        self.agora_recording_secret = os.getenv("AGORA_RECORDING_SECRET", "")
        self.gcs_recording_bucket = os.getenv("GCS_RECORDING_BUCKET", "")
        self.gcs_project_id = os.getenv("GCS_PROJECT_ID", "")
        self.gcs_service_account_json = os.getenv("GCS_SERVICE_ACCOUNT_JSON", "")

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    def __getattr__(self, name: str):
        """The ported interview engine reads SCREAMING_CASE settings (settings.GEMINI_API_KEY).

        HireIQ's own code uses snake_case. Rather than rewrite 3,200 lines of tested engine,
        map the uppercase form onto the snake_case attribute.
        """
        if name.isupper():
            lower = name.lower()
            if lower in self.__dict__:
                return self.__dict__[lower]
        raise AttributeError(name)


@lru_cache
def get_settings() -> Settings:
    return Settings()


#: Module-level singleton. `from app.config import settings` is how the interview
#: engine reaches configuration.
settings = get_settings()
