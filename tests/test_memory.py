import pytest

from jarvis.memory.long_term import InMemoryLongTermMemory


def test_long_term_memory_requires_explicit_intent_and_is_correctable() -> None:
    memory = InMemoryLongTermMemory()
    with pytest.raises(PermissionError):
        memory.remember("meeting_preference", "avoid Mondays", reason="casual remark")

    entry = memory.remember(
        "meeting_preference",
        "avoid Mondays",
        reason="user asked Jarvis to remember it",
        explicit=True,
    )
    assert memory.get("meeting_preference") == entry
    assert memory.forget("meeting_preference")
    assert memory.get("meeting_preference") is None

