from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from jarvis.memory.long_term import MemoryKind, MemorySensitivity


class MemoryOperation(str, Enum):
    REMEMBER = "remember"
    RECALL = "recall"
    CORRECT = "correct"
    FORGET = "forget"


@dataclass(frozen=True, slots=True)
class MemoryCommand:
    operation: MemoryOperation
    subject: str
    key: str | None = None
    value: str | None = None
    kind: MemoryKind = MemoryKind.OTHER
    sensitivity: MemorySensitivity = MemorySensitivity.NORMAL
    restricted: bool = False


_CORRECT = re.compile(
    r"^(?:please\s+)?(?:correct|update|change)\s+"
    r"(?:(?:what\s+you\s+remember\s+about)|(?:the\s+memory\s+for))\s+"
    r"(?P<subject>.+?)\s+(?:to|as)\s+(?P<value>.+)$",
    re.I,
)
_RECALL = re.compile(
    r"^(?:(?:what|which)\s+do\s+you\s+remember\s+about|"
    r"what\s+have\s+you\s+(?:saved|remembered)\s+about|"
    r"(?:please\s+)?recall)\s+(?P<subject>.+)$",
    re.I,
)
_FORGET = re.compile(
    r"^(?:please\s+)?forget\s+(?:(?:what\s+you\s+remember\s+about)\s+|that\s+)?"
    r"(?P<subject>.+)$",
    re.I,
)
_REMEMBER = re.compile(
    r"^(?:please\s+)?remember\s+(?:that\s+)?(?P<body>.+)$",
    re.I,
)
_RELATION = re.compile(r"^(?:my\s+)?(?P<label>.+?)\s+(?:is|are)\s+(?P<value>.+)$", re.I)
_PREFERENCE = re.compile(r"^(?:i\s+)?(?:prefer|like)\s+(?P<value>.+)$", re.I)
_RESTRICTED = re.compile(
    r"\b(?:password|passcode|api\s*key|secret\s*key|access\s*token|refresh\s*token|"
    r"private\s*key|credit\s*card|card\s*number|cvv|social\s*security|ssn)\b",
    re.I,
)
_SENSITIVE = re.compile(
    r"\b(?:home\s+address|street\s+address|phone\s+number|birthday|date\s+of\s+birth|"
    r"medical|health|diagnosis|medication)\b",
    re.I,
)
_WAKE_PREFIX = re.compile(
    r"^(?:(?:hey|ok|okay)\s+)?jarvis(?:\s*[,;:\-]\s*|\s+)",
    re.I,
)


def parse_memory_command(text: str) -> MemoryCommand | None:
    clean = _clean(text)
    clean = _WAKE_PREFIX.sub("", clean, count=1)
    if not clean:
        return None

    match = _CORRECT.fullmatch(clean)
    if match:
        subject = _subject(match.group("subject"))
        value = _clean(match.group("value"))
        return MemoryCommand(
            operation=MemoryOperation.CORRECT,
            subject=subject,
            key=_key(subject),
            value=value,
            kind=_kind(f"{subject} {value}"),
            sensitivity=_sensitivity(f"{subject} {value}"),
            restricted=bool(_RESTRICTED.search(f"{subject} {value}")),
        )

    match = _RECALL.fullmatch(clean)
    if match:
        subject = _subject(match.group("subject"))
        return MemoryCommand(operation=MemoryOperation.RECALL, subject=subject)

    match = _FORGET.fullmatch(clean)
    if match:
        subject = _subject(match.group("subject"))
        if subject.lower() in {"it", "this", "that", "everything"}:
            return None
        return MemoryCommand(
            operation=MemoryOperation.FORGET,
            subject=subject,
            key=_key(subject),
        )

    match = _REMEMBER.fullmatch(clean)
    if not match:
        return None
    body = _clean(match.group("body"))
    if body.lower().startswith("to "):
        return None
    relation = _RELATION.fullmatch(body)
    if relation:
        subject = _subject(relation.group("label"))
        value = _clean(relation.group("value"))
    else:
        preference = _PREFERENCE.fullmatch(body)
        if preference:
            value = _clean(preference.group("value"))
            subject = _preference_subject(value)
        else:
            subject = _subject(body)
            value = body
    combined = f"{subject} {value}"
    return MemoryCommand(
        operation=MemoryOperation.REMEMBER,
        subject=subject,
        key=_key(subject),
        value=value,
        kind=_kind(combined),
        sensitivity=_sensitivity(combined),
        restricted=bool(_RESTRICTED.search(combined)),
    )


def classify_memory_sensitivity(value: str) -> MemorySensitivity:
    """Classify user-entered memory text without interpreting it as a command."""
    return _sensitivity(value)


def _clean(value: str) -> str:
    return value.strip().strip(".!? \t\r\n\"'")


def _subject(value: str) -> str:
    clean = _clean(value)
    clean = re.sub(r"^(?:my|the)\s+", "", clean, flags=re.I)
    return clean


def _key(subject: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", subject.lower()).strip("_")
    return key[:160]


def _preference_subject(value: str) -> str:
    tokens = re.findall(r"[a-z0-9]+", value.lower())[:6]
    return "preference " + " ".join(tokens)


def _kind(value: str) -> MemoryKind:
    lowered = value.lower()
    if re.search(r"\b(?:prefer|preference|favorite|like|dislike)\b", lowered):
        return MemoryKind.PREFERENCE
    if re.search(r"\b(?:name|pronouns?|timezone|location|live\s+in)\b", lowered):
        return MemoryKind.IDENTITY
    if re.search(r"\b(?:always|never|when\s+you)\b", lowered):
        return MemoryKind.INSTRUCTION
    return MemoryKind.FACT


def _sensitivity(value: str) -> MemorySensitivity:
    return (
        MemorySensitivity.SENSITIVE
        if _SENSITIVE.search(value)
        else MemorySensitivity.NORMAL
    )
