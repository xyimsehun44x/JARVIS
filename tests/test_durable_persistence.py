from __future__ import annotations

from jarvis import Jarvis
from jarvis.config import Settings
from jarvis.core.execution import ExecutionRegistry
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
