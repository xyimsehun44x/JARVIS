from types import SimpleNamespace

import pytest

from jarvis.core.latency import LatencyRecorder
from jarvis.voice.stt import PushToTalkSTT, STTResult


def test_capture_validation_rejects_only_obviously_invalid_audio() -> None:
    validate = PushToTalkSTT._validate_capture

    assert validate(audio_duration_ms=100, peak=0.2, rms=0.05, clipped_fraction=0) == "too_short"
    assert validate(audio_duration_ms=1000, peak=0, rms=0, clipped_fraction=0) == "silence"
    assert (
        validate(audio_duration_ms=1000, peak=1, rms=0.5, clipped_fraction=0.2)
        == "excessive_clipping"
    )
    assert validate(audio_duration_ms=1000, peak=0.08, rms=0.01, clipped_fraction=0) is None


def test_segment_quality_is_duration_weighted_and_bounded() -> None:
    segments = [
        SimpleNamespace(start=0, end=1, avg_logprob=-0.2, no_speech_prob=0.1),
        SimpleNamespace(start=1, end=3, avg_logprob=-0.5, no_speech_prob=0.2),
    ]

    average_log_probability, no_speech_probability = PushToTalkSTT._segment_quality(
        segments
    )
    confidence = PushToTalkSTT._confidence(
        average_log_probability, no_speech_probability
    )

    assert average_log_probability == pytest.approx(-0.4)
    assert no_speech_probability == pytest.approx(1 / 6)
    assert confidence == pytest.approx(0.5586, abs=0.001)
    assert 0 <= confidence <= 1


def test_stt_result_exposes_validity_without_hiding_quality_details() -> None:
    result = STTResult(
        text="Hello",
        confidence=0.8,
        quality="high",
        rejection_reason=None,
        audio_duration_ms=1000,
        input_peak=0.1,
        input_rms_dbfs=-30,
        input_gain_db=6,
        clipped_fraction=0,
    )

    assert result.is_valid
    assert result.quality == "high"


def _capture_ready_stt():
    np = pytest.importorskip("numpy")
    streams = []

    class FakeStream:
        def __init__(self, *, callback, **kwargs) -> None:
            del kwargs
            self.callback = callback
            self.started = False
            self.stopped = False
            self.closed = False
            streams.append(self)

        def start(self) -> None:
            self.started = True
            self.callback(np.full((8000, 1), 0.05, dtype=np.float32))

        def stop(self) -> None:
            self.stopped = True

        def close(self) -> None:
            self.closed = True

    class FakeModel:
        def transcribe(self, audio, **kwargs):
            assert audio.shape == (8000,)
            assert kwargs["initial_prompt"] == "Jarvis; OmegaETH"
            return iter(
                [
                    SimpleNamespace(
                        text=" Hello, Jarvis.",
                        start=0,
                        end=0.5,
                        avg_logprob=-0.1,
                        no_speech_prob=0.02,
                    )
                ]
            ), None

    stt = PushToTalkSTT.__new__(PushToTalkSTT)
    stt.sd = SimpleNamespace(InputStream=FakeStream)
    stt.sample_rate = 16000
    stt.beam_size = 5
    stt.hotwords = "Jarvis"
    stt.initial_prompt = "Jarvis"
    stt.vad_filter = True
    stt.latency = LatencyRecorder()
    stt.last_capture_ended_at = None
    stt._capture_stream = None
    stt._capture_frames = None
    stt._capture_started_at = None
    stt.model = FakeModel()
    return stt, streams


def test_explicit_capture_start_and_stop_support_desktop_push_to_talk() -> None:
    stt, streams = _capture_ready_stt()

    stt.start_capture()

    assert stt.is_capturing
    assert streams[0].started

    result = stt.stop_capture(vocabulary=["OmegaETH"])

    assert result.text == "Hello, Jarvis."
    assert result.is_valid
    assert not stt.is_capturing
    assert streams[0].stopped
    assert streams[0].closed
    assert stt.last_capture_ended_at is not None


def test_capture_state_rejects_double_start_and_cancel_is_idempotent() -> None:
    stt, streams = _capture_ready_stt()

    with pytest.raises(RuntimeError, match="not active"):
        stt.stop_capture()

    stt.start_capture()
    with pytest.raises(RuntimeError, match="already active"):
        stt.start_capture()

    stt.cancel_capture()
    stt.cancel_capture()

    assert not stt.is_capturing
    assert streams[0].stopped
    assert streams[0].closed
