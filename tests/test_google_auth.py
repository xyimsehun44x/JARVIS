from __future__ import annotations

import pytest
from google.auth.exceptions import RefreshError

from jarvis.config import Settings
from jarvis.integrations import google_auth


class RevokedCredentials:
    expired = True
    refresh_token = "present"
    valid = False

    def refresh(self, request) -> None:
        del request
        raise RefreshError("invalid_grant: Token has been expired or revoked.")


class FreshCredentials:
    expired = False
    refresh_token = "fresh"
    valid = True

    def has_scopes(self, scopes) -> bool:
        return bool(scopes)

    def to_json(self) -> str:
        return '{"authorized": true}'


def test_revoked_google_refresh_token_falls_back_to_interactive_authorization(
    tmp_path, monkeypatch
) -> None:
    credentials_path = tmp_path / "credentials.json"
    token_path = tmp_path / "google.token.json"
    credentials_path.write_text("{}", encoding="utf-8")
    token_path.write_text("revoked", encoding="utf-8")
    oauth = google_auth.GoogleOAuth(
        str(credentials_path),
        str(token_path),
        [google_auth.CONTACTS_READONLY_SCOPE],
    )
    monkeypatch.setattr(oauth, "_load_token", lambda: RevokedCredentials())
    fresh = FreshCredentials()

    class Flow:
        def run_local_server(self, *, port: int):
            assert port == 0
            return fresh

    monkeypatch.setattr(
        google_auth.InstalledAppFlow,
        "from_client_secrets_file",
        lambda path, scopes: Flow(),
    )

    assert oauth.authorize(interactive=True) is fresh
    assert token_path.read_text(encoding="utf-8") == '{"authorized": true}'


def test_revoked_google_refresh_token_requires_setup_when_noninteractive(
    tmp_path, monkeypatch
) -> None:
    oauth = google_auth.GoogleOAuth(
        str(tmp_path / "credentials.json"),
        str(tmp_path / "google.token.json"),
        [google_auth.CONTACTS_READONLY_SCOPE],
    )
    monkeypatch.setattr(oauth, "_load_token", lambda: RevokedCredentials())

    with pytest.raises(RuntimeError, match="--setup-google"):
        oauth.authorize(interactive=False)


def test_gmail_reconciliation_requests_compose_and_readonly_scopes() -> None:
    scopes = google_auth.scopes_for_settings(
        Settings(enable_gmail=True, enable_calendar=False, enable_contacts=False)
    )

    assert google_auth.GMAIL_COMPOSE_SCOPE in scopes
    assert google_auth.GMAIL_READONLY_SCOPE in scopes
