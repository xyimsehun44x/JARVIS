from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


IPC_PROTOCOL_VERSION = 1


class IpcRequest(BaseModel):
    """One newline-delimited request sent by the desktop process."""

    model_config = ConfigDict(extra="forbid")

    protocol_version: Literal[1]
    request_id: str = Field(min_length=1, max_length=128)
    method: Literal[
        "health",
        "turn",
        "voice.start",
        "voice.stop",
        "voice.cancel",
        "memory.list",
        "memory.correct",
        "memory.forget",
        "memory.restore",
        "shutdown",
    ]
    params: dict[str, Any] = Field(default_factory=dict)


class MemoryListParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MemoryMutationParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_id: str = Field(min_length=1, max_length=128)


class MemoryCorrectParams(MemoryMutationParams):
    value: str = Field(min_length=1, max_length=4000)


class IpcError(BaseModel):
    code: str
    message: str


class IpcResponse(BaseModel):
    """Terminal response for exactly one request."""

    protocol_version: Literal[1] = IPC_PROTOCOL_VERSION
    type: Literal["response"] = "response"
    request_id: str
    ok: bool
    result: dict[str, Any] | None = None
    error: IpcError | None = None


class IpcEvent(BaseModel):
    """Unsolicited or in-progress event emitted by the backend."""

    protocol_version: Literal[1] = IPC_PROTOCOL_VERSION
    type: Literal["event"] = "event"
    event: Literal[
        "ready",
        "assistant.delta",
        "voice.state",
        "voice.transcript",
    ]
    request_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
