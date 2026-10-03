from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from jarvis.memory.long_term import (
    InMemoryLongTermMemory,
    MemoryConflictError,
    MemoryKind,
    MemorySensitivity,
    MemoryStatus,
    RestrictedMemoryError,
    SQLiteLongTermMemory,
)


def test_memory_changes_require_explicit_intent_and_preserve_history() -> None:
    memory = InMemoryLongTermMemory()
    with pytest.raises(PermissionError):
        memory.remember("meeting preference", "avoid Mondays", reason="casual remark")

    original = memory.remember(
        "meeting preference",
        "avoid Mondays",
        reason="user asked Jarvis to remember it",
        explicit=True,
        kind=MemoryKind.PREFERENCE,
    )
    replacement = memory.correct(
        "meeting preference",
        "prefer Tuesdays",
        reason="user corrected the preference",
        explicit=True,
    )

    assert original.memory_id != replacement.memory_id
    assert replacement.supersedes_id == original.memory_id
    assert memory.get("meeting preference") == replacement
    history = memory.history("meeting preference")
    assert [entry.status for entry in history] == [
        MemoryStatus.SUPERSEDED,
        MemoryStatus.ACTIVE,
    ]
    assert history[0].superseded_by_id == replacement.memory_id

    with pytest.raises(PermissionError):
        memory.forget("meeting preference")
    assert memory.forget("meeting preference", explicit=True)
    assert memory.get("meeting preference") is None
    assert memory.history("meeting preference")[-1].status is MemoryStatus.FORGOTTEN

    restored = memory.restore(replacement.memory_id, explicit=True)
    assert restored.status is MemoryStatus.ACTIVE
    assert memory.get("meeting preference") == restored


def test_remember_is_idempotent_but_never_silently_overwrites() -> None:
    memory = InMemoryLongTermMemory()
    first = memory.remember(
        "timezone",
        "Asia/Seoul",
        reason="explicit request",
        explicit=True,
        kind=MemoryKind.IDENTITY,
    )
    duplicate = memory.remember(
        "timezone",
        "Asia/Seoul",
        reason="repeated explicit request",
        explicit=True,
        kind=MemoryKind.IDENTITY,
    )

    assert duplicate.memory_id == first.memory_id
    with pytest.raises(MemoryConflictError):
        memory.remember(
            "timezone",
            "Europe/London",
            reason="conflicting request",
            explicit=True,
        )
    with pytest.raises(RestrictedMemoryError):
        memory.remember(
            "api key",
            "not-a-real-secret",
            reason="explicit request",
            explicit=True,
        )


def test_recall_is_relevant_and_excludes_sensitive_memory_by_default() -> None:
    memory = InMemoryLongTermMemory()
    preference = memory.remember(
        "meeting preference",
        "morning meetings",
        reason="explicit request",
        explicit=True,
        kind=MemoryKind.PREFERENCE,
    )
    sensitive = memory.remember(
        "home address",
        "Example sensitive address",
        reason="explicit request",
        explicit=True,
        kind=MemoryKind.FACT,
        sensitivity=MemorySensitivity.SENSITIVE,
    )

    assert memory.recall("When should we schedule a meeting?") == [preference]
    assert memory.recall("What is my home address?") == []
    assert memory.recall("What is my home address?", include_sensitive=True) == [sensitive]
    assert memory.recall("unrelated subject") == []


def test_sqlite_migrates_legacy_rows_and_preserves_lifecycle_across_restart(tmp_path) -> None:
    database = tmp_path / "jarvis.db"
    connection = sqlite3.connect(database)
    created_at = datetime(2026, 1, 2, tzinfo=timezone.utc).isoformat()
    connection.execute(
        """
        CREATE TABLE long_term_memory (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "INSERT INTO long_term_memory VALUES (?, ?, ?, ?)",
        ("meeting_preference", "avoid Mondays", "legacy explicit request", created_at),
    )
    connection.commit()
    connection.close()

    first = SQLiteLongTermMemory(str(database))
    migrated = first.get("meeting preference")
    assert migrated is not None
    assert migrated.value == "avoid Mondays"
    assert migrated.provenance == "legacy_v1_migration"
    corrected = first.correct(
        "meeting preference",
        "prefer Tuesdays",
        reason="explicit correction",
        explicit=True,
        kind=MemoryKind.PREFERENCE,
    )
    first.close()

    second = SQLiteLongTermMemory(str(database))
    assert second.get("meeting preference") == corrected
    history = second.history("meeting preference")
    assert len(history) == 2
    assert history[0].status is MemoryStatus.SUPERSEDED
    assert history[1].status is MemoryStatus.ACTIVE
    assert second.forget("meeting preference", explicit=True)
    forgotten_id = history[1].memory_id
    second.close()

    third = SQLiteLongTermMemory(str(database))
    assert third.get("meeting preference") is None
    forgotten = third.get_by_id(forgotten_id)
    assert forgotten is not None
    assert forgotten.status is MemoryStatus.FORGOTTEN
    third.close()


def test_credential_like_legacy_memory_cannot_be_restored(tmp_path) -> None:
    database = tmp_path / "legacy-secret.db"
    connection = sqlite3.connect(database)
    created_at = datetime(2026, 1, 2, tzinfo=timezone.utc).isoformat()
    connection.execute(
        """
        CREATE TABLE long_term_memory (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "INSERT INTO long_term_memory VALUES (?, ?, ?, ?)",
        ("api_key", "legacy-value", "legacy explicit request", created_at),
    )
    connection.commit()
    connection.close()

    memory = SQLiteLongTermMemory(str(database))
    try:
        records = memory.list_all(include_sensitive=True)
        assert len(records) == 1
        assert records[0].sensitivity is MemorySensitivity.SENSITIVE
        assert memory.forget("api key", explicit=True)
        with pytest.raises(RestrictedMemoryError):
            memory.restore(records[0].memory_id, explicit=True)
    finally:
        memory.close()
