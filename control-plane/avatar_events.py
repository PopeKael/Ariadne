"""Best-effort Ariadne Host avatar events.

The Python core remains authoritative. These events are presentation hints;
failure to reach the optional Rust host must never affect an AI request.
"""
from __future__ import annotations

import ctypes
import json
import os
from typing import Any


PIPE_NAME = r"\\.\pipe\ariadne-control"
PROTOCOL_VERSION = 1
CANONICAL_AVATAR_STATES = (
    "idle", "listening", "thinking", "searching_vault", "reading",
    "cross_referencing", "loading_model", "working", "speaking", "waiting",
    "success", "warning", "confused", "recovering", "error", "offline",
)
AVATAR_STATES = frozenset(CANONICAL_AVATAR_STATES)


def _write_windows_pipe(payload: bytes) -> bool:
    if os.name != "nt":
        return False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.WaitNamedPipeW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
    kernel32.WaitNamedPipeW.restype = ctypes.c_bool
    kernel32.CreateFileW.argtypes = [
        ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
        ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
    ]
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.WriteFile.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p,
    ]
    kernel32.WriteFile.restype = ctypes.c_bool
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_bool

    if not kernel32.WaitNamedPipeW(PIPE_NAME, 100):
        return False
    handle = kernel32.CreateFileW(
        PIPE_NAME,
        0x40000000,  # GENERIC_WRITE
        0,
        None,
        3,  # OPEN_EXISTING
        0,
        None,
    )
    if not handle or handle == ctypes.c_void_p(-1).value:
        return False
    try:
        buffer = ctypes.create_string_buffer(payload)
        written = ctypes.c_uint32()
        return bool(kernel32.WriteFile(handle, buffer, len(payload), ctypes.byref(written), None)) and written.value == len(payload)
    finally:
        kernel32.CloseHandle(handle)


def emit(message_type: str, **fields: Any) -> bool:
    """Send one v1 event, returning False when the host is unavailable."""
    payload = {"v": PROTOCOL_VERSION, "type": str(message_type)}
    payload.update(fields)
    try:
        encoded = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        return _write_windows_pipe(encoded)
    except (OSError, TypeError, ValueError):
        return False


def emit_state(state: str) -> bool:
    if state not in AVATAR_STATES:
        return False
    return emit("state", state=state)


def reload_avatar() -> bool:
    return emit("reload_avatar")


def emit_say(text: str) -> bool:
    if not isinstance(text, str):
        return False
    return emit("say", text=text[:500])


def show() -> bool:
    return emit("show")


def hide() -> bool:
    return emit("hide")


def move(x: int, y: int) -> bool:
    if isinstance(x, bool) or isinstance(y, bool):
        return False
    return emit("move", x=int(x), y=int(y))


__all__ = [
    "AVATAR_STATES", "CANONICAL_AVATAR_STATES", "PIPE_NAME", "emit", "emit_state",
    "emit_say", "reload_avatar", "show", "hide", "move",
]
