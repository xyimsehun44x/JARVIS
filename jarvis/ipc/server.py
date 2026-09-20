from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any, TextIO

from pydantic import ValidationError

from jarvis.config import Settings
from jarvis.core.jarvis import Jarvis, TurnResult
from jarvis.core.session import SessionCoordinator
from jarvis.ipc.protocol import IpcError, IpcEvent, IpcRequest, IpcResponse
from jarvis.ipc.voice import DesktopVoiceRuntime, DesktopVoiceTurn


class StdioIpcServer:
    """Serve a private JSONL protocol over an owning desktop process's stdio pipes."""

    def __init__(
        self,
        session: SessionCoordinator,
        *,
        input_stream: TextIO = sys.stdin,
        output_stream: TextIO = sys.stdout,
        voice_runtime: DesktopVoiceRuntime | None = None,
    ) -> None:
        self.session = session
        self.input_stream = input_stream
        self.output_stream = output_stream
        self._voice = voice_runtime
        self._voice_available = DesktopVoiceRuntime.dependencies_available()
        self._stopping = False

    def preload_voice(self) -> None:
        """Warm voice models before advertising desktop readiness."""
        if self._voice is None:
            if not self._voice_available:
                return
            self._voice = DesktopVoiceRuntime(
                self.session,
                self.session.jarvis.settings,
                self._emit_event,
            )
        try:
            self._voice.preload()
        except Exception as exc:
            print(
                f"Desktop voice preload failed: {type(exc).__name__}: {exc}",
                file=sys.stderr,
                flush=True,
            )
            self._voice.close()
            self._voice = None
            self._voice_available = False

    def serve(self) -> None:
        self._emit_event("ready", payload=self._health_result())
        try:
            for line in self.input_stream:
                if self._stopping:
                    break
                if not line.strip():
                    continue
                request_id = self._request_id_from_line(line)
                try:
                    request = IpcRequest.model_validate_json(line)
                except (ValidationError, ValueError):
                    self._emit_error(
                        request_id,
                        code="invalid_request",
                        message="Request does not match IPC protocol version 1.",
                    )
                    continue
                self._handle(request)
        finally:
            if self._voice is not None:
                self._voice.close()

    def _handle(self, request: IpcRequest) -> None:
        try:
            if request.method == "health":
                self._require_no_params(request)
                result = self._health_result()
            elif request.method == "turn":
                result = self._turn(request)
            elif request.method == "voice.start":
                result = self._voice_start(request)
            elif request.method == "voice.stop":
                result = self._voice_stop(request)
            elif request.method == "voice.cancel":
                self._require_no_params(request)
                if self._voice is None:
                    result = {"status": "cancelled"}
                else:
                    result = self._voice.cancel(request_id=request.request_id)
            else:
                self._require_no_params(request)
                result = {"status": "shutting_down"}
                self._stopping = True
        except ValueError as exc:
            self._emit_error(
                request.request_id,
                code="invalid_params",
                message=str(exc),
            )
            return
        except Exception:
            self._emit_error(
                request.request_id,
                code="internal_error",
                message="Jarvis could not complete the request.",
            )
            return
        self._emit(
            IpcResponse(
                request_id=request.request_id,
                ok=True,
                result=result,
            )
        )

    def _turn(self, request: IpcRequest) -> dict[str, Any]:
        allowed = {"text", "thread_id"}
        unexpected = set(request.params) - allowed
        if unexpected:
            raise ValueError(f"Unexpected turn parameters: {', '.join(sorted(unexpected))}")
        text = request.params.get("text")
        thread_id = request.params.get("thread_id", "desktop")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("turn.text must be a non-empty string")
        if not isinstance(thread_id, str) or not thread_id.strip():
            raise ValueError("turn.thread_id must be a non-empty string")

        def emit_delta(delta: str) -> None:
            self._emit_event(
                "assistant.delta",
                request_id=request.request_id,
                payload={"thread_id": thread_id, "delta": delta},
            )

        result = self.session.turn(
            text.strip(),
            thread_id=thread_id.strip(),
            on_response_delta=emit_delta,
        )
        return self._turn_result(result)

    def _voice_start(self, request: IpcRequest) -> dict[str, Any]:
        thread_id = self._thread_id(request)
        if not self._voice_available:
            raise RuntimeError("Desktop voice is unavailable")
        if self._voice is None:
            self._voice = DesktopVoiceRuntime(
                self.session,
                self.session.jarvis.settings,
                self._emit_event,
            )
        return self._voice.start(thread_id=thread_id, request_id=request.request_id)

    def _voice_stop(self, request: IpcRequest) -> dict[str, Any]:
        thread_id = self._thread_id(request)
        if self._voice is None:
            raise ValueError("Speech capture is not active")
        voice_turn: DesktopVoiceTurn = self._voice.stop(
            thread_id=thread_id,
            request_id=request.request_id,
        )
        return {
            **self._turn_result(voice_turn.result),
            "transcript": {
                "heard": voice_turn.heard,
                "understood": voice_turn.understood,
                "confidence": voice_turn.confidence,
                "quality": voice_turn.quality,
            },
        }

    @staticmethod
    def _thread_id(request: IpcRequest) -> str:
        unexpected = set(request.params) - {"thread_id"}
        if unexpected:
            raise ValueError(
                f"Unexpected {request.method} parameters: {', '.join(sorted(unexpected))}"
            )
        thread_id = request.params.get("thread_id", "desktop")
        if not isinstance(thread_id, str) or not thread_id.strip():
            raise ValueError(f"{request.method}.thread_id must be a non-empty string")
        return thread_id.strip()

    def _health_result(self) -> dict[str, Any]:
        settings = self.session.jarvis.settings
        llm_model = {
            "gemini": settings.gemini_model,
            "openai": settings.openai_model,
            "rules": "built-in rules",
        }.get(settings.llm_provider, "unknown")
        return {
            "status": "ready",
            "mode": settings.mode,
            "diagnostics": {
                "llm_provider": settings.llm_provider,
                "llm_model": llm_model,
                "stt_model": settings.stt_model,
            },
            "capabilities": {
                "text_turns": True,
                "streamed_text_events": True,
                "voice_push_to_talk": self._voice_available,
                "gmail_send": settings.allow_email_send,
                "calendar_writes": settings.allow_calendar_writes,
            },
        }

    @staticmethod
    def _turn_result(result: TurnResult) -> dict[str, Any]:
        return {
            "response": result.response,
            "needs_input": result.needs_input,
            "prompt": result.prompt,
            "interrupt_kind": result.interrupt_kind,
            "proposal": result.proposal,
            "trace_id": result.trace_id,
            "execution_tier": result.execution_tier,
            "tier_reason": result.tier_reason,
            "response_streamed": result.response_streamed,
        }

    @staticmethod
    def _require_no_params(request: IpcRequest) -> None:
        if request.params:
            raise ValueError(f"{request.method} does not accept parameters")

    @staticmethod
    def _request_id_from_line(line: str) -> str:
        try:
            value = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            return "unknown"
        request_id = value.get("request_id") if isinstance(value, dict) else None
        return request_id if isinstance(request_id, str) and request_id else "unknown"

    def _emit_error(self, request_id: str, *, code: str, message: str) -> None:
        self._emit(
            IpcResponse(
                request_id=request_id,
                ok=False,
                error=IpcError(code=code, message=message),
            )
        )

    def _emit_event(
        self,
        event: str,
        *,
        request_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self._emit(
            IpcEvent(
                event=event,
                request_id=request_id,
                payload=payload or {},
            )
        )

    def _emit(self, message: IpcResponse | IpcEvent) -> None:
        self.output_stream.write(message.model_dump_json(exclude_none=True) + "\n")
        self.output_stream.flush()


def run_stdio_server(
    settings: Settings,
    *,
    input_stream: TextIO = sys.stdin,
    output_stream: TextIO = sys.stdout,
    jarvis_factory: Callable[..., Jarvis] = Jarvis,
) -> None:
    """Build and own one Jarvis runtime for the lifetime of a desktop child process."""
    _configure_standard_streams(input_stream, output_stream)
    jarvis = jarvis_factory(settings=settings)
    # Desktop development always reports content-free per-stage timings to stderr.
    jarvis.latency.enabled = True
    try:
        session = SessionCoordinator(
            jarvis,
            speech_action_confidence_threshold=settings.stt_action_confidence_threshold,
        )
        server = StdioIpcServer(
            session,
            input_stream=input_stream,
            output_stream=output_stream,
        )
        if input_stream is sys.stdin and output_stream is sys.stdout:
            server.preload_voice()
        server.serve()
    finally:
        jarvis.close()


def _configure_standard_streams(input_stream: TextIO, output_stream: TextIO) -> None:
    """Keep JSONL transport encoding stable on Windows regardless of system locale."""
    if input_stream is sys.stdin and hasattr(input_stream, "reconfigure"):
        input_stream.reconfigure(encoding="utf-8", errors="strict")
    if output_stream is sys.stdout and hasattr(output_stream, "reconfigure"):
        output_stream.reconfigure(
            encoding="utf-8", errors="strict", line_buffering=True
        )
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(
                encoding="utf-8", errors="backslashreplace", line_buffering=True
            )
