from __future__ import annotations

import importlib.util
import sys
from contextlib import redirect_stdout
from dataclasses import dataclass
from typing import Any, Callable
from uuid import uuid4

from jarvis.config import Settings
from jarvis.core.jarvis import TurnResult
from jarvis.core.session import SessionCoordinator


EventEmitter = Callable[..., None]


@dataclass(frozen=True, slots=True)
class DesktopVoiceTurn:
    result: TurnResult
    heard: str
    understood: str
    confidence: float | None
    quality: str


class DesktopVoiceRuntime:
    """Desktop adapter around the same STT, normalization, session, and TTS path."""

    _DEPENDENCIES = ("faster_whisper", "kokoro", "numpy", "sounddevice")

    def __init__(
        self,
        session: SessionCoordinator,
        settings: Settings,
        emit_event: EventEmitter,
    ) -> None:
        self.session = session
        self.settings = settings
        self.emit_event = emit_event
        self.stt: Any | None = None
        self.tts: Any | None = None
        self.normalizer: Any | None = None
        self.vocabulary: Any | None = None
        self._capture_thread_id: str | None = None
        self._capture_trace_id: str | None = None
        self._latency_checkpoint = 0

    @classmethod
    def dependencies_available(cls) -> bool:
        return all(importlib.util.find_spec(name) is not None for name in cls._DEPENDENCIES)

    @property
    def is_capturing(self) -> bool:
        return bool(self.stt and self.stt.is_capturing)

    @property
    def is_loaded(self) -> bool:
        return self.stt is not None and self.tts is not None

    def preload(self) -> None:
        """Load desktop voice models before the microphone becomes available."""
        with redirect_stdout(sys.stderr):
            self._ensure_loaded()

    def start(self, *, thread_id: str, request_id: str) -> dict[str, Any]:
        if self.is_capturing:
            raise ValueError("Speech capture is already active")
        trace_id = str(uuid4())
        self._latency_checkpoint = self.session.jarvis.latency.checkpoint()
        if not self.is_loaded:
            self._state("loading", request_id=request_id)
        with redirect_stdout(sys.stderr):
            self._ensure_loaded()
            with self.session.jarvis.latency.trace(trace_id, thread_id=thread_id):
                self.stt.start_capture()
        self._capture_thread_id = thread_id
        self._capture_trace_id = trace_id
        self._state("listening", request_id=request_id)
        return {"status": "listening", "thread_id": thread_id}

    def stop(self, *, thread_id: str, request_id: str) -> DesktopVoiceTurn:
        if not self.is_capturing:
            raise ValueError("Speech capture is not active")
        if thread_id != self._capture_thread_id:
            raise ValueError("voice.stop thread_id must match the active recording")
        trace_id = self._capture_trace_id or str(uuid4())
        self._state("transcribing", request_id=request_id)
        speech = None
        try:
            with redirect_stdout(sys.stderr):
                with self.session.jarvis.latency.trace(trace_id, thread_id=thread_id):
                    stt_result = self.stt.stop_capture(
                        vocabulary=self._thread_vocabulary(thread_id)
                    )
                    with self.session.jarvis.latency.measure("speech.normalize"):
                        utterance = self.normalizer.normalize(
                            stt_result.text,
                            stt_confidence=stt_result.confidence,
                            rejection_reason=stt_result.rejection_reason,
                        )
                    self.emit_event(
                        "voice.transcript",
                        request_id=request_id,
                        payload={
                            "thread_id": thread_id,
                            "heard": utterance.raw_transcript,
                            "understood": utterance.normalized_text,
                            "corrected": (
                                utterance.normalized_text != utterance.raw_transcript
                            ),
                            "confidence": stt_result.confidence,
                            "quality": stt_result.quality,
                        },
                    )
                    self._state("thinking", request_id=request_id)
                    speech = self.tts.start_stream(
                        response_started_at=self.stt.last_capture_ended_at
                    )
                    speaking = False
                    displayed_text = ""

                    def emit_delta(delta: str) -> None:
                        nonlocal displayed_text, speaking
                        displayed_text += delta
                        self.emit_event(
                            "assistant.delta",
                            request_id=request_id,
                            payload={"thread_id": thread_id, "delta": delta},
                        )
                        if not speaking:
                            speaking = True
                            self._state("speaking", request_id=request_id)
                        speech.write(delta)

                    result = self.session.turn(
                        utterance,
                        thread_id=thread_id,
                        trace_id=trace_id,
                        on_response_delta=emit_delta,
                        voice_response=True,
                    )
                    spoken_text = result.prompt if result.needs_input else result.response
                    if result.response_streamed:
                        if spoken_text and displayed_text.strip() != spoken_text:
                            self.emit_event(
                                "assistant.delta",
                                request_id=request_id,
                                payload={
                                    "thread_id": thread_id,
                                    "delta": spoken_text,
                                    "replace": True,
                                },
                            )
                        speech.finish()
                    else:
                        speech.abort()
                        if spoken_text:
                            # Non-streaming Tier-2, clarification, approval, and
                            # model-fallback responses have no incremental deltas.
                            # Publish the completed text before blocking on playback
                            # so the desktop bubble does not remain in "thinking".
                            self.emit_event(
                                "assistant.delta",
                                request_id=request_id,
                                payload={
                                    "thread_id": thread_id,
                                    "delta": spoken_text,
                                    "replace": True,
                                },
                            )
                            self._state("speaking", request_id=request_id)
                            self.tts.speak(
                                spoken_text,
                                response_started_at=self.stt.last_capture_ended_at,
                            )
        except BaseException:
            if speech is not None:
                speech.abort()
            self._state("error", request_id=request_id)
            raise
        finally:
            self._capture_thread_id = None
            self._capture_trace_id = None
        self._state("idle", request_id=request_id)
        events = self.session.jarvis.latency.events_since(self._latency_checkpoint)
        if events:
            print(
                "Desktop voice latency: "
                + self._diagnostic_summary(events),
                file=sys.stderr,
                flush=True,
            )
        return DesktopVoiceTurn(
            result=result,
            heard=utterance.raw_transcript,
            understood=utterance.normalized_text,
            confidence=stt_result.confidence,
            quality=stt_result.quality,
        )

    def cancel(self, *, request_id: str) -> dict[str, Any]:
        if self.stt and self.stt.is_capturing:
            with redirect_stdout(sys.stderr):
                self.stt.cancel_capture()
        self._capture_thread_id = None
        self._capture_trace_id = None
        self._state("idle", request_id=request_id)
        return {"status": "cancelled"}

    def close(self) -> None:
        try:
            if self.stt and self.stt.is_capturing:
                with redirect_stdout(sys.stderr):
                    self.stt.cancel_capture()
        finally:
            if self.vocabulary:
                self.vocabulary.close()

    def _ensure_loaded(self) -> None:
        if self.stt is not None:
            return
        if not self.dependencies_available():
            raise RuntimeError("Install the 'voice' extras to use desktop voice")
        from jarvis.voice.normalizer import SpeechNormalizer
        from jarvis.voice.stt import PushToTalkSTT
        from jarvis.voice.tts import KokoroTTS
        from jarvis.voice.vocabulary import VocabularyStore

        vocabulary = VocabularyStore(self.settings.vocabulary_path)
        try:
            stt = PushToTalkSTT(
                model_size=self.settings.stt_model,
                beam_size=self.settings.stt_beam_size,
                hotwords=self.settings.stt_hotwords,
                initial_prompt=self.settings.stt_initial_prompt,
                vad_filter=self.settings.stt_vad_filter,
                latency_recorder=self.session.jarvis.latency,
            )
            tts = KokoroTTS(latency_recorder=self.session.jarvis.latency)
        except BaseException:
            vocabulary.close()
            raise
        self.vocabulary = vocabulary
        self.normalizer = SpeechNormalizer(vocabulary)
        self.stt = stt
        self.tts = tts

    def _thread_vocabulary(self, thread_id: str) -> list[str]:
        messages = self.session.jarvis.state(thread_id=thread_id).get("messages", [])[-6:]
        context = " ".join(
            str(message.get("content", ""))
            for message in messages
            if isinstance(message, dict)
        )
        return self.vocabulary.select_terms(
            context, limit=self.settings.stt_vocabulary_limit
        )

    def _state(self, state: str, *, request_id: str) -> None:
        self.emit_event(
            "voice.state",
            request_id=request_id,
            payload={"state": state},
        )

    def _diagnostic_summary(self, events: list[Any]) -> str:
        summary = self.session.jarvis.latency.format_events(events)
        transcription = next(
            (event for event in reversed(events) if event.stage == "stt.transcribe"),
            None,
        )
        if transcription is None:
            return summary
        metadata = transcription.metadata
        details = []
        for key, label in (
            ("stt_quality", "stt.quality"),
            ("stt_confidence", "stt.confidence"),
            ("input_rms_dbfs", "input.rms_dbfs"),
            ("input_peak", "input.peak"),
        ):
            value = metadata.get(key)
            if value is not None:
                details.append(f"{label}={value}")
        return " | ".join((summary, *details))
