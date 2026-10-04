from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
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
    provider_operation_id: str | None = None
    external_resource_id: str | None = None
    status: Literal["executing", "verified", "failed", "uncertain"]
    reconciliation_status: Literal[
        "not_required", "pending", "verified", "not_applied", "uncertain"
    ] = "not_required"
    first_attempt_at: datetime | None = None
    last_attempt_at: datetime | None = None
    last_reconciled_at: datetime | None = None
    attempt_count: int = 0
    result: dict[str, Any] | None = None


class RecoveryDecision(BaseModel):
    """Result of checking an interrupted operation against its external provider."""

    outcome: Literal["verified", "not_applied", "uncertain"]
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


def provider_operation_id(action: ProposedAction) -> str:
    """Return a stable, provider-safe identifier for one approved payload."""
    return f"jarvis{payload_hash(action)[:32]}"


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
                    provider_operation_id TEXT,
                    external_resource_id TEXT,
                    status TEXT NOT NULL,
                    reconciliation_status TEXT,
                    first_attempt_at TEXT,
                    last_attempt_at TEXT,
                    last_reconciled_at TEXT,
                    attempt_count INTEGER,
                    result_json TEXT
                )
                """
            )
            columns = {
                row[1]
                for row in self._connection.execute(
                    "PRAGMA table_info(execution_records)"
                ).fetchall()
            }
            migrations = {
                "provider_operation_id": "TEXT",
                "reconciliation_status": "TEXT",
                "first_attempt_at": "TEXT",
                "last_attempt_at": "TEXT",
                "last_reconciled_at": "TEXT",
                "attempt_count": "INTEGER",
            }
            for name, sql_type in migrations.items():
                if name not in columns:
                    self._connection.execute(
                        f"ALTER TABLE execution_records ADD COLUMN {name} {sql_type}"
                    )
            self._connection.commit()

    def execute_once(
        self,
        action: ProposedAction,
        operation: Callable[[], dict[str, Any]],
        *,
        recover: Callable[[ExecutionRecord], RecoveryDecision] | None = None,
        operation_id: str | None = None,
    ) -> ExecutionRecord:
        digest = payload_hash(action)
        stable_operation_id = operation_id or provider_operation_id(action)
        with self._lock:
            previous = self._load(digest)
            if previous and previous.status == "verified":
                return previous

            reconciled = False
            if previous and previous.status in {"executing", "uncertain"}:
                if previous.provider_operation_id is None:
                    previous.provider_operation_id = stable_operation_id
                previous.reconciliation_status = "pending"
                previous.last_reconciled_at = self._now()
                self._save(previous)
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
                if decision.outcome == "verified":
                    previous.status = "verified"
                    previous.reconciliation_status = "verified"
                    if decision.external_resource_id:
                        previous.external_resource_id = decision.external_resource_id
                    previous.result = decision.result
                    self._save(previous)
                    return previous
                # Only an explicit provider proof of ``not_applied`` reaches this
                # branch and permits another write attempt.
                record = previous
                record.status = "executing"
                record.reconciliation_status = "not_applied"
                record.result = None
                self._save(record)
                reconciled = True
            elif previous:
                record = previous
                record.status = "executing"
                record.result = None
            else:
                record = ExecutionRecord(
                    execution_id=str(uuid4()),
                    approved_payload_hash=digest,
                    tool_name=action.tool_name,
                    provider_operation_id=stable_operation_id,
                    status="executing",
                )

            if record.provider_operation_id is None:
                record.provider_operation_id = stable_operation_id
            now = self._now()
            record.first_attempt_at = record.first_attempt_at or now
            record.last_attempt_at = now
            record.attempt_count += 1
            self._save(record)

            try:
                result = operation()
                ok = bool(result.get("ok"))
                if ok:
                    record.status = "verified"
                    record.reconciliation_status = (
                        "verified" if reconciled else "not_required"
                    )
                elif result.get("outcome_uncertain"):
                    record.status = "uncertain"
                    record.reconciliation_status = "uncertain"
                else:
                    record.status = "failed"
                    if not reconciled:
                        record.reconciliation_status = "not_required"
                record.external_resource_id = (
                    result.get("message_id") or result.get("draft_id") or result.get("event_id")
                )
                record.result = result
            except Exception as exc:  # providers are an explicit failure boundary
                record.status = "uncertain"
                record.reconciliation_status = "uncertain"
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
        record.reconciliation_status = "uncertain"
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

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        return datetime.fromisoformat(value) if value else None

    def _load(self, digest: str) -> ExecutionRecord | None:
        if digest in self._by_hash:
            return self._by_hash[digest]
        if not self._connection:
            return None
        row = self._connection.execute(
            """
            SELECT execution_id, approved_payload_hash, tool_name, provider_operation_id,
                   external_resource_id, status, reconciliation_status, first_attempt_at,
                   last_attempt_at, last_reconciled_at, attempt_count, result_json
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
            provider_operation_id=row[3],
            external_resource_id=row[4],
            status=row[5],
            reconciliation_status=row[6] or "not_required",
            first_attempt_at=self._parse_datetime(row[7]),
            last_attempt_at=self._parse_datetime(row[8]),
            last_reconciled_at=self._parse_datetime(row[9]),
            attempt_count=row[10] or 0,
            result=json.loads(row[11]) if row[11] else None,
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
                approved_payload_hash, execution_id, tool_name, provider_operation_id,
                external_resource_id, status, reconciliation_status, first_attempt_at,
                last_attempt_at, last_reconciled_at, attempt_count, result_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(approved_payload_hash) DO UPDATE SET
                execution_id=excluded.execution_id,
                tool_name=excluded.tool_name,
                provider_operation_id=excluded.provider_operation_id,
                external_resource_id=excluded.external_resource_id,
                status=excluded.status,
                reconciliation_status=excluded.reconciliation_status,
                first_attempt_at=excluded.first_attempt_at,
                last_attempt_at=excluded.last_attempt_at,
                last_reconciled_at=excluded.last_reconciled_at,
                attempt_count=excluded.attempt_count,
                result_json=excluded.result_json
            """,
            (
                record.approved_payload_hash,
                record.execution_id,
                record.tool_name,
                record.provider_operation_id,
                record.external_resource_id,
                record.status,
                record.reconciliation_status,
                record.first_attempt_at.isoformat() if record.first_attempt_at else None,
                record.last_attempt_at.isoformat() if record.last_attempt_at else None,
                record.last_reconciled_at.isoformat() if record.last_reconciled_at else None,
                record.attempt_count,
                json.dumps(record.result, sort_keys=True) if record.result is not None else None,
            ),
        )
        self._connection.commit()
