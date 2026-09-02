from __future__ import annotations

from datetime import datetime, timezone
import sqlite3
from pathlib import Path

from pydantic import BaseModel


class MemoryEntry(BaseModel):
    key: str
    value: str
    reason: str
    created_at: datetime


class InMemoryLongTermMemory:
    """Explicit, correctable preference store; transient conversation is not saved."""

    def __init__(self) -> None:
        self._entries: dict[str, MemoryEntry] = {}

    def remember(self, key: str, value: str, *, reason: str, explicit: bool = False) -> MemoryEntry:
        if not explicit:
            raise PermissionError("Long-term memory writes require an explicit user request in V0.1")
        entry = MemoryEntry(
            key=key,
            value=value,
            reason=reason,
            created_at=datetime.now(timezone.utc),
        )
        self._entries[key] = entry
        return entry

    def get(self, key: str) -> MemoryEntry | None:
        return self._entries.get(key)

    def forget(self, key: str) -> bool:
        return self._entries.pop(key, None) is not None


class SQLiteLongTermMemory:
    def __init__(self, database_path: str) -> None:
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(database_path, check_same_thread=False)
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS long_term_memory (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self.connection.commit()

    def remember(self, key: str, value: str, *, reason: str, explicit: bool = False) -> MemoryEntry:
        if not explicit:
            raise PermissionError("Long-term memory writes require an explicit user request")
        entry = MemoryEntry(
            key=key,
            value=value,
            reason=reason,
            created_at=datetime.now(timezone.utc),
        )
        self.connection.execute(
            """
            INSERT INTO long_term_memory (key, value, reason, created_at) VALUES (?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, reason=excluded.reason,
                created_at=excluded.created_at
            """,
            (entry.key, entry.value, entry.reason, entry.created_at.isoformat()),
        )
        self.connection.commit()
        return entry

    def get(self, key: str) -> MemoryEntry | None:
        row = self.connection.execute(
            "SELECT key, value, reason, created_at FROM long_term_memory WHERE key = ?", (key,)
        ).fetchone()
        if not row:
            return None
        return MemoryEntry(
            key=row[0], value=row[1], reason=row[2], created_at=datetime.fromisoformat(row[3])
        )

    def forget(self, key: str) -> bool:
        cursor = self.connection.execute("DELETE FROM long_term_memory WHERE key = ?", (key,))
        self.connection.commit()
        return cursor.rowcount > 0

    def close(self) -> None:
        self.connection.close()
