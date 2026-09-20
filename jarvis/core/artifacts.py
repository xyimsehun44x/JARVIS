from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from threading import RLock
from typing import Any, Callable


class TaskArtifactStore:
    """Idempotent task artifacts that survive LangGraph interrupt replays."""

    def __init__(self, database_path: str | None = None) -> None:
        self._items: dict[tuple[str, str], dict[str, Any]] = {}
        self._lock = RLock()
        self._connection: sqlite3.Connection | None = None
        if database_path:
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(database_path, check_same_thread=False)
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS task_artifacts (
                    task_id TEXT NOT NULL,
                    artifact_key TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    PRIMARY KEY (task_id, artifact_key)
                )
                """
            )
            self._connection.commit()

    def get(self, task_id: str, artifact_key: str) -> dict[str, Any] | None:
        cache_key = (task_id, artifact_key)
        with self._lock:
            if cache_key in self._items:
                return dict(self._items[cache_key])
            if not self._connection:
                return None
            row = self._connection.execute(
                "SELECT value_json FROM task_artifacts WHERE task_id = ? AND artifact_key = ?",
                cache_key,
            ).fetchone()
            if not row:
                return None
            value = json.loads(row[0])
            self._items[cache_key] = value
            return dict(value)

    def get_or_create(
        self,
        task_id: str,
        artifact_key: str,
        factory: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        with self._lock:
            existing = self.get(task_id, artifact_key)
            if existing is not None:
                return existing
            value = factory()
            self._items[(task_id, artifact_key)] = dict(value)
            if self._connection:
                self._connection.execute(
                    """
                    INSERT OR IGNORE INTO task_artifacts (task_id, artifact_key, value_json)
                    VALUES (?, ?, ?)
                    """,
                    (task_id, artifact_key, json.dumps(value, sort_keys=True)),
                )
                self._connection.commit()
                stored = self.get(task_id, artifact_key)
                if stored is not None:
                    return stored
            return dict(value)

    def close(self) -> None:
        if self._connection:
            self._connection.close()
            self._connection = None
