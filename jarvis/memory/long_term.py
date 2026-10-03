from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from threading import RLock
from typing import Literal
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import BaseModel, Field


MEMORY_SCHEMA_VERSION = 2
_RECALL_STOPWORDS = {
    "a",
    "about",
    "am",
    "an",
    "are",
    "do",
    "for",
    "have",
    "i",
    "in",
    "is",
    "it",
    "me",
    "my",
    "of",
    "on",
    "should",
    "the",
    "to",
    "we",
    "what",
    "when",
    "where",
    "you",
}
_RESTRICTED_KEY = re.compile(
    r"(?:^|_)(?:password|passcode|api_key|secret_key|access_token|refresh_token|"
    r"private_key|credit_card|card_number|cvv|ssn)(?:_|$)",
    re.I,
)
_SECRET_VALUE = re.compile(
    r"(?:-----BEGIN [A-Z ]*PRIVATE KEY-----|AIza[0-9A-Za-z_-]{20,}|"
    r"sk-[0-9A-Za-z_-]{20,}|gh[pousr]_[0-9A-Za-z]{20,})"
)
_RESTRICTED_TEXT = re.compile(
    r"\b(?:password|passcode|api\s*key|secret\s*key|access\s*token|refresh\s*token|"
    r"private\s*key|credit\s*card|card\s*number|cvv|social\s*security|ssn)\b",
    re.I,
)
_LEGACY_SENSITIVE = re.compile(
    r"\b(?:password|passcode|key|secret|token|address|phone|birthday|medical|health|"
    r"diagnosis|medication|credit|card|ssn)\b",
    re.I,
)


class MemoryKind(str, Enum):
    PREFERENCE = "preference"
    FACT = "fact"
    IDENTITY = "identity"
    INSTRUCTION = "instruction"
    OTHER = "other"


class MemorySensitivity(str, Enum):
    NORMAL = "normal"
    SENSITIVE = "sensitive"


class MemoryStatus(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    FORGOTTEN = "forgotten"


class MemoryConflictError(ValueError):
    """Raised when a remember operation would silently overwrite active memory."""


class RestrictedMemoryError(ValueError):
    """Raised when credential-like material is offered to long-term memory."""


class MemoryEntry(BaseModel):
    schema_version: Literal[2] = MEMORY_SCHEMA_VERSION
    memory_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    key: str = Field(min_length=1, max_length=160)
    value: str = Field(min_length=1, max_length=4000)
    kind: MemoryKind = MemoryKind.OTHER
    reason: str = Field(min_length=1, max_length=500)
    provenance: str = Field(default="explicit_user_request", min_length=1, max_length=160)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    sensitivity: MemorySensitivity = MemorySensitivity.NORMAL
    status: MemoryStatus = MemoryStatus.ACTIVE
    created_at: datetime
    updated_at: datetime
    supersedes_id: str | None = None
    superseded_by_id: str | None = None
    status_reason: str | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_key(key: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.strip().lower()).strip("_")
    if not normalized:
        raise ValueError("Memory key must contain a letter or number")
    return normalized[:160]


def _require_explicit(explicit: bool) -> None:
    if not explicit:
        raise PermissionError("Long-term memory changes require an explicit user request")


def is_restricted_memory_material(key: str, value: str) -> bool:
    """Return whether a record is unsafe to store or expose in memory controls."""
    return bool(
        _RESTRICTED_KEY.search(_normalize_key(key))
        or _RESTRICTED_TEXT.search(f"{key.replace('_', ' ')} {value}")
        or _SECRET_VALUE.search(value)
    )


def _reject_restricted_material(key: str, value: str) -> None:
    if is_restricted_memory_material(key, value):
        raise RestrictedMemoryError(
            "Credentials, payment-card data, and private keys cannot be stored in memory"
        )


def _new_entry(
    key: str,
    value: str,
    *,
    reason: str,
    kind: MemoryKind,
    provenance: str,
    confidence: float,
    sensitivity: MemorySensitivity,
    supersedes_id: str | None = None,
) -> MemoryEntry:
    _reject_restricted_material(key, value)
    timestamp = _now()
    return MemoryEntry(
        key=_normalize_key(key),
        value=value.strip(),
        kind=kind,
        reason=reason.strip(),
        provenance=provenance.strip(),
        confidence=confidence,
        sensitivity=sensitivity,
        created_at=timestamp,
        updated_at=timestamp,
        supersedes_id=supersedes_id,
    )


def _relevance_score(entry: MemoryEntry, query: str) -> int:
    query_text = query.lower().strip()
    query_tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", query_text)
        if token not in _RECALL_STOPWORDS
    }
    if not query_tokens:
        return 0
    key_text = entry.key.replace("_", " ")
    key_tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", key_text)
        if token not in _RECALL_STOPWORDS
    }
    value_tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", entry.value.lower())
        if token not in _RECALL_STOPWORDS
    }
    score = 3 * len(query_tokens & key_tokens) + len(query_tokens & value_tokens)
    if key_text in query_text or query_text in key_text:
        score += 4
    return score


def _rank_relevant(
    entries: list[MemoryEntry],
    query: str,
    *,
    limit: int,
) -> list[MemoryEntry]:
    if limit < 1:
        return []
    ranked = [(_relevance_score(entry, query), entry) for entry in entries]
    relevant = [(score, entry) for score, entry in ranked if score > 0]
    relevant.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
    return [entry for _, entry in relevant[:limit]]


class InMemoryLongTermMemory:
    """Explicit, versioned memory store with recoverable history."""

    def __init__(self) -> None:
        self._entries: dict[str, MemoryEntry] = {}
        self._lock = RLock()

    def remember(
        self,
        key: str,
        value: str,
        *,
        reason: str,
        explicit: bool = False,
        kind: MemoryKind = MemoryKind.OTHER,
        provenance: str = "explicit_user_request",
        confidence: float = 1.0,
        sensitivity: MemorySensitivity = MemorySensitivity.NORMAL,
    ) -> MemoryEntry:
        _require_explicit(explicit)
        normalized_key = _normalize_key(key)
        with self._lock:
            existing = self.get(normalized_key)
            if existing is not None:
                if existing.value == value.strip():
                    return existing
                raise MemoryConflictError(
                    f"Active memory already exists for {normalized_key}; use correct()"
                )
            entry = _new_entry(
                normalized_key,
                value,
                reason=reason,
                kind=kind,
                provenance=provenance,
                confidence=confidence,
                sensitivity=sensitivity,
            )
            self._entries[entry.memory_id] = entry
            return entry.model_copy(deep=True)

    def get(self, key: str) -> MemoryEntry | None:
        normalized_key = _normalize_key(key)
        with self._lock:
            for entry in self._entries.values():
                if entry.key == normalized_key and entry.status is MemoryStatus.ACTIVE:
                    return entry.model_copy(deep=True)
        return None

    def get_by_id(self, memory_id: str) -> MemoryEntry | None:
        with self._lock:
            entry = self._entries.get(memory_id)
            return entry.model_copy(deep=True) if entry else None

    def correct(
        self,
        key: str,
        value: str,
        *,
        reason: str,
        explicit: bool = False,
        kind: MemoryKind | None = None,
        provenance: str = "explicit_user_correction",
        confidence: float = 1.0,
        sensitivity: MemorySensitivity | None = None,
    ) -> MemoryEntry:
        _require_explicit(explicit)
        normalized_key = _normalize_key(key)
        with self._lock:
            existing = self.get(normalized_key)
            if existing is None:
                raise KeyError(normalized_key)
            if existing.value == value.strip():
                return existing
            replacement = _new_entry(
                normalized_key,
                value,
                reason=reason,
                kind=kind or existing.kind,
                provenance=provenance,
                confidence=confidence,
                sensitivity=sensitivity or existing.sensitivity,
                supersedes_id=existing.memory_id,
            )
            timestamp = replacement.created_at
            self._entries[existing.memory_id] = existing.model_copy(
                update={
                    "status": MemoryStatus.SUPERSEDED,
                    "updated_at": timestamp,
                    "superseded_by_id": replacement.memory_id,
                    "status_reason": reason.strip(),
                }
            )
            self._entries[replacement.memory_id] = replacement
            return replacement.model_copy(deep=True)

    def forget(
        self,
        key: str,
        *,
        reason: str = "explicit user request",
        explicit: bool = False,
    ) -> bool:
        _require_explicit(explicit)
        normalized_key = _normalize_key(key)
        with self._lock:
            existing = self.get(normalized_key)
            if existing is None:
                return False
            self._entries[existing.memory_id] = existing.model_copy(
                update={
                    "status": MemoryStatus.FORGOTTEN,
                    "updated_at": _now(),
                    "status_reason": reason.strip(),
                }
            )
            return True

    def restore(
        self,
        memory_id: str,
        *,
        reason: str = "explicit user request",
        explicit: bool = False,
    ) -> MemoryEntry:
        _require_explicit(explicit)
        with self._lock:
            entry = self._entries.get(memory_id)
            if entry is None or entry.status is not MemoryStatus.FORGOTTEN:
                raise KeyError(memory_id)
            _reject_restricted_material(entry.key, entry.value)
            if self.get(entry.key) is not None:
                raise MemoryConflictError(f"Active memory already exists for {entry.key}")
            restored = entry.model_copy(
                update={
                    "status": MemoryStatus.ACTIVE,
                    "updated_at": _now(),
                    "status_reason": reason.strip(),
                }
            )
            self._entries[memory_id] = restored
            return restored.model_copy(deep=True)

    def history(self, key: str) -> list[MemoryEntry]:
        normalized_key = _normalize_key(key)
        with self._lock:
            entries = [
                entry.model_copy(deep=True)
                for entry in self._entries.values()
                if entry.key == normalized_key
            ]
        return sorted(entries, key=lambda entry: entry.created_at)

    def list_active(self, *, include_sensitive: bool = False) -> list[MemoryEntry]:
        with self._lock:
            entries = [
                entry.model_copy(deep=True)
                for entry in self._entries.values()
                if entry.status is MemoryStatus.ACTIVE
                and (include_sensitive or entry.sensitivity is MemorySensitivity.NORMAL)
            ]
        return sorted(entries, key=lambda entry: entry.updated_at, reverse=True)

    def list_all(self, *, include_sensitive: bool = False) -> list[MemoryEntry]:
        with self._lock:
            entries = [
                entry.model_copy(deep=True)
                for entry in self._entries.values()
                if include_sensitive
                or entry.sensitivity is MemorySensitivity.NORMAL
            ]
        return sorted(entries, key=lambda entry: entry.updated_at, reverse=True)

    def recall(
        self,
        query: str,
        *,
        limit: int = 5,
        include_sensitive: bool = False,
    ) -> list[MemoryEntry]:
        return _rank_relevant(
            self.list_active(include_sensitive=include_sensitive), query, limit=limit
        )


class SQLiteLongTermMemory:
    _COLUMNS = (
        "memory_id, schema_version, key, value, kind, reason, provenance, confidence, "
        "sensitivity, status, created_at, updated_at, supersedes_id, superseded_by_id, "
        "status_reason"
    )

    def __init__(self, database_path: str) -> None:
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(database_path, check_same_thread=False)
        self._lock = RLock()
        with self._lock:
            self._create_schema()
            self._migrate_legacy_rows()

    def _create_schema(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS long_term_memory_records (
                memory_id TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                kind TEXT NOT NULL,
                reason TEXT NOT NULL,
                provenance TEXT NOT NULL,
                confidence REAL NOT NULL,
                sensitivity TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                supersedes_id TEXT,
                superseded_by_id TEXT,
                status_reason TEXT
            )
            """
        )
        self.connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS ux_long_term_memory_active_key
            ON long_term_memory_records(key) WHERE status = 'active'
            """
        )
        self.connection.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_long_term_memory_key_history
            ON long_term_memory_records(key, created_at)
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS long_term_memory_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        self.connection.commit()

    def _migrate_legacy_rows(self) -> None:
        migrated = self.connection.execute(
            "SELECT value FROM long_term_memory_meta WHERE key = 'legacy_v1_migrated'"
        ).fetchone()
        if migrated:
            return
        legacy_exists = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'long_term_memory'"
        ).fetchone()
        if legacy_exists:
            rows = self.connection.execute(
                "SELECT key, value, reason, created_at FROM long_term_memory"
            ).fetchall()
            for key, value, reason, created_at in rows:
                normalized_key = _normalize_key(key)
                legacy_id = str(uuid5(NAMESPACE_URL, f"jarvis:legacy-memory:{normalized_key}"))
                legacy_sensitivity = (
                    MemorySensitivity.SENSITIVE
                    if _LEGACY_SENSITIVE.search(
                        f"{normalized_key.replace('_', ' ')} {value}"
                    )
                    or _SECRET_VALUE.search(value)
                    else MemorySensitivity.NORMAL
                )
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO long_term_memory_records (
                        memory_id, schema_version, key, value, kind, reason, provenance,
                        confidence, sensitivity, status, created_at, updated_at,
                        supersedes_id, superseded_by_id, status_reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)
                    """,
                    (
                        legacy_id,
                        MEMORY_SCHEMA_VERSION,
                        normalized_key,
                        value,
                        MemoryKind.OTHER.value,
                        reason,
                        "legacy_v1_migration",
                        1.0,
                        legacy_sensitivity.value,
                        MemoryStatus.ACTIVE.value,
                        created_at,
                        created_at,
                    ),
                )
        self.connection.execute(
            "INSERT OR REPLACE INTO long_term_memory_meta (key, value) VALUES (?, ?)",
            ("legacy_v1_migrated", datetime.now(timezone.utc).isoformat()),
        )
        self.connection.commit()

    @classmethod
    def _entry_from_row(cls, row: tuple[object, ...]) -> MemoryEntry:
        return MemoryEntry(
            memory_id=str(row[0]),
            schema_version=int(row[1]),
            key=str(row[2]),
            value=str(row[3]),
            kind=MemoryKind(str(row[4])),
            reason=str(row[5]),
            provenance=str(row[6]),
            confidence=float(row[7]),
            sensitivity=MemorySensitivity(str(row[8])),
            status=MemoryStatus(str(row[9])),
            created_at=datetime.fromisoformat(str(row[10])),
            updated_at=datetime.fromisoformat(str(row[11])),
            supersedes_id=str(row[12]) if row[12] is not None else None,
            superseded_by_id=str(row[13]) if row[13] is not None else None,
            status_reason=str(row[14]) if row[14] is not None else None,
        )

    def _insert(self, entry: MemoryEntry) -> None:
        self.connection.execute(
            """
            INSERT INTO long_term_memory_records (
                memory_id, schema_version, key, value, kind, reason, provenance,
                confidence, sensitivity, status, created_at, updated_at,
                supersedes_id, superseded_by_id, status_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.memory_id,
                entry.schema_version,
                entry.key,
                entry.value,
                entry.kind.value,
                entry.reason,
                entry.provenance,
                entry.confidence,
                entry.sensitivity.value,
                entry.status.value,
                entry.created_at.isoformat(),
                entry.updated_at.isoformat(),
                entry.supersedes_id,
                entry.superseded_by_id,
                entry.status_reason,
            ),
        )

    def remember(
        self,
        key: str,
        value: str,
        *,
        reason: str,
        explicit: bool = False,
        kind: MemoryKind = MemoryKind.OTHER,
        provenance: str = "explicit_user_request",
        confidence: float = 1.0,
        sensitivity: MemorySensitivity = MemorySensitivity.NORMAL,
    ) -> MemoryEntry:
        _require_explicit(explicit)
        normalized_key = _normalize_key(key)
        with self._lock:
            existing = self.get(normalized_key)
            if existing is not None:
                if existing.value == value.strip():
                    return existing
                raise MemoryConflictError(
                    f"Active memory already exists for {normalized_key}; use correct()"
                )
            entry = _new_entry(
                normalized_key,
                value,
                reason=reason,
                kind=kind,
                provenance=provenance,
                confidence=confidence,
                sensitivity=sensitivity,
            )
            self._insert(entry)
            self.connection.commit()
            return entry

    def get(self, key: str) -> MemoryEntry | None:
        normalized_key = _normalize_key(key)
        with self._lock:
            row = self.connection.execute(
                f"SELECT {self._COLUMNS} FROM long_term_memory_records "
                "WHERE key = ? AND status = 'active'",
                (normalized_key,),
            ).fetchone()
        return self._entry_from_row(row) if row else None

    def get_by_id(self, memory_id: str) -> MemoryEntry | None:
        with self._lock:
            row = self.connection.execute(
                f"SELECT {self._COLUMNS} FROM long_term_memory_records WHERE memory_id = ?",
                (memory_id,),
            ).fetchone()
        return self._entry_from_row(row) if row else None

    def correct(
        self,
        key: str,
        value: str,
        *,
        reason: str,
        explicit: bool = False,
        kind: MemoryKind | None = None,
        provenance: str = "explicit_user_correction",
        confidence: float = 1.0,
        sensitivity: MemorySensitivity | None = None,
    ) -> MemoryEntry:
        _require_explicit(explicit)
        normalized_key = _normalize_key(key)
        with self._lock:
            existing = self.get(normalized_key)
            if existing is None:
                raise KeyError(normalized_key)
            if existing.value == value.strip():
                return existing
            replacement = _new_entry(
                normalized_key,
                value,
                reason=reason,
                kind=kind or existing.kind,
                provenance=provenance,
                confidence=confidence,
                sensitivity=sensitivity or existing.sensitivity,
                supersedes_id=existing.memory_id,
            )
            try:
                self.connection.execute(
                    """
                    UPDATE long_term_memory_records
                    SET status = ?, updated_at = ?, superseded_by_id = ?, status_reason = ?
                    WHERE memory_id = ? AND status = 'active'
                    """,
                    (
                        MemoryStatus.SUPERSEDED.value,
                        replacement.created_at.isoformat(),
                        replacement.memory_id,
                        reason.strip(),
                        existing.memory_id,
                    ),
                )
                self._insert(replacement)
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                raise
            return replacement

    def forget(
        self,
        key: str,
        *,
        reason: str = "explicit user request",
        explicit: bool = False,
    ) -> bool:
        _require_explicit(explicit)
        normalized_key = _normalize_key(key)
        with self._lock:
            cursor = self.connection.execute(
                """
                UPDATE long_term_memory_records
                SET status = ?, updated_at = ?, status_reason = ?
                WHERE key = ? AND status = 'active'
                """,
                (
                    MemoryStatus.FORGOTTEN.value,
                    _now().isoformat(),
                    reason.strip(),
                    normalized_key,
                ),
            )
            self.connection.commit()
            return cursor.rowcount > 0

    def restore(
        self,
        memory_id: str,
        *,
        reason: str = "explicit user request",
        explicit: bool = False,
    ) -> MemoryEntry:
        _require_explicit(explicit)
        with self._lock:
            entry = self.get_by_id(memory_id)
            if entry is None or entry.status is not MemoryStatus.FORGOTTEN:
                raise KeyError(memory_id)
            _reject_restricted_material(entry.key, entry.value)
            if self.get(entry.key) is not None:
                raise MemoryConflictError(f"Active memory already exists for {entry.key}")
            self.connection.execute(
                """
                UPDATE long_term_memory_records
                SET status = ?, updated_at = ?, status_reason = ? WHERE memory_id = ?
                """,
                (
                    MemoryStatus.ACTIVE.value,
                    _now().isoformat(),
                    reason.strip(),
                    memory_id,
                ),
            )
            self.connection.commit()
            restored = self.get_by_id(memory_id)
            if restored is None:
                raise RuntimeError("Restored memory could not be loaded")
            return restored

    def history(self, key: str) -> list[MemoryEntry]:
        normalized_key = _normalize_key(key)
        with self._lock:
            rows = self.connection.execute(
                f"SELECT {self._COLUMNS} FROM long_term_memory_records "
                "WHERE key = ? ORDER BY created_at",
                (normalized_key,),
            ).fetchall()
        return [self._entry_from_row(row) for row in rows]

    def list_active(self, *, include_sensitive: bool = False) -> list[MemoryEntry]:
        query = (
            f"SELECT {self._COLUMNS} FROM long_term_memory_records WHERE status = 'active'"
        )
        params: tuple[str, ...] = ()
        if not include_sensitive:
            query += " AND sensitivity = ?"
            params = (MemorySensitivity.NORMAL.value,)
        query += " ORDER BY updated_at DESC"
        with self._lock:
            rows = self.connection.execute(query, params).fetchall()
        return [self._entry_from_row(row) for row in rows]

    def list_all(self, *, include_sensitive: bool = False) -> list[MemoryEntry]:
        query = f"SELECT {self._COLUMNS} FROM long_term_memory_records"
        params: tuple[str, ...] = ()
        if not include_sensitive:
            query += " WHERE sensitivity = ?"
            params = (MemorySensitivity.NORMAL.value,)
        query += " ORDER BY updated_at DESC"
        with self._lock:
            rows = self.connection.execute(query, params).fetchall()
        return [self._entry_from_row(row) for row in rows]

    def recall(
        self,
        query: str,
        *,
        limit: int = 5,
        include_sensitive: bool = False,
    ) -> list[MemoryEntry]:
        return _rank_relevant(
            self.list_active(include_sensitive=include_sensitive), query, limit=limit
        )

    def close(self) -> None:
        with self._lock:
            self.connection.close()
