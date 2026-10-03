from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import Any

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


GMAIL_COMPOSE_SCOPE = "https://www.googleapis.com/auth/gmail.compose"
CALENDAR_EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"
CALENDAR_READONLY_SCOPE = "https://www.googleapis.com/auth/calendar.events.readonly"
CONTACTS_READONLY_SCOPE = "https://www.googleapis.com/auth/contacts.readonly"


class GoogleOAuth:
    """Shared installed-app OAuth session for all enabled Google specialists."""

    def __init__(self, credentials_path: str, token_path: str, scopes: list[str]) -> None:
        self.credentials_path = Path(credentials_path)
        self.token_path = Path(token_path)
        self.scopes = sorted(set(scopes))
        self._credentials: Credentials | None = None
        self._services: dict[tuple[str, str], Any] = {}
        self._lock = RLock()

    def authorize(self, *, interactive: bool = True) -> Credentials:
        with self._lock:
            credentials = self._load_token()
            if credentials and credentials.expired and credentials.refresh_token:
                try:
                    credentials.refresh(Request())
                except RefreshError:
                    # A revoked refresh token cannot be repaired. Treat it as absent so an
                    # explicit interactive setup can obtain and persist fresh credentials.
                    credentials = None
                    self._credentials = None
            if not credentials or not credentials.valid or not credentials.has_scopes(self.scopes):
                if not interactive:
                    raise RuntimeError(
                        "Google authorization is required. Run `python main.py --setup-google`."
                    )
                if not self.credentials_path.is_file():
                    raise FileNotFoundError(
                        f"Google OAuth credentials not found: {self.credentials_path}"
                    )
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self.credentials_path), self.scopes
                )
                credentials = flow.run_local_server(port=0)
            self._credentials = credentials
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            self.token_path.write_text(credentials.to_json(), encoding="utf-8")
            return credentials

    def service(self, api: str, version: str):
        key = (api, version)
        with self._lock:
            if key not in self._services:
                self._services[key] = build(
                    api,
                    version,
                    credentials=self.authorize(interactive=True),
                    cache_discovery=False,
                )
            return self._services[key]

    def has_token(self) -> bool:
        return self.token_path.is_file()

    def _load_token(self) -> Credentials | None:
        if self._credentials:
            return self._credentials
        if not self.token_path.is_file():
            return None
        try:
            return Credentials.from_authorized_user_file(str(self.token_path), self.scopes)
        except (ValueError, OSError):
            return None


def scopes_for_settings(settings) -> list[str]:
    scopes: list[str] = []
    if settings.enable_gmail:
        scopes.append(GMAIL_COMPOSE_SCOPE)
    if settings.enable_calendar:
        scopes.append(
            CALENDAR_EVENTS_SCOPE
            if settings.allow_calendar_writes
            else CALENDAR_READONLY_SCOPE
        )
    if settings.enable_contacts:
        scopes.append(CONTACTS_READONLY_SCOPE)
    return scopes
