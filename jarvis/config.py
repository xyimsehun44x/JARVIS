from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Settings:
    mode: str = "mock"
    llm_provider: str = "rules"
    openai_model: str = "gpt-5.2"
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.8-flash"
    gemini_thinking_level: str = "low"
    stt_model: str = "small.en"
    stt_beam_size: int = 5
    stt_hotwords: str = "Jarvis"
    stt_initial_prompt: str = (
        "Jarvis personal assistant; Tell me something interesting; email, calendar, "
        "meeting, weather, forecast, contacts, Joo, Seoul, Busan"
    )
    stt_vad_filter: bool = True
    stt_action_confidence_threshold: float = 0.35
    stt_vocabulary_limit: int = 15
    vocabulary_path: str = "jarvis.vocabulary.json"
    latency_logging: bool = False
    confirm_drafts: bool = False
    user_name: str | None = None
    timezone: str = "Asia/Seoul"
    default_location: str | None = None
    weather_timeout_seconds: float = 5.0
    persistence: str = "memory"
    database_path: str = "jarvis.db"
    google_credentials_path: str = "credentials.json"
    google_token_path: str = "google.token.json"
    enable_gmail: bool = True
    enable_calendar: bool = True
    enable_contacts: bool = True
    allow_email_send: bool = False
    allow_calendar_writes: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        mode = os.getenv("JARVIS_MODE", "mock").lower()
        return cls(
            mode=mode,
            llm_provider=os.getenv(
                "JARVIS_LLM_PROVIDER", "openai" if mode == "google" else "rules"
            ).lower(),
            openai_model=os.getenv("JARVIS_OPENAI_MODEL", "gpt-5.2"),
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            openai_base_url=os.getenv("JARVIS_OPENAI_BASE_URL") or None,
            gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
            gemini_model=os.getenv("JARVIS_GEMINI_MODEL", "gemini-3.8-flash"),
            gemini_thinking_level=os.getenv(
                "JARVIS_GEMINI_THINKING_LEVEL", "low"
            ).lower(),
            stt_model=os.getenv("JARVIS_STT_MODEL", "small.en"),
            stt_beam_size=int(os.getenv("JARVIS_STT_BEAM_SIZE", "5")),
            stt_hotwords=os.getenv("JARVIS_STT_HOTWORDS", "Jarvis"),
            stt_initial_prompt=os.getenv(
                "JARVIS_STT_INITIAL_PROMPT",
                "Jarvis personal assistant; Tell me something interesting; email, calendar, "
                "meeting, weather, forecast, contacts, Joo, Seoul, Busan",
            ),
            stt_vad_filter=_env_bool("JARVIS_STT_VAD_FILTER", True),
            stt_action_confidence_threshold=float(
                os.getenv("JARVIS_STT_ACTION_CONFIDENCE_THRESHOLD", "0.35")
            ),
            stt_vocabulary_limit=int(os.getenv("JARVIS_STT_VOCABULARY_LIMIT", "15")),
            vocabulary_path=os.getenv(
                "JARVIS_VOCABULARY_PATH", "jarvis.vocabulary.json"
            ),
            latency_logging=_env_bool("JARVIS_LATENCY_LOGGING", False),
            confirm_drafts=os.getenv("JARVIS_CONFIRM_DRAFTS", "false").lower()
            in {"1", "true", "yes", "on"},
            user_name=os.getenv("JARVIS_USER_NAME") or None,
            timezone=os.getenv("JARVIS_TIMEZONE", "Asia/Seoul"),
            default_location=os.getenv("JARVIS_DEFAULT_LOCATION") or None,
            weather_timeout_seconds=float(os.getenv("JARVIS_WEATHER_TIMEOUT_SECONDS", "5")),
            persistence=os.getenv(
                "JARVIS_PERSISTENCE", "sqlite" if mode == "google" else "memory"
            ).lower(),
            database_path=os.getenv("JARVIS_DATABASE_PATH", "jarvis.db"),
            google_credentials_path=os.getenv(
                "JARVIS_GOOGLE_CREDENTIALS", "credentials.json"
            ),
            google_token_path=os.getenv("JARVIS_GOOGLE_TOKEN", "google.token.json"),
            enable_gmail=_env_bool("JARVIS_ENABLE_GMAIL", True),
            enable_calendar=_env_bool("JARVIS_ENABLE_CALENDAR", True),
            enable_contacts=_env_bool("JARVIS_ENABLE_CONTACTS", True),
            allow_email_send=_env_bool("JARVIS_ALLOW_EMAIL_SEND", False),
            allow_calendar_writes=_env_bool("JARVIS_ALLOW_CALENDAR_WRITES", False),
        )

    def configuration_errors(self) -> list[str]:
        errors: list[str] = []
        if self.mode not in {"mock", "google"}:
            errors.append("JARVIS_MODE must be 'mock' or 'google'")
        if self.llm_provider not in {"rules", "openai", "gemini"}:
            errors.append("JARVIS_LLM_PROVIDER must be 'rules', 'openai', or 'gemini'")
        if self.llm_provider == "openai" and not self.openai_api_key:
            errors.append("OPENAI_API_KEY is required for the OpenAI provider")
        if self.llm_provider == "gemini" and not self.gemini_api_key:
            errors.append("GEMINI_API_KEY is required for the Gemini provider")
        if self.gemini_thinking_level not in {"minimal", "low", "medium", "high"}:
            errors.append(
                "JARVIS_GEMINI_THINKING_LEVEL must be minimal, low, medium, or high"
            )
        if self.stt_beam_size < 1:
            errors.append("JARVIS_STT_BEAM_SIZE must be at least 1")
        if not 0 <= self.stt_action_confidence_threshold <= 1:
            errors.append(
                "JARVIS_STT_ACTION_CONFIDENCE_THRESHOLD must be between 0 and 1"
            )
        if not 1 <= self.stt_vocabulary_limit <= 15:
            errors.append("JARVIS_STT_VOCABULARY_LIMIT must be between 1 and 15")
        if self.weather_timeout_seconds <= 0:
            errors.append("JARVIS_WEATHER_TIMEOUT_SECONDS must be greater than zero")
        if self.mode == "google" and not Path(self.google_credentials_path).is_file():
            errors.append(
                f"Google desktop OAuth credentials not found: {self.google_credentials_path}"
            )
        return errors


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}
