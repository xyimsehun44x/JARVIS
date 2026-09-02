from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from threading import RLock
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from jarvis.core.permissions import ProposedAction


class ExecutionRecord(BaseModel):
    execution_id: str
    approved_payload_hash: str
    tool_name: str
    external_resource_id: str | None = None
    status: str
    result: dict[str, Any] | None = None


def payload_hash(action: ProposedAction) -> str:
    canonical = json.dumps(
        {
            "action": action.model_dump(mode="json"),
            "idempotency_key": action.idempotency_key,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ExecutionRegistry:
    """Idempotency registry, durable when a SQLite database path is supplied."""

    def __init__(self, database_path: str | None = None) -> None:
        self._by_hash: dict[str, ExecutionRecord] = {}
        self._lock = RLock()
        self._connection: sqlite3.Connection | None = None
        if database_path:
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(database_path, check_same_thread=False)
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS execution_records (
                    approved_payload_hash TEXT PRIMARY KEY,
                    execution_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    external_resource_id TEXT,
                    status TEXT NOT NULL,
                    result_json TEXT
                )
                """
            )
            self._connection.commit()

    def execute_once(
        self,
        action: ProposedAction,
        operation: Callable[[], dict[str, Any]],
    ) -> ExecutionRecord:
        digest = payload_hash(action)
        with self._lock:
            previous = self._load(digest)
            if previous and previous.status == "verified":
                return previous

            record = ExecutionRecord(
                execution_id=str(uuid4()),
                approved_payload_hash=digest,
                tool_name=action.tool_name,
                status="executing",
            )
            self._save(record)
            try:
                result = operation()
                ok = bool(result.get("ok"))
                record.status = "verified" if ok else "failed"
                record.external_resource_id = (
                    result.get("message_id") or result.get("draft_id") or result.get("event_id")
                )
                record.result = result
            except Exception as exc:  # providers are an explicit failure boundary
                record.status = "failed"
                record.result = {"ok": False, "error": str(exc)}
            self._save(record)
            return record

    def close(self) -> None:
        if self._connection:
            self._connection.close()
            self._connection = None

    def _load(self, digest: str) -> ExecutionRecord | None:
        if digest in self._by_hash:
            return self._by_hash[digest]
        if not self._connection:
            return None
        row = self._connection.execute(
            """
            SELECT execution_id, approved_payload_hash, tool_name, external_resource_id,
                   status, result_json
            FROM execution_records WHERE approved_payload_hash = ?
            """,
            (digest,),
        ).fetchone()
        if not row:
            return None
        record = ExecutionRecord(
            execution_id=row[0],
            approved_payload_hash=row[1],
            tool_name=row[2],
            external_resource_id=row[3],
            status=row[4],
            result=json.loads(row[5]) if row[5] else None,
        )
        self._by_hash[digest] = record
        return record

    def _save(self, record: ExecutionRecord) -> None:
        self._by_hash[record.approved_payload_hash] = record
        if not self._connection:
            return
        self._connection.execute(
            """
            INSERT INTO execution_records (
                approved_payload_hash, execution_id, tool_name, external_resource_id,
                status, result_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(approved_payload_hash) DO UPDATE SET
                execution_id=excluded.execution_id,
                tool_name=excluded.tool_name,
                external_resource_id=excluded.external_resource_id,
                status=excluded.status,
                result_json=excluded.result_json
            """,
            (
                record.approved_payload_hash,
                record.execution_id,
                record.tool_name,
                record.external_resource_id,
                record.status,
                json.dumps(record.result, sort_keys=True) if record.result is not None else None,
            ),
        )
        self._connection.commit()
