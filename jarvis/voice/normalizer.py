from __future__ import annotations

import re
from dataclasses import dataclass, field

from jarvis.voice.vocabulary import VocabularyStore


@dataclass(frozen=True, slots=True)
class SpeechCorrection:
    original: str
    replacement: str
    reason: str


@dataclass(frozen=True, slots=True)
class NormalizedUtterance:
    raw_transcript: str
    normalized_text: str
    stt_confidence: float | None = None
    normalization_confidence: float = 1.0
    corrections: list[SpeechCorrection] = field(default_factory=list)
    ambiguous_entities: list[str] = field(default_factory=list)
    requires_clarification: bool = False
    rejection_reason: str | None = None


class SpeechNormalizer:
    """Conservative deterministic repair before routing or task interpretation."""

    _WEATHER_CUE = re.compile(
        r"\b(?:weather|forecast|temperature|rain(?:ing|y)?|snow(?:ing|y)?)\b",
        re.I,
    )
    _CALENDAR_LOOKING_LIKE = re.compile(
        r"^(?:(?:what(?:'s|s)?|how(?:'s|s)?)\s+)?"
        r"(?:to)?moral[\s-]+looking[\s-]+(?:life|like)[?.]?$",
        re.I,
    )

    def __init__(self, vocabulary: VocabularyStore | None = None) -> None:
        self.vocabulary = vocabulary

    def normalize(
        self,
        transcript: str,
        *,
        stt_confidence: float | None = None,
        rejection_reason: str | None = None,
    ) -> NormalizedUtterance:
        raw = transcript.strip()
        text = re.sub(r"\s+", " ", raw)
        corrections: list[SpeechCorrection] = []

        if re.match(r"^help me something interesting\b", text, re.I):
            text = self._replace(
                text,
                r"^help me(?=\s+something interesting\b)",
                "Tell me",
                "tell command substitution",
                corrections,
            )

        if re.match(r"^female\b", text, re.I) and re.search(
            r"^female\s+.{1,60}\s+and\s+(?:say|tell)\b", text, re.I
        ):
            text = self._replace(
                text,
                r"^female\b",
                "Email",
                "email command homophone",
                corrections,
            )

        if self._CALENDAR_LOOKING_LIKE.fullmatch(text):
            corrections.append(
                SpeechCorrection(text, "What's tomorrow looking like?", "safe calendar phrase")
            )
            text = "What's tomorrow looking like?"

        if self._WEATHER_CUE.search(text):
            text = self._replace(
                text,
                r"\b(?:and|in)\s+soul\b",
                "in Seoul",
                "weather location homophone",
                corrections,
            )
            text = self._replace(
                text,
                r"^(?:fell|felt|held|hold|holid)\s+it\s+(?=rain\b)",
                "Will it ",
                "weather question homophone",
                corrections,
            )
            if re.search(r"\bSeoul\s+moral[?.]?$", text, re.I):
                text = self._replace(
                    text,
                    r"\bmoral(?=[?.]?$)",
                    "tomorrow",
                    "weather date homophone",
                    corrections,
                )

        if self.vocabulary:
            text, vocabulary_corrections = self.vocabulary.apply(text)
            corrections.extend(
                SpeechCorrection(
                    correction.original,
                    correction.replacement,
                    "explicit vocabulary alias",
                )
                for correction in vocabulary_corrections
            )

        return NormalizedUtterance(
            raw_transcript=raw,
            normalized_text=text,
            stt_confidence=stt_confidence,
            normalization_confidence=1.0 if not corrections else 0.95,
            corrections=corrections,
            requires_clarification=bool(rejection_reason),
            rejection_reason=rejection_reason,
        )

    @staticmethod
    def _replace(
        text: str,
        pattern: str,
        replacement: str,
        reason: str,
        corrections: list[SpeechCorrection],
    ) -> str:
        match = re.search(pattern, text, re.I)
        if not match:
            return text
        corrections.append(SpeechCorrection(match.group(0), replacement.strip(), reason))
        return re.sub(pattern, replacement, text, flags=re.I)
