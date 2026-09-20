from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from threading import RLock
from collections.abc import Callable
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel

from jarvis.core.permissions import ProposedAction


class ExecutionRecord(BaseModel):
    execution_id: str
    approved_payload_hash: str
    tool_name: str
    external_resource_id: str | None = None
    status: Literal["executing", "verified", "failed", "uncertain"]
    result: dict[str, Any] | None = None


class RecoveryDecision(BaseModel):
    """Result of checking an interrupted operation against its external provider."""

    outcome: Literal["verified", "failed", "retry", "uncertain"]
    external_resource_id: str | None = None
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
        *,
        recover: Callable[[ExecutionRecord], RecoveryDecision] | None = None,
    ) -> ExecutionRecord:
        digest = payload_hash(action)
        with self._lock:
            previous = self._load(digest)
            if previous and previous.status == "verified":
                return previous

            if previous and previous.status in {"executing", "uncertain"}:
                try:
                    decision = recover(previous) if recover else None
                except Exception as exc:
                    decision = RecoveryDecision(
                        outcome="uncertain",
                        external_resource_id=previous.external_resource_id,
                        result={
                            "ok": False,
                            "outcome_uncertain": True,
                            "error": f"External reconciliation failed: {exc}",
                        },
                    )
                if decision is None or decision.outcome == "uncertain":
                    return self._mark_uncertain(previous, decision)
                if decision.outcome != "retry":
                    previous.status = decision.outcome
                    if decision.external_resource_id:
                        previous.external_resource_id = decision.external_resource_id
                    previous.result = decision.result
                    self._save(previous)
                    return previous
                record = previous
                record.status = "executing"
                record.result = None
                self._save(record)
            else:
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
                if ok:
                    record.status = "verified"
                elif result.get("outcome_uncertain"):
                    record.status = "uncertain"
                else:
                    record.status = "failed"
                record.external_resource_id = (
                    result.get("message_id") or result.get("draft_id") or result.get("event_id")
                )
                record.result = result
            except Exception as exc:  # providers are an explicit failure boundary
                record.status = "uncertain"
                record.result = {
                    "ok": False,
                    "outcome_uncertain": True,
                    "error": (
                        f"The {action.tool_name} call ended without a verified outcome: {exc}"
                    ),
                }
            self._save(record)
            return record

    def _mark_uncertain(
        self,
        record: ExecutionRecord,
        decision: RecoveryDecision | None = None,
    ) -> ExecutionRecord:
        record.status = "uncertain"
        if decision and decision.external_resource_id:
            record.external_resource_id = decision.external_resource_id
        if decision and decision.result:
            record.result = decision.result
        elif not record.result:
            record.result = {
                "ok": False,
                "outcome_uncertain": True,
                "error": (
                    "A previous execution stopped before its result was verified. "
                    "Jarvis will not retry it until the external service is reconciled."
                ),
            }
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
