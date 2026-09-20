from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum
from uuid import uuid4

from jarvis.core.jarvis import Jarvis, TurnResult
from jarvis.core.router import Route
from jarvis.voice.normalizer import NormalizedUtterance


class ExecutionTier(IntEnum):
    DETERMINISTIC = 0
    CONVERSATION = 1
    AGENT = 2


@dataclass(frozen=True, slots=True)
class TierDecision:
    tier: ExecutionTier
    reason: str


class TierDispatcher:
    """Conservative, deterministic gate; uncertainty always falls through to Tier 2."""

    _APPROVAL = re.compile(
        r"^(?:yes(?:\s+please)?|approve|send\s+it|do\s+it|go\s+ahead|confirm|"
        r"perfect(?:[.!]?\s+send\s+it)?)\s*[.!]?$",
        re.I,
    )
    _REJECTION = re.compile(
        r"^(?:no(?:\s+thanks)?|reject|cancel|never\s*mind|forget\s+it|stop)\s*[.!]?$",
        re.I,
    )
    _FRESH_DATA = re.compile(
        r"\b(?:weather|forecast|temperature|rain(?:ing|y)?|snow(?:ing|y)?|"
        r"news|headlines|stock\s+price|share\s+price|"
        r"bitcoin\s+price|crypto\s+price|exchange\s+rate|sports?\s+score|live\s+score|"
        r"traffic|air\s+quality)\b",
        re.I,
    )
    _CLEAR_NEW_REQUEST = re.compile(
        r"^(?:(?:please\s+)?(?:tell\s+me|explain|define|give\s+me)|"
        r"(?:what(?:'s|\s+is|\s+are|\s+was|\s+were|\s+does|\s+do|\s+did|\s+can)|"
        r"who|why|how|when|where|will|can\s+you|could\s+you|would\s+you)\b)",
        re.I,
    )
    _CALENDAR_WRITE = re.compile(
        r"\b(?:create|add|book|move|reschedule|cancel|delete)\b.*"
        r"\b(?:meeting|event|lunch|dinner|appointment|call)\b",
        re.I,
    )

    def __init__(self, jarvis: Jarvis) -> None:
        self.jarvis = jarvis

    def decide(
        self,
        text: str,
        *,
        has_pending_input: bool,
        pending_known_locally: bool,
        weather_followup: bool = False,
    ) -> TierDecision:
        stripped = text.strip()
        if has_pending_input:
            if pending_known_locally and (
                self._APPROVAL.fullmatch(stripped) or self._REJECTION.fullmatch(stripped)
            ):
                return TierDecision(ExecutionTier.DETERMINISTIC, "explicit pending-task control")
            return TierDecision(ExecutionTier.AGENT, "pending task requires stateful continuation")

        if self._FRESH_DATA.search(stripped):
            return TierDecision(ExecutionTier.AGENT, "current external data required")
        if weather_followup:
            return TierDecision(ExecutionTier.AGENT, "weather context continuation")
        if self.jarvis.router.explicit_route(stripped) is not None:
            return TierDecision(ExecutionTier.AGENT, "explicit specialist domain")
        if self.jarvis.router.may_need_model_routing(stripped):
            return TierDecision(ExecutionTier.AGENT, "possible action or ambiguous intent")
        return TierDecision(ExecutionTier.CONVERSATION, "ordinary conversation")

    def is_clear_new_request(self, text: str) -> bool:
        """Recognize an obvious topic switch without interpreting clarification content."""
        stripped = text.strip()
        if self._APPROVAL.fullmatch(stripped) or self._REJECTION.fullmatch(stripped):
            return False
        return bool(self._CLEAR_NEW_REQUEST.search(stripped))

    def is_sensitive_action(self, text: str) -> bool:
        route = self.jarvis.router.explicit_route(text)
        return route in {Route.EMAIL, Route.CROSS_DOMAIN} or bool(
            self._CALENDAR_WRITE.search(text)
        )


class SessionCoordinator:
    """Single ingress for CLI/desktop turns across all execution tiers."""

    def __init__(
        self,
        jarvis: Jarvis,
        dispatcher: TierDispatcher | None = None,
        *,
        speech_action_confidence_threshold: float = 0.35,
    ) -> None:
        self.jarvis = jarvis
        self.dispatcher = dispatcher or TierDispatcher(jarvis)
        self.speech_action_confidence_threshold = speech_action_confidence_threshold
        self._locally_pending_threads: set[str] = set()

    def turn(
        self,
        text: str | NormalizedUtterance,
        *,
        thread_id: str = "default",
        trace_id: str | None = None,
        on_response_delta: Callable[[str], None] | None = None,
        voice_response: bool = False,
    ) -> TurnResult:
        utterance = text if isinstance(text, NormalizedUtterance) else None
        normalized_text = utterance.normalized_text if utterance else text
        trace_id = trace_id or self.jarvis.latency.current_trace_id or str(uuid4())
        has_pending = self.jarvis.has_pending_input(thread_id)
        with self.jarvis.latency.trace(trace_id, thread_id=thread_id):
            speech_prompt = self._speech_clarification_prompt(
                utterance,
                has_pending=has_pending,
                sensitive_action=self.dispatcher.is_sensitive_action(normalized_text),
            )
            if speech_prompt:
                self.jarvis.latency.record(
                    "speech.reject",
                    0.0,
                    reason=(utterance.rejection_reason if utterance else None)
                    or "low_confidence_sensitive_input",
                    pending=has_pending,
                )
                self._locally_pending_threads.add(thread_id)
                return TurnResult(
                    needs_input=True,
                    prompt=speech_prompt,
                    interrupt_kind="speech_clarification",
                    state=self.jarvis.state(thread_id=thread_id),
                    trace_id=trace_id,
                    execution_tier=0,
                    tier_reason="speech quality gate",
                )
            if has_pending and self.dispatcher.is_clear_new_request(normalized_text):
                with self.jarvis.latency.measure("pending.abandon"):
                    abandoned = self.jarvis.resume(
                        "cancel", thread_id=thread_id, trace_id=trace_id
                    )
                if abandoned.needs_input:
                    return abandoned
                self._locally_pending_threads.discard(thread_id)
                has_pending = False
            with self.jarvis.latency.measure("tier.dispatch"):
                decision = self.dispatcher.decide(
                    normalized_text,
                    has_pending_input=has_pending,
                    pending_known_locally=thread_id in self._locally_pending_threads,
                    weather_followup=self.jarvis.is_weather_followup(
                        normalized_text, thread_id
                    ),
                )
            with self.jarvis.latency.measure(f"tier.{int(decision.tier)}.total"):
                if decision.tier is ExecutionTier.CONVERSATION:
                    result = self.jarvis.direct_conversation(
                        normalized_text,
                        thread_id=thread_id,
                        trace_id=trace_id,
                        on_response_delta=on_response_delta,
                        voice_response=voice_response,
                    )
                else:
                    result = self.jarvis.turn(
                        normalized_text, thread_id=thread_id, trace_id=trace_id
                    )

        result.execution_tier = int(decision.tier)
        result.tier_reason = decision.reason
        if result.needs_input:
            self._locally_pending_threads.add(thread_id)
        else:
            self._locally_pending_threads.discard(thread_id)
        return result

    def _speech_clarification_prompt(
        self,
        utterance: NormalizedUtterance | None,
        *,
        has_pending: bool,
        sensitive_action: bool,
    ) -> str | None:
        if utterance is None:
            return None
        if utterance.requires_clarification:
            if utterance.rejection_reason == "excessive_clipping":
                return "The audio was distorted. Please say that again."
            return "I didn't catch that clearly. Please say it again."
        if (
            utterance.stt_confidence is not None
            and utterance.stt_confidence < self.speech_action_confidence_threshold
            and (has_pending or sensitive_action)
        ):
            return "I'm not confident I heard that action correctly. Please repeat it."
        return None
