from __future__ import annotations

from jarvis import Jarvis
from jarvis.config import Settings
from jarvis.core.router import Route
from jarvis.core.session import ExecutionTier, SessionCoordinator
from jarvis.memory.intents import MemoryOperation, parse_memory_command
from jarvis.memory.long_term import MemorySensitivity, MemoryStatus
from jarvis.voice.normalizer import NormalizedUtterance


class RecordingModel:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[dict[str, str]]]] = []

    def respond(self, text, history):
        self.calls.append((text, list(history)))
        return "Understood."


def test_memory_command_parser_is_explicit_and_conservative() -> None:
    remember = parse_memory_command("Remember that my meeting preference is avoid Mondays.")
    assert remember is not None
    assert remember.operation is MemoryOperation.REMEMBER
    assert remember.key == "meeting_preference"
    assert remember.value == "avoid Mondays"
    invoked = parse_memory_command(
        "Jarvis, remember that my meeting preference is avoid Mondays."
    )
    assert invoked is not None
    assert invoked.operation is MemoryOperation.REMEMBER
    assert invoked.key == "meeting_preference"

    correction = parse_memory_command(
        "Correct what you remember about my meeting preference to prefer Tuesdays."
    )
    assert correction is not None
    assert correction.operation is MemoryOperation.CORRECT
    assert correction.key == "meeting_preference"

    assert parse_memory_command("I hate Monday meetings") is None
    assert parse_memory_command("Remember to email David") is None
    assert parse_memory_command("forget it") is None


def test_explicit_memory_lifecycle_uses_tier_two_without_calling_the_model() -> None:
    model = RecordingModel()
    jarvis = Jarvis(settings=Settings(), conversation_model=model)
    coordinator = SessionCoordinator(jarvis)

    remembered = coordinator.turn(
        "Jarvis, remember that my meeting preference is avoid Mondays.",
        thread_id="memory",
    )
    recalled = coordinator.turn(
        "What do you remember about my meeting preference?", thread_id="memory"
    )
    corrected = coordinator.turn(
        "Correct what you remember about my meeting preference to prefer Tuesdays.",
        thread_id="memory",
    )
    forgotten = coordinator.turn(
        "Forget what you remember about my meeting preference.", thread_id="memory"
    )
    missing = coordinator.turn(
        "What do you remember about my meeting preference?", thread_id="memory"
    )

    assert remembered.execution_tier == ExecutionTier.AGENT
    assert remembered.tier_reason == "explicit specialist domain"
    assert "avoid Mondays" in (remembered.response or "")
    assert "avoid Mondays" in (recalled.response or "")
    assert "prefer Tuesdays" in (corrected.response or "")
    assert "forgotten" in (forgotten.response or "")
    assert "don't have" in (missing.response or "")
    assert model.calls == []
    history = jarvis.memory.history("meeting preference")
    assert [entry.status for entry in history] == [
        MemoryStatus.SUPERSEDED,
        MemoryStatus.FORGOTTEN,
    ]
    jarvis.close()


def test_casual_remarks_never_create_memory() -> None:
    model = RecordingModel()
    jarvis = Jarvis(settings=Settings(), conversation_model=model)
    coordinator = SessionCoordinator(jarvis)

    result = coordinator.turn("I hate Monday meetings", thread_id="casual")

    assert result.execution_tier == ExecutionTier.CONVERSATION
    assert jarvis.memory.list_active(include_sensitive=True) == []
    assert len(model.calls) == 1
    jarvis.close()


def test_relevant_memory_is_injected_as_untrusted_context_but_not_persisted() -> None:
    model = RecordingModel()
    jarvis = Jarvis(settings=Settings(), conversation_model=model)
    jarvis.memory.remember(
        "meeting preference",
        "morning meetings",
        reason="explicit test request",
        explicit=True,
    )

    result = jarvis.direct_conversation(
        "When should we schedule a meeting?", thread_id="context"
    )

    _, model_history = model.calls[0]
    assert len(model_history) == 1
    assert model_history[0]["role"] == "system"
    assert "untrusted data" in model_history[0]["content"]
    assert "morning meetings" in model_history[0]["content"]
    persisted = result.state["messages"]
    assert [message["role"] for message in persisted] == ["user", "assistant"]
    assert all("morning meetings" not in message["content"] for message in persisted)
    jarvis.close()


def test_sensitive_memory_is_recalled_explicitly_but_never_auto_injected() -> None:
    model = RecordingModel()
    jarvis = Jarvis(settings=Settings(), conversation_model=model)
    coordinator = SessionCoordinator(jarvis)

    saved = coordinator.turn(
        "Remember that my home address is Example sensitive address.",
        thread_id="sensitive",
    )
    jarvis.direct_conversation("Where should I order dinner?", thread_id="sensitive")
    recalled = coordinator.turn(
        "What do you remember about my home address?", thread_id="sensitive"
    )
    jarvis.direct_conversation("Help with my home address", thread_id="sensitive")

    entry = jarvis.memory.get("home address")
    assert entry is not None
    assert entry.sensitivity is MemorySensitivity.SENSITIVE
    assert "marked it sensitive" in (saved.response or "")
    assert "Example sensitive address" in (recalled.response or "")
    assert all(
        "Example sensitive address" not in message["content"]
        for _, history in model.calls
        for message in history
    )
    assert all(
        "home address" not in message["content"].lower()
        for _, history in model.calls
        for message in history
    )
    jarvis.close()


def test_legacy_sensitive_transcript_is_scrubbed_before_model_use() -> None:
    jarvis = Jarvis(settings=Settings(), conversation_model=RecordingModel())
    jarvis.memory.remember(
        "home address",
        "Example sensitive address",
        reason="explicit test request",
        explicit=True,
        sensitivity=MemorySensitivity.SENSITIVE,
    )
    history = [
        {
            "role": "user",
            "content": "Remember that my home address is Example sensitive address.",
        },
        {
            "role": "assistant",
            "content": "I remember home address: Example sensitive address.",
        },
    ]

    safe_history = jarvis._conversation_history_with_memories(
        "Where should I order dinner?", history
    )

    assert all(
        "Example sensitive address" not in message["content"]
        for message in safe_history
    )
    assert all("home address" not in message["content"].lower() for message in safe_history)
    jarvis.close()


def test_secret_memory_is_refused_and_low_confidence_voice_must_repeat() -> None:
    jarvis = Jarvis(settings=Settings())
    coordinator = SessionCoordinator(jarvis)

    refused = coordinator.turn(
        "Remember that my API key is definitely-not-a-real-key", thread_id="secret"
    )
    low_confidence = coordinator.turn(
        NormalizedUtterance(
            raw_transcript="Remember that my timezone is Asia/Seoul",
            normalized_text="Remember that my timezone is Asia/Seoul",
            stt_confidence=0.1,
        ),
        thread_id="voice-memory",
    )

    assert "won't store" in (refused.response or "")
    assert jarvis.memory.list_active(include_sensitive=True) == []
    assert all(
        "definitely-not-a-real-key" not in message["content"]
        for message in refused.state["messages"]
    )
    assert low_confidence.needs_input is True
    assert low_confidence.interrupt_kind == "speech_clarification"
    assert jarvis.select_route("Remember that my timezone is Asia/Seoul") is Route.MEMORY
    jarvis.close()


def test_memory_workflow_persists_across_jarvis_restarts(tmp_path) -> None:
    database = tmp_path / "persistent-memory.db"
    settings = Settings(persistence="sqlite", database_path=str(database))

    first = Jarvis(settings=settings, conversation_model=RecordingModel())
    first_result = SessionCoordinator(first).turn(
        "Remember that my timezone is Asia/Seoul", thread_id="memory-first"
    )
    assert "Asia/Seoul" in (first_result.response or "")
    first.close()

    second = Jarvis(settings=settings, conversation_model=RecordingModel())
    second_result = SessionCoordinator(second).turn(
        "What do you remember about my timezone?", thread_id="memory-second"
    )
    assert "Asia/Seoul" in (second_result.response or "")
    second.close()


def test_explicit_memory_request_safely_abandons_an_unrelated_pending_task() -> None:
    jarvis = Jarvis(settings=Settings(), conversation_model=RecordingModel())
    coordinator = SessionCoordinator(jarvis)

    pending = coordinator.turn("Email Nobody and say hello", thread_id="memory-switch")
    remembered = coordinator.turn(
        "Remember that my timezone is Asia/Seoul", thread_id="memory-switch"
    )

    assert pending.needs_input is True
    assert remembered.execution_tier == ExecutionTier.AGENT
    assert "Asia/Seoul" in (remembered.response or "")
    assert jarvis.gmail.sent == []
    assert not jarvis.has_pending_input("memory-switch")
    jarvis.close()
