from __future__ import annotations

import json
from threading import Event

from jarvis import Jarvis
from jarvis.config import Settings
from jarvis.core.latency import LatencyRecorder
from jarvis.core.jarvis import TurnResult
from jarvis.main import _finish_response_audio, build_parser
from jarvis.voice.tts import KokoroTTS


def test_latency_recorder_tracks_trace_metadata_and_duration() -> None:
    ticks = iter((10.0, 10.025))
    recorder = LatencyRecorder(enabled=True, clock=lambda: next(ticks))

    with recorder.trace("trace-1", thread_id="thread-1"):
        with recorder.measure("llm.respond", provider="fake"):
            pass

    events = recorder.events_since()
    assert len(events) == 1
    assert events[0].stage == "llm.respond"
    assert round(events[0].duration_ms) == 25
    assert events[0].trace_id == "trace-1"
    assert events[0].thread_id == "thread-1"
    assert events[0].metadata == {"provider": "fake"}


def test_latency_statistics_report_p50_and_nearest_rank_p95() -> None:
    recorder = LatencyRecorder(enabled=True)
    for duration in (10, 20, 30, 40, 100):
        recorder.record("routing", duration)

    stats = recorder.statistics()["routing"]

    assert stats.count == 5
    assert stats.p50_ms == 30
    assert stats.p95_ms == 100
    assert "routing=200ms" in recorder.format_events(recorder.events_since())
    assert "p50=30ms" in recorder.format_statistics()


def test_jarvis_records_conversation_and_tool_boundaries() -> None:
    recorder = LatencyRecorder(enabled=True)
    jarvis = Jarvis(settings=Settings(), latency_recorder=recorder)

    conversation = jarvis.turn("Hello", thread_id="latency-chat", trace_id="trace-chat")
    email = jarvis.turn(
        "Email Jisoo and say hello", thread_id="latency-email", trace_id="trace-email"
    )
    assert email.needs_input
    sent = jarvis.resume("send it", thread_id="latency-email", trace_id="trace-email")
    jarvis.close()

    assert conversation.trace_id == "trace-chat"
    assert sent.response == "Sent."
    stages = {event.stage for event in recorder.events_since()}
    assert {"routing", "llm.respond", "tool.contacts.search", "tool.gmail.send"} <= stages
    assert sum(event.stage == "turn.total" for event in recorder.events_since()) == 3
    assert all(event.duration_ms >= 0 for event in recorder.events_since())
    assert all(
        event.trace_id == "trace-email"
        for event in recorder.events_since()
        if event.stage == "tool.gmail.send"
    )


def test_disabled_latency_recorder_collects_nothing() -> None:
    recorder = LatencyRecorder(enabled=False)
    with recorder.measure("routing"):
        pass
    assert recorder.events_since() == []


def test_cli_accepts_latency_flag() -> None:
    args = build_parser().parse_args(
        ["--latency", "--latency-report", "benchmarks/before.json"]
    )
    assert args.latency is True
    assert args.latency_report == "benchmarks/before.json"


def test_latency_logging_can_be_enabled_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_LATENCY_LOGGING", "true")
    assert Settings.from_env().latency_logging is True


def test_latency_report_is_sanitized_and_machine_readable(tmp_path) -> None:
    recorder = LatencyRecorder(enabled=True)
    with recorder.trace("trace-1", thread_id="thread-1"):
        recorder.record("stt.transcribe", 125.5, backend="fake")

    path = recorder.write_report(
        tmp_path / "voice.json",
        metadata={"stt_model": "base.en"},
    )
    report = json.loads(path.read_text(encoding="utf-8"))

    assert report["schema_version"] == 1
    assert report["metadata"] == {"stt_model": "base.en"}
    assert report["events"] == [
        {
            "stage": "stt.transcribe",
            "duration_ms": 125.5,
            "trace_id": "trace-1",
            "thread_id": "thread-1",
            "metadata": {"backend": "fake"},
        }
    ]
    assert report["statistics"]["stt.transcribe"] == {
        "count": 1,
        "p50_ms": 125.5,
        "p95_ms": 125.5,
        "max_ms": 125.5,
    }


def test_tts_splits_long_responses_and_does_not_speak_source_metadata() -> None:
    text = (
        "In Seoul, it's currently 28 degrees and clear. "
        "This deliberately long second sentence contains enough words to exceed the "
        "maximum chunk size and should be divided at a word boundary. "
        "Source: Open-Meteo, retrieved 2026-09-07 07:45 UTC."
    )

    chunks = KokoroTTS.speech_chunks(text, maximum_characters=60)

    assert chunks[0] == "In Seoul, it's currently 28 degrees and clear."
    assert all(len(chunk) <= 60 for chunk in chunks)
    assert "Source" not in " ".join(chunks)
    assert "Open-Meteo" not in " ".join(chunks)


def test_streaming_tts_buffers_sentences_and_overlaps_synthesis_with_playback() -> None:
    playback_started = Event()
    second_synthesized = Event()
    synthesized = []
    played = []

    class FakePipeline:
        def __call__(self, text, *, voice):
            del voice
            synthesized.append(text)
            if len(synthesized) == 2:
                assert playback_started.wait(timeout=1)
                second_synthesized.set()
            yield None, None, [len(synthesized)]

    class FakeSoundDevice:
        def play(self, audio, *, samplerate):
            assert samplerate == 24000
            played.append(audio.tolist())
            playback_started.set()

        def wait(self):
            if len(played) == 1:
                assert second_synthesized.wait(timeout=1)

    recorder = LatencyRecorder(enabled=True)
    tts = object.__new__(KokoroTTS)
    tts.pipeline = FakePipeline()
    tts.sd = FakeSoundDevice()
    tts.voice = "test"
    tts.latency = recorder

    with recorder.trace("tts-trace", thread_id="tts-thread"):
        speech = tts.start_stream()
        speech.write("First sentence. Second")
        assert synthesized == [] or synthesized == ["First sentence."]
        speech.write(" sentence.")
        speech.finish()

    assert synthesized == ["First sentence.", "Second sentence."]
    assert played == [[1], [2]]
    stages = {event.stage for event in recorder.events_since()}
    assert {
        "tts.first_chunk_ready",
        "tts.synthesis",
        "tts.playback",
        "tts.first_audio",
        "tts.total",
    } <= stages
    assert all(event.trace_id == "tts-trace" for event in recorder.events_since())
    assert all(event.thread_id == "tts-thread" for event in recorder.events_since())


def test_streaming_tts_keeps_a_short_voice_response_as_one_natural_utterance() -> None:
    synthesized = []

    class FakePipeline:
        def __call__(self, text, *, voice):
            del voice
            synthesized.append(text)
            yield None, None, [1]

    class FakeSoundDevice:
        def play(self, audio, *, samplerate):
            del audio, samplerate

        def wait(self):
            pass

    tts = object.__new__(KokoroTTS)
    tts.pipeline = FakePipeline()
    tts.sd = FakeSoundDevice()
    tts.voice = "test"
    tts.latency = LatencyRecorder()

    speech = tts.start_stream()
    speech.write(
        "Maintaining a consistent sleep schedule\u2014even on weekends\u2014regulates "
        "your circadian rhythm far more effectively than merely sleeping longer."
    )
    speech.finish()

    assert synthesized == [
        "Maintaining a consistent sleep schedule\u2014even on weekends\u2014regulates "
        "your circadian rhythm far more effectively than merely sleeping longer."
    ]


def test_streaming_tts_uses_one_continuous_audio_device_stream() -> None:
    synthesized = []
    opened = []
    written = []

    class FakePipeline:
        def __call__(self, text, *, voice):
            del voice
            synthesized.append(text)
            yield None, None, [len(synthesized)]

    class FakeOutputStream:
        def __init__(self, **kwargs) -> None:
            opened.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def write(self, audio) -> None:
            written.append(audio.tolist())

    class FakeSoundDevice:
        OutputStream = FakeOutputStream

    tts = object.__new__(KokoroTTS)
    tts.pipeline = FakePipeline()
    tts.sd = FakeSoundDevice()
    tts.voice = "test"
    tts.latency = LatencyRecorder()

    speech = tts.start_stream()
    speech.write("First sentence. Second sentence.")
    speech.finish()

    assert synthesized == ["First sentence.", "Second sentence."]
    assert opened == [{"samplerate": 24000, "channels": 1, "dtype": "float32"}]
    assert written == [[[1.0]], [[2.0]]]


def test_streaming_tts_does_not_flush_a_dangling_fragment() -> None:
    synthesized = []

    class FakePipeline:
        def __call__(self, text, *, voice):
            del voice
            synthesized.append(text)
            yield None, None, [1]

    class FakeSoundDevice:
        def play(self, audio, *, samplerate):
            del audio, samplerate

        def wait(self):
            pass

    recorder = LatencyRecorder(enabled=True)
    tts = object.__new__(KokoroTTS)
    tts.pipeline = FakePipeline()
    tts.sd = FakeSoundDevice()
    tts.voice = "test"
    tts.latency = recorder

    speech = tts.start_stream()
    speech.write("I am sorry to hear that. Would")
    speech.finish()

    assert synthesized == ["I am sorry to hear that."]
    assert "tts.incomplete_tail_discarded" in {
        event.stage for event in recorder.events_since()
    }


def test_nonstreamed_voice_recovery_aborts_partial_audio_before_speaking() -> None:
    class FakeSpeech:
        def __init__(self) -> None:
            self.finished = False
            self.aborted = False

        def finish(self) -> None:
            self.finished = True

        def abort(self) -> None:
            self.aborted = True

    class FakeTTS:
        def __init__(self) -> None:
            self.spoken = []

        def speak(self, text, *, response_started_at):
            self.spoken.append((text, response_started_at))

    speech = FakeSpeech()
    tts = FakeTTS()
    result = TurnResult(response="Please ask me again.", response_streamed=False)

    _finish_response_audio(
        result,
        speech,
        tts,
        result.response,
        response_started_at=12.0,
    )

    assert speech.aborted is True
    assert speech.finished is False
    assert tts.spoken == [("Please ask me again.", 12.0)]
