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
    confirm_drafts: bool = False
    user_name: str | None = None
    timezone: str = "Asia/Seoul"
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
            confirm_drafts=os.getenv("JARVIS_CONFIRM_DRAFTS", "false").lower()
            in {"1", "true", "yes", "on"},
            user_name=os.getenv("JARVIS_USER_NAME") or None,
            timezone=os.getenv("JARVIS_TIMEZONE", "Asia/Seoul"),
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
        if self.llm_provider not in {"rules", "openai"}:
            errors.append("JARVIS_LLM_PROVIDER must be 'rules' or 'openai'")
        if self.llm_provider == "openai" and not self.openai_api_key:
            errors.append("OPENAI_API_KEY is required for the OpenAI provider")
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
