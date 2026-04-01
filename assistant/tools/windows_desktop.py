"""Shared Win32 desktop helpers used by app, vision, and input runtimes."""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Any


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class POINT(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_long),
        ("y", ctypes.c_long),
    ]


def load_desktop_libraries() -> tuple[Any | None, Any | None, str]:
    if sys.platform != "win32":
        return None, None, "Desktop APIs are only available on Windows."
    try:
        return ctypes.windll.user32, ctypes.windll.kernel32, ""
    except Exception as exc:  # pragma: no cover - environment-specific
        return None, None, str(exc)


def get_foreground_window_handle(user32: Any) -> int:
    return int(user32.GetForegroundWindow())


def get_window_title(user32: Any, hwnd: int) -> str:
    length = int(user32.GetWindowTextLengthW(hwnd))
    buffer = ctypes.create_unicode_buffer(max(1, length + 1))
    user32.GetWindowTextW(hwnd, buffer, len(buffer))
    return buffer.value.strip()


def get_window_process_id(user32: Any, hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def query_process_path(kernel32: Any, pid: int) -> str:
    if pid <= 0 or kernel32 is None:
        return ""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buffer = ctypes.create_unicode_buffer(4096)
        size = wintypes.DWORD(len(buffer))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value
    finally:
        kernel32.CloseHandle(handle)
    return ""


def get_window_rect(user32: Any, hwnd: int) -> tuple[int, int, int, int]:
    rect = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise RuntimeError("Unable to locate the active window bounds.")
    return rect.left, rect.top, rect.right, rect.bottom


def build_window_snapshot(user32: Any, kernel32: Any, hwnd: int) -> dict[str, Any]:
    title = get_window_title(user32, hwnd)
    pid = get_window_process_id(user32, hwnd)
    executable_path = query_process_path(kernel32, pid)
    process_name = Path(executable_path).stem if executable_path else ""
    return {
        "window_id": str(hwnd),
        "title": title,
        "process_name": process_name,
        "executable_path": executable_path,
        "is_minimized": bool(user32.IsIconic(hwnd)),
        "is_visible": bool(user32.IsWindowVisible(hwnd)),
    }


def get_foreground_window_snapshot(user32: Any, kernel32: Any) -> dict[str, Any]:
    return build_window_snapshot(user32, kernel32, get_foreground_window_handle(user32))


def normalize_window_match_text(value: str) -> str:
    text = value.strip().strip("\"'").lower()
    return "".join(char for char in text if char.isalnum())
