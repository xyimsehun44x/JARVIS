from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any


VOCABULARY_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class VocabularyEntry:
    canonical: str
    aliases: tuple[str, ...] = ()
    source: str = "user"
    explicit: bool = True
    usage_count: int = 0
    confidence: float = 1.0
    sensitive: bool = False
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True, slots=True)
class VocabularyCorrection:
    original: str
    replacement: str
    canonical: str


class VocabularyStore:
    """Small explicit dictionary used for weak STT bias and deterministic repair."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None
        self._lock = RLock()
        self._entries: dict[str, VocabularyEntry] = {}
        self._dirty = False
        if self.path and self.path.is_file():
            self._load()

    def entries(self) -> list[VocabularyEntry]:
        with self._lock:
            return sorted(self._entries.values(), key=lambda entry: entry.canonical.lower())

    def add(
        self,
        canonical: str,
        *,
        aliases: list[str] | tuple[str, ...] = (),
        source: str = "user",
        explicit: bool = True,
        confidence: float = 1.0,
        sensitive: bool = False,
    ) -> VocabularyEntry:
        canonical = self._clean_term(canonical)
        cleaned_aliases = tuple(
            dict.fromkeys(
                self._clean_term(alias)
                for alias in aliases
                if alias and alias.strip()
            )
        )
        if not 0 <= confidence <= 1:
            raise ValueError("Vocabulary confidence must be between 0 and 1")
        source = self._clean_source(source)
        now = datetime.now(timezone.utc).isoformat()
        key = canonical.casefold()
        with self._lock:
            current = self._entries.get(key)
            self._assert_no_conflicts(key, canonical, cleaned_aliases)
            if current:
                combined_aliases = tuple(
                    dict.fromkeys((*current.aliases, *cleaned_aliases))
                )
                entry = replace(
                    current,
                    aliases=combined_aliases,
                    source=source,
                    explicit=current.explicit or explicit,
                    confidence=max(current.confidence, confidence),
                    sensitive=current.sensitive or sensitive,
                    updated_at=now,
                )
            else:
                entry = VocabularyEntry(
                    canonical=canonical,
                    aliases=cleaned_aliases,
                    source=source,
                    explicit=explicit,
                    confidence=confidence,
                    sensitive=sensitive,
                    created_at=now,
                    updated_at=now,
                )
            self._entries[key] = entry
            self._dirty = True
            self.flush()
            return entry

    def select_terms(self, context_text: str = "", *, limit: int = 15) -> list[str]:
        """Return a capped, non-sensitive canonical vocabulary list for weak STT bias."""
        if limit < 1:
            return []
        context = context_text.casefold()
        with self._lock:
            candidates = [
                entry
                for entry in self._entries.values()
                if entry.confidence > 0.8 and not entry.sensitive
            ]

        def is_contextual(entry: VocabularyEntry) -> bool:
            terms = (entry.canonical, *entry.aliases)
            return any(self._contains_term(context, term.casefold()) for term in terms)

        candidates.sort(
            key=lambda entry: (
                not is_contextual(entry),
                not entry.explicit,
                -entry.usage_count,
                -entry.confidence,
                -len(entry.canonical),
                entry.canonical.casefold(),
            )
        )
        return [entry.canonical for entry in candidates[: min(limit, 15)]]

    def remove(self, canonical: str) -> bool:
        key = self._clean_term(canonical).casefold()
        with self._lock:
            if key not in self._entries:
                return False
            del self._entries[key]
            self._dirty = True
            self.flush()
            return True

    def apply(self, text: str) -> tuple[str, list[VocabularyCorrection]]:
        """Apply high-confidence, non-sensitive aliases once without cascading."""
        alias_entries: list[tuple[str, VocabularyEntry]] = []
        with self._lock:
            for entry in self._entries.values():
                if entry.confidence <= 0.8 or entry.sensitive:
                    continue
                for alias in entry.aliases:
                    alias_entries.append((alias, entry))
        alias_entries.sort(
            key=lambda item: (-len(item[0]), -item[1].usage_count, item[0].casefold())
        )
        unique: list[tuple[str, VocabularyEntry]] = []
        seen_aliases: set[str] = set()
        for alias, entry in alias_entries:
            key = alias.casefold()
            if key not in seen_aliases:
                seen_aliases.add(key)
                unique.append((alias, entry))
        if not unique:
            return text, []

        groups: dict[str, VocabularyEntry] = {}
        parts: list[str] = []
        for index, (alias, entry) in enumerate(unique):
            group = f"alias_{index}"
            groups[group] = entry
            parts.append(rf"(?P<{group}>(?<!\w){re.escape(alias)}(?!\w))")
        pattern = re.compile("|".join(parts), re.I)
        corrections: list[VocabularyCorrection] = []
        used_keys: set[str] = set()

        def replace_match(match: re.Match[str]) -> str:
            group = match.lastgroup
            if group is None:
                return match.group(0)
            entry = groups[group]
            original = match.group(0)
            corrections.append(
                VocabularyCorrection(original, entry.canonical, entry.canonical)
            )
            used_keys.add(entry.canonical.casefold())
            return entry.canonical

        normalized = pattern.sub(replace_match, text)
        if used_keys:
            now = datetime.now(timezone.utc).isoformat()
            with self._lock:
                for key in used_keys:
                    entry = self._entries[key]
                    self._entries[key] = replace(
                        entry,
                        usage_count=entry.usage_count + 1,
                        updated_at=now,
                    )
                self._dirty = True
        return normalized, corrections

    def flush(self) -> None:
        if not self.path:
            return
        with self._lock:
            if not self._dirty:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "schema_version": VOCABULARY_SCHEMA_VERSION,
                "entries": [self._serialize(entry) for entry in self.entries()],
            }
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.path)
            self._dirty = False

    def close(self) -> None:
        self.flush()

    def _load(self) -> None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Could not load vocabulary file {self.path}: {exc}") from exc
        if payload.get("schema_version") != VOCABULARY_SCHEMA_VERSION:
            raise ValueError("Unsupported vocabulary schema version")
        loaded: dict[str, VocabularyEntry] = {}
        owners: dict[str, str] = {}
        for raw in payload.get("entries", []):
            entry = self._deserialize(raw)
            key = entry.canonical.casefold()
            for term in (entry.canonical, *entry.aliases):
                term_key = term.casefold()
                owner = owners.get(term_key)
                if owner is not None and owner != key:
                    raise ValueError(f"Vocabulary term is ambiguous: {term}")
                owners[term_key] = key
            loaded[key] = entry
        self._entries = loaded
        self._dirty = False

    @classmethod
    def _deserialize(cls, raw: dict[str, Any]) -> VocabularyEntry:
        canonical = cls._clean_term(str(raw.get("canonical", "")))
        aliases = tuple(cls._clean_term(str(alias)) for alias in raw.get("aliases", []))
        confidence = float(raw.get("confidence", 1.0))
        if not 0 <= confidence <= 1:
            raise ValueError("Vocabulary confidence must be between 0 and 1")
        return VocabularyEntry(
            canonical=canonical,
            aliases=aliases,
            source=cls._clean_source(str(raw.get("source", "user"))),
            explicit=bool(raw.get("explicit", False)),
            usage_count=max(0, int(raw.get("usage_count", 0))),
            confidence=confidence,
            sensitive=bool(raw.get("sensitive", False)),
            created_at=str(raw.get("created_at", "")),
            updated_at=str(raw.get("updated_at", "")),
        )

    @staticmethod
    def _serialize(entry: VocabularyEntry) -> dict[str, Any]:
        payload = asdict(entry)
        payload["aliases"] = list(entry.aliases)
        return payload

    @staticmethod
    def _clean_term(value: str) -> str:
        cleaned = re.sub(r"\s+", " ", value).strip()
        if not cleaned:
            raise ValueError("Vocabulary terms cannot be empty")
        if len(cleaned) > 80:
            raise ValueError("Vocabulary terms must be 80 characters or fewer")
        if any(ord(character) < 32 for character in cleaned):
            raise ValueError("Vocabulary terms cannot contain control characters")
        return cleaned

    @staticmethod
    def _clean_source(value: str) -> str:
        cleaned = value.strip() or "user"
        if len(cleaned) > 40:
            raise ValueError("Vocabulary source must be 40 characters or fewer")
        return cleaned

    def _assert_no_conflicts(
        self, key: str, canonical: str, aliases: tuple[str, ...]
    ) -> None:
        incoming = {term.casefold() for term in (canonical, *aliases)}
        for other_key, entry in self._entries.items():
            if other_key == key:
                continue
            existing = {term.casefold() for term in (entry.canonical, *entry.aliases)}
            overlap = incoming & existing
            if overlap:
                conflict = next(iter(overlap))
                raise ValueError(
                    f"Vocabulary term already belongs to {entry.canonical}: {conflict}"
                )

    @staticmethod
    def _contains_term(context: str, term: str) -> bool:
        return bool(re.search(rf"(?<!\w){re.escape(term)}(?!\w)", context, re.I))
