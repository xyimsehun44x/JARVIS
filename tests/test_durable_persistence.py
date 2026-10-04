from __future__ import annotations

import pytest

from jarvis import Jarvis
from jarvis.config import Settings
from jarvis.core.execution import ExecutionRegistry, RecoveryDecision
from jarvis.core.permissions import ProposedAction, RiskLevel


def test_execution_idempotency_survives_restart(tmp_path) -> None:
    database = str(tmp_path / "jarvis.db")
    action = ProposedAction(
        tool_name="gmail.send",
        risk=RiskLevel.EXTERNAL_WRITE,
        summary="Send exact message",
        payload={"to": "jisoo@example.com", "body": "Hello"},
        idempotency_key="thread-1:task-1:gmail.send",
    )
    calls = []

    first = ExecutionRegistry(database)
    first.execute_once(action, lambda: calls.append(1) or {"ok": True, "message_id": "m1"})
    first.close()

    second = ExecutionRegistry(database)
    record = second.execute_once(
        action, lambda: calls.append(2) or {"ok": True, "message_id": "m2"}
    )
    second.close()

    assert calls == [1]
    assert record.external_resource_id == "m1"


def test_interrupted_execution_becomes_uncertain_and_is_not_retried(tmp_path) -> None:
    class SimulatedProcessCrash(BaseException):
        pass

    database = str(tmp_path / "jarvis.db")
    action = ProposedAction(
        tool_name="gmail.send",
        risk=RiskLevel.EXTERNAL_WRITE,
        summary="Send exact message",
        payload={"to": "jisoo@example.com", "body": "Hello"},
        idempotency_key="thread-1:crashed-task:gmail.send",
    )
    calls = []

    def crash_after_possible_effect():
        calls.append("possibly-sent")
        raise SimulatedProcessCrash()

    first = ExecutionRegistry(database)
    with pytest.raises(SimulatedProcessCrash):
        first.execute_once(action, crash_after_possible_effect)
    first.close()

    second = ExecutionRegistry(database)
    record = second.execute_once(
        action, lambda: calls.append("duplicate") or {"ok": True, "message_id": "m2"}
    )
    second.close()

    assert calls == ["possibly-sent"]
    assert record.status == "uncertain"
    assert record.result["outcome_uncertain"] is True
    assert "will not retry" in record.result["error"]


def test_uncertain_execution_can_be_resolved_without_repeating_write(tmp_path) -> None:
    database = str(tmp_path / "jarvis.db")
    action = ProposedAction(
        tool_name="calendar.create_event",
        risk=RiskLevel.EXTERNAL_WRITE,
        summary="Create exact event",
        payload={"title": "Lunch"},
        idempotency_key="thread-1:crashed-task:calendar.create_event",
    )
    registry = ExecutionRegistry(database)
    first = registry.execute_once(
        action,
        lambda: {
            "ok": False,
            "outcome_uncertain": True,
            "error": "verification timed out",
        },
    )
    assert first.status == "uncertain"

    calls = []
    resolved = registry.execute_once(
        action,
        lambda: calls.append("duplicate") or {"ok": True, "event_id": "event-2"},
        recover=lambda _: RecoveryDecision(
            outcome="verified",
            external_resource_id="event-1",
            result={"ok": True, "event_id": "event-1", "reconciled": True},
        ),
    )
    registry.close()

    assert calls == []
    assert resolved.status == "verified"
    assert resolved.external_resource_id == "event-1"
    assert resolved.result["reconciled"] is True


def test_recovery_retries_only_after_explicit_safe_retry_decision(tmp_path) -> None:
    database = str(tmp_path / "jarvis.db")
    action = ProposedAction(
        tool_name="calendar.update_event",
        risk=RiskLevel.EXTERNAL_WRITE,
        summary="Update exact event",
        payload={"event_id": "event-1", "start": "15:00"},
        idempotency_key="thread-1:crashed-task:calendar.update_event",
    )
    registry = ExecutionRegistry(database)
    registry.execute_once(
        action,
        lambda: {"ok": False, "outcome_uncertain": True, "error": "timeout"},
    )

    calls = []
    retried = registry.execute_once(
        action,
        lambda: calls.append("retry") or {"ok": True, "event_id": "event-1"},
        recover=lambda _: RecoveryDecision(outcome="not_applied"),
    )
    registry.close()

    assert calls == ["retry"]
    assert retried.status == "verified"
    assert retried.reconciliation_status == "verified"
    assert retried.attempt_count == 2


def test_conversation_and_pending_interrupt_survive_restart(tmp_path) -> None:
    database = str(tmp_path / "jarvis.db")
    settings = Settings(persistence="sqlite", database_path=database)

    first = Jarvis(settings=settings)
    first.turn("Hello", thread_id="durable-chat")
    pending = first.turn("Email Jisoo and say hello", thread_id="durable-email")
    assert pending.needs_input
    first.close()

    second = Jarvis(settings=settings)
    chat = second.turn("Thanks", thread_id="durable-chat")
    sent = second.turn("send it", thread_id="durable-email")
    assert chat.response == "Of course."
    assert len(second.state(thread_id="durable-chat")["messages"]) == 4
    assert sent.response == "Sent."
    assert len(second.gmail.sent) == 1
    second.close()
