from __future__ import annotations

import json
import sqlite3
from io import StringIO

from jarvis.config import Settings
from jarvis.core.jarvis import Jarvis, TurnResult
from jarvis.core.session import SessionCoordinator
from jarvis.ipc.protocol import IPC_PROTOCOL_VERSION
from jarvis.ipc.server import StdioIpcServer, run_stdio_server
from jarvis.ipc.voice import DesktopVoiceRuntime, DesktopVoiceTurn
from jarvis.voice.normalizer import SpeechNormalizer
from jarvis.voice.stt import STTResult


def _request(request_id: str, method: str, params: dict | None = None) -> str:
    return json.dumps(
        {
            "protocol_version": IPC_PROTOCOL_VERSION,
            "request_id": request_id,
            "method": method,
            "params": params or {},
        }
    )


def _messages(output: StringIO) -> list[dict]:
    return [json.loads(line) for line in output.getvalue().splitlines()]


def test_stdio_ipc_serves_ready_health_turn_and_shutdown() -> None:
    input_stream = StringIO(
        "\n".join(
            [
                _request("health-1", "health"),
                _request("turn-1", "turn", {"text": "Hello", "thread_id": "desk"}),
                _request("stop-1", "shutdown"),
                _request("ignored", "health"),
            ]
        )
    )
    output_stream = StringIO()
    jarvis = Jarvis(settings=Settings())
    try:
        StdioIpcServer(
            SessionCoordinator(jarvis),
            input_stream=input_stream,
            output_stream=output_stream,
        ).serve()
    finally:
        jarvis.close()

    messages = _messages(output_stream)
    assert messages[0]["type"] == "event"
    assert messages[0]["event"] == "ready"
    assert messages[0]["payload"]["capabilities"]["gmail_send"] is False
    assert messages[0]["payload"]["diagnostics"] == {
        "llm_provider": "rules",
        "llm_model": "built-in rules",
        "stt_model": "small.en",
    }
    assert messages[1]["request_id"] == "health-1"
    assert messages[1]["result"]["status"] == "ready"
    assert messages[2]["request_id"] == "turn-1"
    assert messages[2]["result"]["response"] == "Hello. What can I do for you?"
    assert messages[2]["result"]["execution_tier"] == 1
    assert messages[3]["request_id"] == "stop-1"
    assert messages[3]["result"]["status"] == "shutting_down"
    assert all(message.get("request_id") != "ignored" for message in messages)


def test_stdio_ipc_reports_invalid_requests_and_continues() -> None:
    input_stream = StringIO(
        "\n".join(
            [
                "not-json",
                json.dumps(
                    {
                        "protocol_version": 2,
                        "request_id": "wrong-version",
                        "method": "health",
                    }
                ),
                _request("bad-turn", "turn", {"text": ""}),
                _request("health-2", "health"),
            ]
        )
    )
    output_stream = StringIO()
    jarvis = Jarvis(settings=Settings())
    try:
        StdioIpcServer(
            SessionCoordinator(jarvis),
            input_stream=input_stream,
            output_stream=output_stream,
        ).serve()
    finally:
        jarvis.close()

    messages = _messages(output_stream)
    responses = [message for message in messages if message["type"] == "response"]
    assert responses[0]["error"]["code"] == "invalid_request"
    assert responses[0]["request_id"] == "unknown"
    assert responses[1]["error"]["code"] == "invalid_request"
    assert responses[1]["request_id"] == "wrong-version"
    assert responses[2]["error"]["code"] == "invalid_params"
    assert responses[3]["request_id"] == "health-2"
    assert responses[3]["ok"] is True


def test_stdio_ipc_manages_memory_by_record_id_with_exact_lifecycle_results() -> None:
    jarvis = Jarvis(settings=Settings())
    correct_target = jarvis.memory.remember(
        "meeting preference",
        "avoid Mondays",
        reason="explicit request",
        explicit=True,
    )
    forget_target = jarvis.memory.remember(
        "timezone",
        "Asia/Seoul",
        reason="explicit request",
        explicit=True,
    )
    restore_target = jarvis.memory.remember(
        "coffee preference",
        "flat white",
        reason="explicit request",
        explicit=True,
    )
    assert jarvis.memory.forget("coffee preference", explicit=True)
    secret_rejection_target = jarvis.memory.remember(
        "editor preference",
        "VS Code",
        reason="explicit request",
        explicit=True,
    )

    input_stream = StringIO(
        "\n".join(
            [
                _request("memory-list", "memory.list"),
                _request(
                    "memory-correct",
                    "memory.correct",
                    {
                        "memory_id": correct_target.memory_id,
                        "value": "prefer Tuesdays",
                    },
                ),
                _request(
                    "memory-forget",
                    "memory.forget",
                    {"memory_id": forget_target.memory_id},
                ),
                _request(
                    "memory-restore",
                    "memory.restore",
                    {"memory_id": restore_target.memory_id},
                ),
                _request(
                    "memory-secret",
                    "memory.correct",
                    {
                        "memory_id": secret_rejection_target.memory_id,
                        "value": "sk-abcdefghijklmnopqrstuvwxyz123456",
                    },
                ),
                _request(
                    "memory-password",
                    "memory.correct",
                    {
                        "memory_id": secret_rejection_target.memory_id,
                        "value": "my password is not-for-storage",
                    },
                ),
                _request("memory-bad-params", "memory.list", {"database_path": "x"}),
                _request("stop", "shutdown"),
            ]
        )
    )
    output_stream = StringIO()
    try:
        StdioIpcServer(
            SessionCoordinator(jarvis),
            input_stream=input_stream,
            output_stream=output_stream,
        ).serve()
    finally:
        jarvis.close()

    responses = {
        message["request_id"]: message
        for message in _messages(output_stream)
        if message["type"] == "response"
    }
    listed = responses["memory-list"]["result"]
    assert {entry["key"] for entry in listed["active"]} == {
        "meeting_preference",
        "timezone",
        "editor_preference",
    }
    assert [entry["key"] for entry in listed["history"]] == ["coffee_preference"]

    correction = responses["memory-correct"]["result"]
    assert correction["old"]["value"] == "avoid Mondays"
    assert correction["new"]["value"] == "prefer Tuesdays"
    assert correction["new"]["supersedes_id"] == correct_target.memory_id
    assert responses["memory-forget"]["result"]["memory"]["status"] == "forgotten"
    assert responses["memory-restore"]["result"]["memory"]["status"] == "active"
    assert responses["memory-secret"]["error"]["code"] == "invalid_params"
    assert responses["memory-password"]["error"]["code"] == "invalid_params"
    assert responses["memory-bad-params"]["error"]["code"] == "invalid_params"


def test_stdio_ipc_never_lists_credential_like_legacy_memory(tmp_path) -> None:
    database = tmp_path / "legacy-memory.db"
    connection = sqlite3.connect(database)
    connection.execute(
        """
        CREATE TABLE long_term_memory (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "INSERT INTO long_term_memory VALUES (?, ?, ?, ?)",
        ("api_key", "legacy-value", "legacy", "2026-01-02T00:00:00+00:00"),
    )
    connection.commit()
    connection.close()

    output_stream = StringIO()
    jarvis = Jarvis(
        settings=Settings(persistence="sqlite", database_path=str(database))
    )
    try:
        StdioIpcServer(
            SessionCoordinator(jarvis),
            input_stream=StringIO(_request("memory-list", "memory.list")),
            output_stream=output_stream,
        ).serve()
    finally:
        jarvis.close()

    response = next(
        message
        for message in _messages(output_stream)
        if message.get("request_id") == "memory-list"
    )
    assert response["result"] == {"active": [], "history": []}


def test_stdio_ipc_streams_desktop_text_without_requesting_voice_style() -> None:
    class StreamingModel:
        def __init__(self) -> None:
            self.voice_modes: list[bool] = []

        def respond(self, text, history):
            del text, history
            return "fallback"

        def respond_stream(self, text, history, *, voice_mode=True):
            del text, history
            self.voice_modes.append(voice_mode)
            yield "Desktop "
            yield "reply."

    model = StreamingModel()
    jarvis = Jarvis(settings=Settings(), conversation_model=model)
    output_stream = StringIO()
    try:
        StdioIpcServer(
            SessionCoordinator(jarvis),
            input_stream=StringIO(
                _request("turn-stream", "turn", {"text": "Hello", "thread_id": "desk"})
            ),
            output_stream=output_stream,
        ).serve()
    finally:
        jarvis.close()

    messages = _messages(output_stream)
    deltas = [
        message["payload"]["delta"]
        for message in messages
        if message.get("event") == "assistant.delta"
    ]
    response = next(
        message for message in messages if message.get("request_id") == "turn-stream" and message["type"] == "response"
    )
    assert model.voice_modes == [False]
    assert deltas == ["Desktop ", "reply."]
    assert response["result"]["response"] == "Desktop reply."
    assert response["result"]["response_streamed"] is True


def test_run_stdio_server_owns_and_closes_runtime() -> None:
    created: list[Jarvis] = []

    class RecordingJarvis(Jarvis):
        def close(self) -> None:
            self.was_closed = True
            super().close()

    def factory(**kwargs) -> RecordingJarvis:
        runtime = RecordingJarvis(**kwargs)
        runtime.was_closed = False
        created.append(runtime)
        return runtime

    output_stream = StringIO()
    run_stdio_server(
        Settings(),
        input_stream=StringIO(_request("stop", "shutdown")),
        output_stream=output_stream,
        jarvis_factory=factory,
    )

    assert created[0].was_closed is True
    assert _messages(output_stream)[-1]["result"]["status"] == "shutting_down"


def test_stdio_ipc_supports_desktop_push_to_talk_lifecycle() -> None:
    class FakeVoiceRuntime:
        def __init__(self) -> None:
            self.preloaded = False
            self.started = False
            self.closed = False

        def preload(self) -> None:
            self.preloaded = True

        def start(self, *, thread_id: str, request_id: str):
            assert request_id == "voice-start"
            self.started = True
            return {"status": "listening", "thread_id": thread_id}

        def stop(self, *, thread_id: str, request_id: str):
            assert self.started
            assert thread_id == "desk"
            assert request_id == "voice-stop"
            return DesktopVoiceTurn(
                result=TurnResult(
                    response="Good morning.",
                    execution_tier=1,
                    response_streamed=True,
                ),
                heard="Good mourning",
                understood="Good morning",
                confidence=0.81,
                quality="high",
            )

        def cancel(self, *, request_id: str):
            del request_id
            return {"status": "cancelled"}

        def close(self) -> None:
            self.closed = True

    voice = FakeVoiceRuntime()
    jarvis = Jarvis(settings=Settings())
    output_stream = StringIO()
    try:
        server = StdioIpcServer(
            SessionCoordinator(jarvis),
            input_stream=StringIO(
                "\n".join(
                    [
                        _request(
                            "voice-start", "voice.start", {"thread_id": "desk"}
                        ),
                        _request(
                            "voice-stop", "voice.stop", {"thread_id": "desk"}
                        ),
                        _request("shutdown", "shutdown"),
                    ]
                )
            ),
            output_stream=output_stream,
            voice_runtime=voice,
        )
        server.preload_voice()
        server.serve()
    finally:
        jarvis.close()

    responses = [
        message for message in _messages(output_stream) if message["type"] == "response"
    ]
    assert responses[0]["result"]["status"] == "listening"
    assert responses[1]["result"]["response"] == "Good morning."
    assert responses[1]["result"]["transcript"] == {
        "heard": "Good mourning",
        "understood": "Good morning",
        "confidence": 0.81,
        "quality": "high",
    }
    assert voice.closed is True
    assert voice.preloaded is True


def test_desktop_voice_runtime_uses_normalized_voice_session_and_streamed_tts() -> None:
    class StreamingModel:
        def __init__(self) -> None:
            self.voice_modes: list[bool] = []

        def respond(self, text, history):
            del text, history
            return "fallback"

        def respond_stream(self, text, history, *, voice_mode=True):
            del text, history
            self.voice_modes.append(voice_mode)
            yield "Good "
            yield "morning."

    class FakeSTT:
        def __init__(self) -> None:
            self.is_capturing = False
            self.last_capture_ended_at = 1.0

        def start_capture(self) -> None:
            self.is_capturing = True

        def stop_capture(self, *, vocabulary):
            assert vocabulary == ["OmegaETH"]
            self.is_capturing = False
            return STTResult(
                text="Hello",
                confidence=0.9,
                quality="high",
                rejection_reason=None,
                audio_duration_ms=800,
                input_peak=0.1,
                input_rms_dbfs=-25,
                input_gain_db=0,
                clipped_fraction=0,
            )

        def cancel_capture(self) -> None:
            self.is_capturing = False

    class FakeSpeech:
        def __init__(self) -> None:
            self.text = ""
            self.finished = False

        def write(self, delta: str) -> None:
            self.text += delta

        def finish(self) -> None:
            self.finished = True

        def abort(self) -> None:
            pass

    class FakeTTS:
        def __init__(self) -> None:
            self.speech = FakeSpeech()

        def start_stream(self, *, response_started_at):
            assert response_started_at == 1.0
            return self.speech

        def speak(self, text, *, response_started_at):
            raise AssertionError((text, response_started_at))

    class FakeVocabulary:
        def select_terms(self, context, *, limit):
            del context
            assert limit == 15
            return ["OmegaETH"]

        def close(self) -> None:
            pass

    events: list[tuple[str, str | None, dict]] = []

    def emit(event, *, request_id=None, payload=None):
        events.append((event, request_id, payload or {}))

    model = StreamingModel()
    jarvis = Jarvis(settings=Settings(), conversation_model=model)
    try:
        runtime = DesktopVoiceRuntime(
            SessionCoordinator(jarvis), jarvis.settings, emit
        )
        runtime.stt = FakeSTT()
        runtime.tts = FakeTTS()
        runtime.normalizer = SpeechNormalizer()
        runtime.vocabulary = FakeVocabulary()

        assert runtime.start(thread_id="desk", request_id="start")["status"] == "listening"
        voice_turn = runtime.stop(thread_id="desk", request_id="stop")

        assert voice_turn.heard == "Hello"
        assert voice_turn.understood == "Hello"
        assert voice_turn.result.response == "Good morning."
        assert model.voice_modes == [True]
        assert runtime.tts.speech.text == "Good morning."
        assert runtime.tts.speech.finished is True
        assert [event[0] for event in events] == [
            "voice.state",
            "voice.state",
            "voice.transcript",
            "voice.state",
            "assistant.delta",
            "voice.state",
            "assistant.delta",
            "voice.state",
        ]
    finally:
        jarvis.close()


def test_desktop_voice_runtime_publishes_nonstream_text_before_playback() -> None:
    class NonStreamingModel:
        def respond(self, text, history):
            del text, history
            return "Complete answer."

    class FakeSTT:
        def __init__(self) -> None:
            self.is_capturing = False
            self.last_capture_ended_at = 1.0

        def start_capture(self) -> None:
            self.is_capturing = True

        def stop_capture(self, *, vocabulary):
            assert vocabulary == []
            self.is_capturing = False
            return STTResult(
                text="Tell me something interesting",
                confidence=0.9,
                quality="high",
                rejection_reason=None,
                audio_duration_ms=800,
                input_peak=0.1,
                input_rms_dbfs=-25,
                input_gain_db=0,
                clipped_fraction=0,
            )

    class FakeSpeech:
        def abort(self) -> None:
            pass

    class FakeTTS:
        def start_stream(self, *, response_started_at):
            assert response_started_at == 1.0
            return FakeSpeech()

        def speak(self, text, *, response_started_at):
            assert response_started_at == 1.0
            order.append(("tts.speak", text))

    class FakeVocabulary:
        def select_terms(self, context, *, limit):
            del context, limit
            return []

        def close(self) -> None:
            pass

    order: list[tuple[str, object]] = []

    def emit(event, *, request_id=None, payload=None):
        del request_id
        order.append((event, payload or {}))

    jarvis = Jarvis(settings=Settings(), conversation_model=NonStreamingModel())
    try:
        runtime = DesktopVoiceRuntime(
            SessionCoordinator(jarvis), jarvis.settings, emit
        )
        runtime.stt = FakeSTT()
        runtime.tts = FakeTTS()
        runtime.normalizer = SpeechNormalizer()
        runtime.vocabulary = FakeVocabulary()

        runtime.start(thread_id="desk", request_id="start")
        voice_turn = runtime.stop(thread_id="desk", request_id="stop")

        assert voice_turn.result.response == "Complete answer."
        delta_index = order.index(
            (
                "assistant.delta",
                {
                    "thread_id": "desk",
                    "delta": "Complete answer.",
                    "replace": True,
                },
            )
        )
        speak_index = order.index(("tts.speak", "Complete answer."))
        assert delta_index < speak_index
    finally:
        jarvis.close()
