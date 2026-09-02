from __future__ import annotations

import base64
from datetime import datetime
from email import message_from_bytes

from jarvis.agents.calendar.schemas import CalendarProposal
from jarvis.agents.email.schemas import EmailProposal
from jarvis.integrations.calendar import GoogleCalendarProvider
from jarvis.integrations.gmail import GoogleGmailProvider


class Request:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class GmailDrafts:
    def __init__(self):
        self.raw = None

    def create(self, **kwargs):
        self.raw = kwargs["body"]["message"]["raw"]
        return Request({"id": "draft-1"})

    def get(self, **kwargs):
        return Request({"id": kwargs["id"]})


class GmailMessages:
    def __init__(self):
        self.raw = None

    def send(self, **kwargs):
        self.raw = kwargs["body"]["raw"]
        return Request({"id": "message-1"})

    def get(self, **kwargs):
        return Request({"id": kwargs["id"]})


class GmailUsers:
    def __init__(self):
        self.drafts_api = GmailDrafts()
        self.messages_api = GmailMessages()

    def drafts(self):
        return self.drafts_api

    def messages(self):
        return self.messages_api


class GmailService:
    def __init__(self):
        self.users_api = GmailUsers()

    def users(self):
        return self.users_api


class OAuth:
    def __init__(self, service):
        self.api = service

    def service(self, *_):
        return self.api


def test_gmail_provider_builds_mime_verifies_and_enforces_send_gate() -> None:
    service = GmailService()
    proposal = EmailProposal(
        recipient_name="Jisoo Park",
        recipient_email="jisoo@example.com",
        subject="Lunch",
        body="Are you free?",
        operation="send",
    )
    locked = GoogleGmailProvider(OAuth(service), allow_send=False)
    assert not locked.send(proposal).ok
    assert service.users_api.messages_api.raw is None

    provider = GoogleGmailProvider(OAuth(service), allow_send=True)
    result = provider.send(proposal)
    assert result.ok and result.message_id == "message-1"
    decoded = message_from_bytes(base64.urlsafe_b64decode(service.users_api.messages_api.raw))
    assert decoded["To"] == "jisoo@example.com"
    assert decoded["Subject"] == "Lunch"


class CalendarEvents:
    def __init__(self):
        self.items = {
            "event-1": {
                "id": "event-1",
                "summary": "Lunch",
                "start": {"dateTime": "2026-09-01T13:00:00+09:00"},
                "end": {"dateTime": "2026-09-01T14:00:00+09:00"},
            }
        }
        self.insert_body = None

    def list(self, **kwargs):
        return Request({"items": list(self.items.values())})

    def insert(self, **kwargs):
        self.insert_body = kwargs["body"]
        self.items["event-new"] = {"id": "event-new", **kwargs["body"]}
        return Request({"id": "event-new"})

    def get(self, **kwargs):
        return Request(self.items[kwargs["eventId"]])


class CalendarService:
    def __init__(self):
        self.events_api = CalendarEvents()

    def events(self):
        return self.events_api


def test_calendar_provider_reads_creates_and_verifies_exact_payload() -> None:
    service = CalendarService()
    provider = GoogleCalendarProvider(
        OAuth(service), timezone_name="Asia/Seoul", allow_writes=True
    )
    events = provider.list_events(
        datetime(2026, 9, 1), datetime(2026, 9, 2)
    )
    assert events[0].title == "Lunch"
    proposal = CalendarProposal(
        operation="create_event",
        title="Design review",
        start=datetime(2026, 9, 1, 15),
        end=datetime(2026, 9, 1, 16),
        attendees=["jisoo@example.com"],
    )
    result = provider.create_event(proposal)
    assert result.ok and result.event_id == "event-new"
    assert service.events_api.insert_body["attendees"] == [{"email": "jisoo@example.com"}]


def test_calendar_provider_write_gate_prevents_api_call() -> None:
    service = CalendarService()
    provider = GoogleCalendarProvider(
        OAuth(service), timezone_name="Asia/Seoul", allow_writes=False
    )
    proposal = CalendarProposal(
        operation="create_event",
        title="Should not be created",
        start=datetime(2026, 9, 1, 15),
        end=datetime(2026, 9, 1, 16),
    )
    result = provider.create_event(proposal)
    assert not result.ok
    assert "disabled" in result.error
    assert service.events_api.insert_body is None
