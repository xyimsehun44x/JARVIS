"""Versioned local transport for desktop clients."""

from jarvis.ipc.protocol import IPC_PROTOCOL_VERSION, IpcEvent, IpcRequest, IpcResponse
from jarvis.ipc.server import StdioIpcServer, run_stdio_server

__all__ = [
    "IPC_PROTOCOL_VERSION",
    "IpcEvent",
    "IpcRequest",
    "IpcResponse",
    "StdioIpcServer",
    "run_stdio_server",
]
