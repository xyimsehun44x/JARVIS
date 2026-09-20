from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

from jarvis.core.latency import LatencyRecorder


STTQuality = Literal["high", "medium", "low", "invalid"]


@dataclass(frozen=True, slots=True)
class STTResult:
    text: str
    confidence: float | None
    quality: STTQuality
    rejection_reason: str | None
    audio_duration_ms: float
    input_peak: float
    input_rms_dbfs: float
    input_gain_db: float
    clipped_fraction: float
    segment_count: int = 0
    average_log_probability: float | None = None
    no_speech_probability: float | None = None

    @property
    def is_valid(self) -> bool:
        return self.rejection_reason is None


class PushToTalkSTT:
    def __init__(
        self,
        model_size: str = "small.en",
        beam_size: int = 5,
        hotwords: str | None = "Jarvis",
        initial_prompt: str | None = None,
        vad_filter: bool = True,
        latency_recorder: LatencyRecorder | None = None,
    ) -> None:
        try:
            import sounddevice as sd
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError("Install the 'voice' extras to use speech recognition") from exc
        self.sd = sd
        self.sample_rate = 16000
        self.beam_size = beam_size
        self.hotwords = hotwords
        self.initial_prompt = initial_prompt
        self.vad_filter = vad_filter
        self.latency = latency_recorder or LatencyRecorder()
        self.last_capture_ended_at: float | None = None
        self._capture_stream: Any | None = None
        self._capture_frames: list[Any] | None = None
        self._capture_started_at: float | None = None
        with self.latency.measure("stt.load"):
            self.model = WhisperModel(model_size, device="cpu", compute_type="int8")

    def listen(self, *, vocabulary: list[str] | None = None) -> STTResult:
        input("Press Enter to start speaking, then press Enter again to stop...")
        self.start_capture()
        try:
            input()
        except BaseException:
            self.cancel_capture()
            raise
        return self.stop_capture(vocabulary=vocabulary)

    @property
    def is_capturing(self) -> bool:
        return self._capture_stream is not None

    def start_capture(self) -> None:
        """Begin microphone capture without consuming process stdin."""
        if self.is_capturing:
            raise RuntimeError("Speech capture is already active")
        frames: list[Any] = []

        def callback(indata, *_):
            frames.append(indata.copy())

        started = self.latency.now()
        stream = self.sd.InputStream(
            samplerate=self.sample_rate, channels=1, dtype="float32", callback=callback
        )
        self._capture_stream = stream
        self._capture_frames = frames
        self._capture_started_at = started
        try:
            stream.start()
        except BaseException:
            self._clear_capture()
            try:
                stream.close()
            except Exception:
                pass
            raise

    def stop_capture(self, *, vocabulary: list[str] | None = None) -> STTResult:
        """Stop the active microphone stream and transcribe its buffered audio."""
        stream = self._capture_stream
        frames = self._capture_frames
        started = self._capture_started_at
        if stream is None or frames is None or started is None:
            raise RuntimeError("Speech capture is not active")
        try:
            stream.stop()
        finally:
            try:
                stream.close()
            finally:
                self._clear_capture()
                self.latency.record_elapsed("audio.capture", started)
                self.last_capture_ended_at = self.latency.now()
        return self._transcribe_frames(frames, vocabulary=vocabulary)

    def cancel_capture(self) -> None:
        """Discard an active recording without running speech recognition."""
        stream = self._capture_stream
        started = self._capture_started_at
        if stream is None:
            return
        try:
            stream.stop()
        finally:
            try:
                stream.close()
            finally:
                self._clear_capture()
                if started is not None:
                    self.latency.record_elapsed("audio.capture", started, cancelled=True)
                self.last_capture_ended_at = self.latency.now()

    def _clear_capture(self) -> None:
        self._capture_stream = None
        self._capture_frames = None
        self._capture_started_at = None

    def _transcribe_frames(
        self,
        frames: list[Any],
        *,
        vocabulary: list[str] | None = None,
    ) -> STTResult:
        import numpy as np

        if not frames:
            return self._invalid_result("empty_capture")
        audio = np.concatenate(frames, axis=0).flatten()
        audio_duration_ms = audio.size / self.sample_rate * 1000
        raw_peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        raw_rms = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0
        raw_rms_dbfs = 20 * math.log10(max(raw_rms, 1e-9))
        clipped_fraction = (
            float(np.mean(np.abs(audio) >= 0.999)) if audio.size else 0.0
        )
        rejection_reason = self._validate_capture(
            audio_duration_ms=audio_duration_ms,
            peak=raw_peak,
            rms=raw_rms,
            clipped_fraction=clipped_fraction,
        )
        if rejection_reason:
            return self._invalid_result(
                rejection_reason,
                audio_duration_ms=audio_duration_ms,
                input_peak=raw_peak,
                input_rms_dbfs=raw_rms_dbfs,
                clipped_fraction=clipped_fraction,
            )
        audio, gain = self._normalize_level(audio, raw_peak, raw_rms)
        gain_db = 20 * math.log10(max(gain, 1e-9))
        started = self.latency.now()
        vocabulary_terms = self._bounded_vocabulary(vocabulary or [])
        segments_iter, _ = self.model.transcribe(
            audio,
            language="en",
            beam_size=self.beam_size,
            temperature=0.0,
            condition_on_previous_text=False,
            hotwords=self.hotwords,
            initial_prompt=self._prompt_with_vocabulary(vocabulary_terms),
            vad_filter=self.vad_filter,
            without_timestamps=True,
        )
        segments = list(segments_iter)
        transcript = " ".join(segment.text for segment in segments).strip()
        average_log_probability, no_speech_probability = self._segment_quality(segments)
        confidence = self._confidence(average_log_probability, no_speech_probability)
        quality: STTQuality
        if not transcript:
            quality = "invalid"
            rejection_reason = "no_transcript"
        elif confidence is None or confidence >= 0.65:
            quality = "high"
            rejection_reason = None
        elif confidence >= 0.35:
            quality = "medium"
            rejection_reason = None
        else:
            quality = "low"
            rejection_reason = None
        self.latency.record_elapsed(
            "stt.transcribe",
            started,
            input_peak=round(raw_peak, 4),
            input_rms_dbfs=round(raw_rms_dbfs, 1),
            input_gain_db=round(gain_db, 1),
            audio_duration_ms=round(audio_duration_ms, 1),
            clipped_fraction=round(clipped_fraction, 4),
            segment_count=len(segments),
            average_log_probability=self._round_optional(average_log_probability),
            no_speech_probability=self._round_optional(no_speech_probability),
            stt_confidence=self._round_optional(confidence),
            stt_quality=quality,
            rejected=bool(rejection_reason),
            vocabulary_count=len(vocabulary_terms),
        )
        return STTResult(
            text=transcript,
            confidence=confidence,
            quality=quality,
            rejection_reason=rejection_reason,
            audio_duration_ms=audio_duration_ms,
            input_peak=raw_peak,
            input_rms_dbfs=raw_rms_dbfs,
            input_gain_db=gain_db,
            clipped_fraction=clipped_fraction,
            segment_count=len(segments),
            average_log_probability=average_log_probability,
            no_speech_probability=no_speech_probability,
        )

    def _invalid_result(
        self,
        reason: str,
        *,
        audio_duration_ms: float = 0.0,
        input_peak: float = 0.0,
        input_rms_dbfs: float = -180.0,
        clipped_fraction: float = 0.0,
    ) -> STTResult:
        self.latency.record(
            "stt.validate",
            0.0,
            rejection_reason=reason,
            audio_duration_ms=round(audio_duration_ms, 1),
            input_peak=round(input_peak, 4),
            input_rms_dbfs=round(input_rms_dbfs, 1),
            clipped_fraction=round(clipped_fraction, 4),
        )
        return STTResult(
            text="",
            confidence=0.0,
            quality="invalid",
            rejection_reason=reason,
            audio_duration_ms=audio_duration_ms,
            input_peak=input_peak,
            input_rms_dbfs=input_rms_dbfs,
            input_gain_db=0.0,
            clipped_fraction=clipped_fraction,
        )

    @staticmethod
    def _validate_capture(
        *, audio_duration_ms: float, peak: float, rms: float, clipped_fraction: float
    ) -> str | None:
        if audio_duration_ms < 250:
            return "too_short"
        if peak < 1e-4 or rms < 1e-5:
            return "silence"
        if clipped_fraction >= 0.1:
            return "excessive_clipping"
        return None

    @staticmethod
    def _segment_quality(segments: list[Any]) -> tuple[float | None, float | None]:
        if not segments:
            return None, None
        weights = [
            max(float(segment.end) - float(segment.start), 0.01)
            for segment in segments
        ]
        total_weight = sum(weights)
        average_log_probability = sum(
            float(segment.avg_logprob) * weight
            for segment, weight in zip(segments, weights)
        ) / total_weight
        no_speech_probability = sum(
            float(segment.no_speech_prob) * weight
            for segment, weight in zip(segments, weights)
        ) / total_weight
        return average_log_probability, no_speech_probability

    @staticmethod
    def _confidence(
        average_log_probability: float | None,
        no_speech_probability: float | None,
    ) -> float | None:
        """Return a bounded quality heuristic, not a calibrated probability."""
        if average_log_probability is None:
            return None
        speech_probability = 1.0 - min(max(no_speech_probability or 0.0, 0.0), 1.0)
        score = math.exp(min(average_log_probability, 0.0)) * speech_probability
        return min(max(score, 0.0), 1.0)

    @staticmethod
    def _round_optional(value: float | None) -> float | None:
        return round(value, 4) if value is not None else None

    @staticmethod
    def _bounded_vocabulary(vocabulary: list[str]) -> list[str]:
        terms: list[str] = []
        seen: set[str] = set()
        for value in vocabulary:
            term = " ".join(value.split()).strip()
            key = term.casefold()
            if not term or len(term) > 80 or key in seen:
                continue
            seen.add(key)
            terms.append(term)
            if len(terms) == 15:
                break
        return terms

    def _prompt_with_vocabulary(self, vocabulary: list[str]) -> str | None:
        if not vocabulary:
            return self.initial_prompt
        dynamic_prompt = ", ".join(vocabulary)
        return (
            f"{self.initial_prompt}; {dynamic_prompt}"
            if self.initial_prompt
            else dynamic_prompt
        )

    @staticmethod
    def _normalize_level(audio, peak: float, rms: float):
        """Raise quiet microphone input toward -24 dBFS without clipping."""
        if rms < 1e-6 or peak < 1e-6:
            return audio, 1.0
        target_rms = 10 ** (-24 / 20)
        gain = min(target_rms / rms, 0.95 / peak, 10.0)
        if 0.95 <= gain <= 1.05:
            return audio, 1.0
        return audio * gain, gain
