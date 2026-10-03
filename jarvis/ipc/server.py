from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any, TextIO

from pydantic import ValidationError

from jarvis.config import Settings
from jarvis.core.jarvis import Jarvis, TurnResult
from jarvis.core.session import SessionCoordinator
from jarvis.ipc.protocol import (
    IpcError,
    IpcEvent,
    IpcRequest,
    IpcResponse,
    MemoryCorrectParams,
    MemoryListParams,
    MemoryMutationParams,
)
from jarvis.ipc.voice import DesktopVoiceRuntime, DesktopVoiceTurn
from jarvis.memory.intents import classify_memory_sensitivity
from jarvis.memory.long_term import (
    MemoryEntry,
    MemorySensitivity,
    MemoryStatus,
    is_restricted_memory_material,
)


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
            elif request.method == "memory.list":
                result = self._memory_list(request)
            elif request.method == "memory.correct":
                result = self._memory_correct(request)
            elif request.method == "memory.forget":
                result = self._memory_forget(request)
            elif request.method == "memory.restore":
                result = self._memory_restore(request)
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
        except Exception as exc:
            print(
                f"Desktop IPC {request.method} failed: {type(exc).__name__}",
                file=sys.stderr,
                flush=True,
            )
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

    def _memory_list(self, request: IpcRequest) -> dict[str, Any]:
        MemoryListParams.model_validate(request.params)
        entries = [
            entry
            for entry in self.session.jarvis.memory.list_all(include_sensitive=True)
            if not is_restricted_memory_material(entry.key, entry.value)
        ]
        return {
            "active": [
                self._memory_record(entry)
                for entry in entries
                if entry.status is MemoryStatus.ACTIVE
            ],
            "history": [
                self._memory_record(entry)
                for entry in entries
                if entry.status is not MemoryStatus.ACTIVE
            ],
        }

    def _memory_correct(self, request: IpcRequest) -> dict[str, Any]:
        params = MemoryCorrectParams.model_validate(request.params)
        value = params.value.strip()
        if not value:
            raise ValueError("memory.correct.value must not be blank")
        old = self._managed_memory(params.memory_id, required_status=MemoryStatus.ACTIVE)
        inferred = classify_memory_sensitivity(f"{old.key} {value}")
        sensitivity = (
            MemorySensitivity.SENSITIVE
            if old.sensitivity is MemorySensitivity.SENSITIVE
            or inferred is MemorySensitivity.SENSITIVE
            else MemorySensitivity.NORMAL
        )
        replacement = self.session.jarvis.memory.correct(
            old.key,
            value,
            reason="explicit desktop correction",
            explicit=True,
            kind=old.kind,
            provenance="explicit_desktop_correction",
            sensitivity=sensitivity,
        )
        return {
            "old": self._memory_record(old),
            "new": self._memory_record(replacement),
        }

    def _memory_forget(self, request: IpcRequest) -> dict[str, Any]:
        params = MemoryMutationParams.model_validate(request.params)
        entry = self._managed_memory(
            params.memory_id, required_status=MemoryStatus.ACTIVE
        )
        if not self.session.jarvis.memory.forget(
            entry.key,
            reason="explicit desktop forget",
            explicit=True,
        ):
            raise ValueError("The memory is no longer active")
        forgotten = self.session.jarvis.memory.get_by_id(entry.memory_id)
        if forgotten is None:
            raise RuntimeError("Forgotten memory could not be loaded")
        return {"memory": self._memory_record(forgotten)}

    def _memory_restore(self, request: IpcRequest) -> dict[str, Any]:
        params = MemoryMutationParams.model_validate(request.params)
        entry = self._managed_memory(
            params.memory_id, required_status=MemoryStatus.FORGOTTEN
        )
        restored = self.session.jarvis.memory.restore(
            entry.memory_id,
            reason="explicit desktop restoration",
            explicit=True,
        )
        return {"memory": self._memory_record(restored)}

    def _managed_memory(
        self,
        memory_id: str,
        *,
        required_status: MemoryStatus,
    ) -> MemoryEntry:
        entry = self.session.jarvis.memory.get_by_id(memory_id)
        if entry is None:
            raise ValueError("Memory record was not found")
        if is_restricted_memory_material(entry.key, entry.value):
            raise ValueError("Credential-like memory cannot be managed in the desktop UI")
        if entry.status is not required_status:
            raise ValueError(f"Memory record must be {required_status.value}")
        return entry

    @staticmethod
    def _memory_record(entry: MemoryEntry) -> dict[str, Any]:
        return entry.model_dump(mode="json")

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
