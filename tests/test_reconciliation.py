from __future__ import annotations

import sqlite3
from datetime import datetime

from jarvis.agents.calendar.schemas import CalendarProposal
from jarvis.agents.email.schemas import EmailProposal
from jarvis.core.execution import (
    ExecutionRegistry,
    payload_hash,
    provider_operation_id,
)
from jarvis.core.permissions import ProposedAction, RiskLevel
from jarvis.integrations.calendar import GoogleCalendarProvider
from jarvis.integrations.gmail import GoogleGmailProvider


class Request:
    def __init__(self, callback):
        self.callback = callback

    def execute(self):
        return self.callback()


class GoogleNotFoundError(Exception):
    def __init__(self):
        self.resp = type("Response", (), {"status": 404})()
        super().__init__("not found")


class OAuth:
    def __init__(self, service):
        self.api = service

    def service(self, *_):
        return self.api


class ReconciliationCalendarEvents:
    def __init__(self, failure: str):
        self.failure = failure
        self.items: dict[str, dict] = {}
        self.insert_calls = 0

    def insert(self, **kwargs):
        body = kwargs["body"]

        def execute():
            self.insert_calls += 1
            event_id = body["id"]
            if self.failure == "before":
                self.failure = "none"
                raise RuntimeError("timeout before Calendar applied the write")
            self.items[event_id] = {"id": event_id, **body}
            if self.failure == "after":
                self.failure = "none"
                raise RuntimeError("timeout after Calendar applied the write")
            return {"id": event_id}

        return Request(execute)

    def get(self, **kwargs):
        def execute():
            event_id = kwargs["eventId"]
            if event_id not in self.items:
                raise GoogleNotFoundError()
            return self.items[event_id]

        return Request(execute)


class ReconciliationCalendarService:
    def __init__(self, failure: str):
        self.events_api = ReconciliationCalendarEvents(failure)

    def events(self):
        return self.events_api


class ReconciliationGmailMessages:
    def __init__(self, failure: str):
        self.failure = failure
        self.items: dict[str, dict] = {}
        self.send_calls = 0

    def send(self, **kwargs):
        raw = kwargs["body"]["raw"]

        def execute():
            self.send_calls += 1
            if self.failure == "before":
                self.failure = "none"
                raise RuntimeError("timeout before Gmail applied the send")
            self.items["message-1"] = {"id": "message-1", "raw": raw}
            if self.failure == "after":
                self.failure = "none"
                raise RuntimeError("timeout after Gmail applied the send")
            return {"id": "message-1"}

        return Request(execute)

    def list(self, **kwargs):
        return Request(
            lambda: {
                "messages": [
                    {"id": item["id"]} for item in self.items.values()
                ]
            }
        )

    def get(self, **kwargs):
        def execute():
            message_id = kwargs["id"]
            if message_id not in self.items:
                raise GoogleNotFoundError()
            return self.items[message_id]

        return Request(execute)


class ReconciliationGmailDrafts:
    def __init__(self, messages: ReconciliationGmailMessages):
        self.messages = messages
        self.items: dict[str, dict] = {}
        self.create_calls = 0

    def create(self, **kwargs):
        raw = kwargs["body"]["message"]["raw"]

        def execute():
            self.create_calls += 1
            message = {"id": "draft-message-1", "raw": raw}
            self.messages.items[message["id"]] = message
            self.items["draft-1"] = {"id": "draft-1", "message": message}
            raise RuntimeError("timeout after Gmail applied the draft creation")

        return Request(execute)

    def list(self, **kwargs):
        return Request(lambda: {"drafts": list(self.items.values())})

    def get(self, **kwargs):
        def execute():
            draft_id = kwargs["id"]
            if draft_id not in self.items:
                raise GoogleNotFoundError()
            return self.items[draft_id]

        return Request(execute)


class ReconciliationGmailUsers:
    def __init__(self, failure: str):
        self.messages_api = ReconciliationGmailMessages(failure)
        self.drafts_api = ReconciliationGmailDrafts(self.messages_api)

    def messages(self):
        return self.messages_api

    def drafts(self):
        return self.drafts_api


class ReconciliationGmailService:
    def __init__(self, failure: str):
        self.users_api = ReconciliationGmailUsers(failure)

    def users(self):
        return self.users_api


def calendar_action(proposal: CalendarProposal) -> ProposedAction:
    return ProposedAction(
        tool_name="calendar.create_event",
        risk=RiskLevel.EXTERNAL_WRITE,
        summary="Create exact event",
        payload=proposal.model_dump(mode="json"),
        idempotency_key="thread:task:calendar.create_event",
    )


def email_action(proposal: EmailProposal) -> ProposedAction:
    return ProposedAction(
        tool_name=f"gmail.{proposal.operation}",
        risk=RiskLevel.EXTERNAL_WRITE,
        summary="Apply exact Gmail operation",
        payload=proposal.model_dump(mode="json"),
        idempotency_key=f"thread:task:gmail.{proposal.operation}",
    )


def test_calendar_timeout_after_write_is_verified_after_restart_without_duplicate(
    tmp_path,
) -> None:
    service = ReconciliationCalendarService("after")
    provider = GoogleCalendarProvider(
        OAuth(service), timezone_name="Asia/Seoul", allow_writes=True
    )
    proposal = CalendarProposal(
        operation="create_event",
        title="Design review",
        start=datetime(2026, 10, 5, 15),
        end=datetime(2026, 10, 5, 16),
    )
    action = calendar_action(proposal)
    operation_id = provider_operation_id(action)
    tagged = proposal.model_copy(update={"provider_operation_id": operation_id})
    database = str(tmp_path / "jarvis.db")
    registry = ExecutionRegistry(database)

    first = registry.execute_once(
        action,
        lambda: provider.create_event(tagged).model_dump(mode="json"),
        operation_id=operation_id,
    )
    assert first.status == "uncertain"
    registry.close()
    registry = ExecutionRegistry(database)
    recovered = registry.execute_once(
        action,
        lambda: provider.create_event(tagged).model_dump(mode="json"),
        recover=lambda record: provider.reconcile(
            tagged, record.external_resource_id
        ),
        operation_id=operation_id,
    )

    assert recovered.status == "verified"
    assert recovered.reconciliation_status == "verified"
    assert recovered.attempt_count == 1
    assert recovered.external_resource_id == operation_id
    assert service.events_api.insert_calls == 1
    assert list(service.events_api.items) == [operation_id]
    registry.close()


def test_calendar_timeout_before_write_retries_only_after_not_applied() -> None:
    service = ReconciliationCalendarService("before")
    provider = GoogleCalendarProvider(
        OAuth(service), timezone_name="Asia/Seoul", allow_writes=True
    )
    proposal = CalendarProposal(
        operation="create_event",
        title="Design review",
        start=datetime(2026, 10, 5, 15),
        end=datetime(2026, 10, 5, 16),
    )
    action = calendar_action(proposal)
    operation_id = provider_operation_id(action)
    tagged = proposal.model_copy(update={"provider_operation_id": operation_id})
    registry = ExecutionRegistry()

    registry.execute_once(
        action,
        lambda: provider.create_event(tagged).model_dump(mode="json"),
        operation_id=operation_id,
    )
    recovered = registry.execute_once(
        action,
        lambda: provider.create_event(tagged).model_dump(mode="json"),
        recover=lambda record: provider.reconcile(
            tagged, record.external_resource_id
        ),
        operation_id=operation_id,
    )

    assert recovered.status == "verified"
    assert recovered.reconciliation_status == "verified"
    assert recovered.attempt_count == 2
    assert service.events_api.insert_calls == 2
    assert list(service.events_api.items) == [operation_id]


def test_calendar_update_and_cancel_reconciliation_fail_closed() -> None:
    service = ReconciliationCalendarService("none")
    service.events_api.items["event-1"] = {
        "id": "event-1",
        "summary": "Design review",
        "start": {"dateTime": "2026-10-05T15:00:00+09:00"},
        "end": {"dateTime": "2026-10-05T16:00:00+09:00"},
        "attendees": [],
    }
    provider = GoogleCalendarProvider(
        OAuth(service), timezone_name="Asia/Seoul", allow_writes=True
    )
    update = CalendarProposal(
        operation="update_event",
        event_id="event-1",
        title="Design review",
        start=datetime(2026, 10, 5, 15),
        end=datetime(2026, 10, 5, 16),
    )
    cancel = update.model_copy(update={"operation": "cancel_event"})

    assert provider.reconcile(update, "event-1").outcome == "verified"
    service.events_api.items["event-1"]["summary"] = "Concurrent edit"
    assert provider.reconcile(update, "event-1").outcome == "uncertain"
    assert provider.reconcile(cancel, "event-1").outcome == "not_applied"
    service.events_api.items.pop("event-1")
    assert provider.reconcile(cancel, "event-1").outcome == "verified"


def test_gmail_timeout_after_send_is_verified_without_duplicate() -> None:
    service = ReconciliationGmailService("after")
    provider = GoogleGmailProvider(OAuth(service), allow_send=True)
    proposal = EmailProposal(
        recipient_name="Jisoo",
        recipient_email="jisoo@example.com",
        subject="Lunch",
        body="Are you free?",
        operation="send",
    )
    action = email_action(proposal)
    operation_id = provider_operation_id(action)
    tagged = proposal.model_copy(update={"provider_operation_id": operation_id})
    registry = ExecutionRegistry()

    registry.execute_once(
        action,
        lambda: provider.send(tagged).model_dump(mode="json"),
        operation_id=operation_id,
    )
    recovered = registry.execute_once(
        action,
        lambda: provider.send(tagged).model_dump(mode="json"),
        recover=lambda record: provider.reconcile(
            tagged, record.external_resource_id
        ),
        operation_id=operation_id,
    )

    assert recovered.status == "verified"
    assert recovered.external_resource_id == "message-1"
    assert recovered.attempt_count == 1
    assert service.users_api.messages_api.send_calls == 1


def test_gmail_timeout_before_send_remains_uncertain_and_is_not_retried() -> None:
    service = ReconciliationGmailService("before")
    provider = GoogleGmailProvider(OAuth(service), allow_send=True)
    proposal = EmailProposal(
        recipient_name="Jisoo",
        recipient_email="jisoo@example.com",
        subject="Lunch",
        body="Are you free?",
        operation="send",
    )
    action = email_action(proposal)
    operation_id = provider_operation_id(action)
    tagged = proposal.model_copy(update={"provider_operation_id": operation_id})
    registry = ExecutionRegistry()

    registry.execute_once(
        action,
        lambda: provider.send(tagged).model_dump(mode="json"),
        operation_id=operation_id,
    )
    recovered = registry.execute_once(
        action,
        lambda: provider.send(tagged).model_dump(mode="json"),
        recover=lambda record: provider.reconcile(
            tagged, record.external_resource_id
        ),
        operation_id=operation_id,
    )

    assert recovered.status == "uncertain"
    assert recovered.reconciliation_status == "uncertain"
    assert recovered.attempt_count == 1
    assert service.users_api.messages_api.send_calls == 1


def test_gmail_draft_timeout_after_write_is_reconciled_by_immutable_ids() -> None:
    service = ReconciliationGmailService("none")
    provider = GoogleGmailProvider(OAuth(service), allow_send=False)
    proposal = EmailProposal(
        recipient_name="Jisoo",
        recipient_email="jisoo@example.com",
        subject="Lunch",
        body="Are you free?",
        operation="save_draft",
    )
    action = email_action(proposal)
    operation_id = provider_operation_id(action)
    tagged = proposal.model_copy(update={"provider_operation_id": operation_id})
    registry = ExecutionRegistry()

    registry.execute_once(
        action,
        lambda: provider.save_draft(tagged).model_dump(mode="json"),
        operation_id=operation_id,
    )
    recovered = registry.execute_once(
        action,
        lambda: provider.save_draft(tagged).model_dump(mode="json"),
        recover=lambda record: provider.reconcile(
            tagged, record.external_resource_id
        ),
        operation_id=operation_id,
    )

    assert recovered.status == "verified"
    assert recovered.external_resource_id == "draft-1"
    assert recovered.attempt_count == 1
    assert service.users_api.drafts_api.create_calls == 1


def test_execution_schema_migrates_and_persists_reconciliation_metadata(
    tmp_path,
) -> None:
    database = tmp_path / "legacy.db"
    connection = sqlite3.connect(database)
    connection.execute(
        """
        CREATE TABLE execution_records (
            approved_payload_hash TEXT PRIMARY KEY,
            execution_id TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            external_resource_id TEXT,
            status TEXT NOT NULL,
            result_json TEXT
        )
        """
    )
    connection.commit()
    connection.close()
    proposal = CalendarProposal(
        operation="create_event",
        title="Design review",
        start=datetime(2026, 10, 5, 15),
        end=datetime(2026, 10, 5, 16),
    )
    action = calendar_action(proposal)

    first = ExecutionRegistry(str(database))
    saved = first.execute_once(
        action, lambda: {"ok": True, "event_id": "event-1"}
    )
    first.close()
    second = ExecutionRegistry(str(database))
    loaded = second.execute_once(
        action, lambda: {"ok": True, "event_id": "duplicate"}
    )
    second.close()

    assert saved.provider_operation_id == provider_operation_id(action)
    assert loaded.provider_operation_id == saved.provider_operation_id
    assert loaded.first_attempt_at is not None
    assert loaded.last_attempt_at is not None
    assert loaded.attempt_count == 1
    assert loaded.external_resource_id == "event-1"


def test_execution_schema_migration_preserves_verified_legacy_rows(tmp_path) -> None:
    database = tmp_path / "legacy-with-row.db"
    proposal = CalendarProposal(
        operation="create_event",
        title="Legacy event",
        start=datetime(2026, 10, 5, 15),
        end=datetime(2026, 10, 5, 16),
    )
    action = calendar_action(proposal)
    connection = sqlite3.connect(database)
    connection.execute(
        """
        CREATE TABLE execution_records (
            approved_payload_hash TEXT PRIMARY KEY,
            execution_id TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            external_resource_id TEXT,
            status TEXT NOT NULL,
            result_json TEXT
        )
        """
    )
    connection.execute(
        """
        INSERT INTO execution_records (
            approved_payload_hash, execution_id, tool_name, external_resource_id,
            status, result_json
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            payload_hash(action),
            "legacy-execution",
            action.tool_name,
            "legacy-event-id",
            "verified",
            '{"ok": true, "event_id": "legacy-event-id"}',
        ),
    )
    connection.commit()
    connection.close()
    calls = []

    registry = ExecutionRegistry(str(database))
    loaded = registry.execute_once(
        action,
        lambda: calls.append("duplicate")
        or {"ok": True, "event_id": "duplicate"},
    )
    registry.close()

    assert calls == []
    assert loaded.execution_id == "legacy-execution"
    assert loaded.status == "verified"
    assert loaded.external_resource_id == "legacy-event-id"
