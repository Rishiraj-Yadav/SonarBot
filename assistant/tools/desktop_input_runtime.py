"""Windows keyboard, mouse, and clipboard control runtime."""

from __future__ import annotations

import ctypes
import re
import sys
from typing import Any

from assistant.tools.windows_desktop import (
    POINT,
    get_foreground_window_snapshot,
    get_window_rect,
    load_desktop_libraries,
    normalize_window_match_text,
)


INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_WHEEL = 0x0800
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
WHEEL_DELTA = 120
ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ULONG_PTR),
    ]


class INPUTUNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_ulong),
        ("union", INPUTUNION),
    ]


class DesktopInputRuntime:
    def __init__(self, config) -> None:
        self.config = config
        self.user32 = None
        self.kernel32 = None
        self._availability_error = ""
        if sys.platform == "win32":
            self.user32, self.kernel32, self._availability_error = load_desktop_libraries()

    def ensure_available(self) -> None:
        if not bool(getattr(self.config.desktop_input, "enabled", False)):
            raise RuntimeError("Desktop input is not enabled.")
        if sys.platform != "win32":
            raise RuntimeError("Desktop input is only available on Windows hosts.")
        if self.user32 is None or self.kernel32 is None:
            detail = f" ({self._availability_error})" if self._availability_error else ""
            raise RuntimeError(f"Desktop input is unavailable on this Windows host{detail}.")

    def mouse_position(self) -> dict[str, Any]:
        self.ensure_available()
        self._ensure_mouse_enabled()
        point = POINT()
        assert self.user32 is not None
        if not self.user32.GetCursorPos(ctypes.byref(point)):
            raise RuntimeError("Unable to read the current cursor position.")
        return {
            "x": int(point.x),
            "y": int(point.y),
            "coordinate_space": "screen",
            "active_window": self._active_window_snapshot(),
        }

    def move_mouse(
        self,
        *,
        x: int,
        y: int,
        coordinate_space: str = "screen",
        expected_window_title: str = "",
        expected_process_name: str = "",
    ) -> dict[str, Any]:
        self.ensure_available()
        self._ensure_mouse_enabled()
        target_x, target_y, active_window = self._resolve_coordinates(
            x,
            y,
            coordinate_space=coordinate_space,
            expected_window_title=expected_window_title,
            expected_process_name=expected_process_name,
        )
        assert self.user32 is not None
        if not self.user32.SetCursorPos(target_x, target_y):
            raise RuntimeError("Unable to move the mouse cursor.")
        return {
            "x": target_x,
            "y": target_y,
            "coordinate_space": coordinate_space,
            "active_window": active_window,
        }

    def click_mouse(
        self,
        *,
        x: int,
        y: int,
        coordinate_space: str = "screen",
        button: str = "left",
        count: int = 1,
        expected_window_title: str = "",
        expected_process_name: str = "",
    ) -> dict[str, Any]:
        self.ensure_available()
        self._ensure_mouse_enabled()
        normalized_button = button.strip().lower()
        if normalized_button not in {"left", "right"}:
            raise RuntimeError("Mouse button must be 'left' or 'right'.")
        normalized_count = max(1, min(int(count), 2))
        target_x, target_y, active_window = self._resolve_coordinates(
            x,
            y,
            coordinate_space=coordinate_space,
            expected_window_title=expected_window_title,
            expected_process_name=expected_process_name,
        )
        assert self.user32 is not None
        if not self.user32.SetCursorPos(target_x, target_y):
            raise RuntimeError("Unable to move the mouse cursor before clicking.")
        down_flag = MOUSEEVENTF_LEFTDOWN if normalized_button == "left" else MOUSEEVENTF_RIGHTDOWN
        up_flag = MOUSEEVENTF_LEFTUP if normalized_button == "left" else MOUSEEVENTF_RIGHTUP
        inputs: list[INPUT] = []
        for _ in range(normalized_count):
            inputs.append(self._mouse_input(down_flag))
            inputs.append(self._mouse_input(up_flag))
        self._send_inputs(inputs)
        return {
            "x": target_x,
            "y": target_y,
            "coordinate_space": coordinate_space,
            "button": normalized_button,
            "count": normalized_count,
            "active_window": active_window,
        }

    def scroll_mouse(
        self,
        *,
        direction: str,
        amount: int,
        expected_window_title: str = "",
        expected_process_name: str = "",
    ) -> dict[str, Any]:
        self.ensure_available()
        self._ensure_mouse_enabled()
        normalized_direction = direction.strip().lower()
        if normalized_direction not in {"up", "down"}:
            raise RuntimeError("Scroll direction must be 'up' or 'down'.")
        normalized_amount = max(1, min(int(amount), 20))
        active_window = self._guard_expected_window(expected_window_title, expected_process_name)
        wheel_delta = WHEEL_DELTA * normalized_amount * (1 if normalized_direction == "up" else -1)
        self._send_inputs([self._mouse_input(MOUSEEVENTF_WHEEL, mouse_data=wheel_delta & 0xFFFFFFFF)])
        return {
            "direction": normalized_direction,
            "amount": normalized_amount,
            "active_window": active_window,
        }

    def type_text(
        self,
        *,
        text: str,
        expected_window_title: str = "",
        expected_process_name: str = "",
    ) -> dict[str, Any]:
        self.ensure_available()
        self._ensure_keyboard_enabled()
        normalized_text = text
        max_chars = max(1, int(getattr(self.config.desktop_input, "max_type_chars", 500)))
        if len(normalized_text) > max_chars:
            raise RuntimeError(f"Typing is limited to {max_chars} characters per action.")
        active_window = self._guard_expected_window(expected_window_title, expected_process_name)
        inputs: list[INPUT] = []
        for unit in self._utf16_units(normalized_text):
            inputs.append(self._keyboard_input(0, unit, KEYEVENTF_UNICODE))
            inputs.append(self._keyboard_input(0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
        self._send_inputs(inputs)
        return {
            "characters_typed": len(normalized_text),
            "active_window": active_window,
        }

    def press_hotkey(
        self,
        *,
        hotkey: str,
        expected_window_title: str = "",
        expected_process_name: str = "",
    ) -> dict[str, Any]:
        self.ensure_available()
        self._ensure_keyboard_enabled()
        tokens = self.parse_hotkey_tokens(hotkey)
        active_window = self._guard_expected_window(expected_window_title, expected_process_name)
        modifier_keys = {"ctrl", "shift", "alt", "win"}
        modifiers = [token for token in tokens if token in modifier_keys]
        non_modifiers = [token for token in tokens if token not in modifier_keys]
        if not non_modifiers:
            raise RuntimeError("Hotkeys must include at least one non-modifier key.")
        inputs: list[INPUT] = []
        for token in modifiers:
            inputs.append(self._keyboard_input(self._virtual_key(token), 0, 0))
        for token in non_modifiers:
            vk = self._virtual_key(token)
            inputs.append(self._keyboard_input(vk, 0, 0))
            inputs.append(self._keyboard_input(vk, 0, KEYEVENTF_KEYUP))
        for token in reversed(modifiers):
            inputs.append(self._keyboard_input(self._virtual_key(token), 0, KEYEVENTF_KEYUP))
        self._send_inputs(inputs)
        return {
            "hotkey": self.normalize_hotkey(hotkey),
            "active_window": active_window,
        }

    def read_clipboard(self) -> dict[str, Any]:
        self.ensure_available()
        self._ensure_clipboard_enabled()
        assert self.user32 is not None
        assert self.kernel32 is not None
        if not self.user32.OpenClipboard(None):
            raise RuntimeError("Unable to open the clipboard.")
        try:
            handle = self.user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return {"content": "", "char_count": 0, "line_count": 0}
            pointer = self.kernel32.GlobalLock(handle)
            if not pointer:
                raise RuntimeError("Unable to lock the clipboard text.")
            try:
                text = ctypes.wstring_at(pointer)
            finally:
                self.kernel32.GlobalUnlock(handle)
        finally:
            self.user32.CloseClipboard()
        normalized = text.replace("\r\n", "\n")
        return {
            "content": normalized,
            "char_count": len(normalized),
            "line_count": len(normalized.splitlines()),
        }

    def write_clipboard(self, *, text: str) -> dict[str, Any]:
        self.ensure_available()
        self._ensure_clipboard_enabled()
        assert self.user32 is not None
        assert self.kernel32 is not None
        normalized = text.replace("\r\n", "\n").replace("\n", "\r\n")
        buffer = ctypes.create_unicode_buffer(normalized)
        size = ctypes.sizeof(buffer)
        if not self.user32.OpenClipboard(None):
            raise RuntimeError("Unable to open the clipboard.")
        handle = None
        try:
            self.user32.EmptyClipboard()
            handle = self.kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
            if not handle:
                raise RuntimeError("Unable to allocate clipboard memory.")
            locked = self.kernel32.GlobalLock(handle)
            if not locked:
                raise RuntimeError("Unable to lock clipboard memory.")
            try:
                ctypes.memmove(locked, buffer, size)
            finally:
                self.kernel32.GlobalUnlock(handle)
            if not self.user32.SetClipboardData(CF_UNICODETEXT, handle):
                raise RuntimeError("Unable to write text into the clipboard.")
            handle = None
        finally:
            if handle:
                self.kernel32.GlobalFree(handle)
            self.user32.CloseClipboard()
        return {"char_count": len(text)}

    def is_safe_hotkey(self, hotkey: str) -> bool:
        normalized = self.normalize_hotkey(hotkey)
        safe_hotkeys = {self.normalize_hotkey(item) for item in getattr(self.config.desktop_input, "safe_hotkeys", [])}
        return normalized in safe_hotkeys

    @staticmethod
    def normalize_hotkey(hotkey: str) -> str:
        normalized = hotkey.strip().strip("\"'").lower()
        replacements = {
            "control": "ctrl",
            "escape": "esc",
            "page up": "pageup",
            "page down": "pagedown",
            "return": "enter",
            "windows": "win",
            "command": "win",
            "option": "alt",
        }
        for source, target in replacements.items():
            normalized = normalized.replace(source, target)
        normalized = normalized.replace("-", "+")
        normalized = re.sub(r"\s*\+\s*", "+", normalized)
        tokens = [token for token in re.split(r"[+\s]+", normalized) if token]
        return "+".join(tokens)

    @classmethod
    def parse_hotkey_tokens(cls, hotkey: str) -> list[str]:
        normalized = cls.normalize_hotkey(hotkey)
        tokens = [token for token in normalized.split("+") if token]
        if not tokens:
            raise RuntimeError("Please provide a hotkey to press.")
        return tokens

    def _resolve_coordinates(
        self,
        x: int,
        y: int,
        *,
        coordinate_space: str,
        expected_window_title: str,
        expected_process_name: str,
    ) -> tuple[int, int, dict[str, Any]]:
        normalized_space = coordinate_space.strip().lower()
        if normalized_space not in {"screen", "active_window"}:
            raise RuntimeError("Coordinate space must be 'screen' or 'active_window'.")
        active_window = self._guard_expected_window(expected_window_title, expected_process_name)
        target_x = int(x)
        target_y = int(y)
        if normalized_space == "screen":
            if not bool(getattr(self.config.desktop_input, "allow_absolute_coordinates", True)):
                raise RuntimeError("Absolute screen coordinates are disabled in desktop_input.allow_absolute_coordinates.")
        else:
            assert self.user32 is not None
            left, top, _right, _bottom = get_window_rect(self.user32, int(active_window["window_id"]))
            target_x = left + target_x
            target_y = top + target_y
        return target_x, target_y, active_window

    def _guard_expected_window(self, expected_window_title: str, expected_process_name: str) -> dict[str, Any]:
        active_window = self._active_window_snapshot()
        if expected_window_title:
            expected_title = normalize_window_match_text(expected_window_title)
            active_title = normalize_window_match_text(str(active_window.get("title", "")))
            if expected_title not in active_title:
                raise RuntimeError(
                    f"Desktop input expected the active window title to include '{expected_window_title}', "
                    f"but the active window is '{active_window.get('title', 'Unknown')}'."
                )
        if expected_process_name:
            expected_process = normalize_window_match_text(expected_process_name)
            active_process = normalize_window_match_text(str(active_window.get("process_name", "")))
            if expected_process not in active_process:
                raise RuntimeError(
                    f"Desktop input expected the active process to include '{expected_process_name}', "
                    f"but the active process is '{active_window.get('process_name', 'Unknown')}'."
                )
        return active_window

    def _active_window_snapshot(self) -> dict[str, Any]:
        assert self.user32 is not None
        assert self.kernel32 is not None
        return get_foreground_window_snapshot(self.user32, self.kernel32)

    def _send_inputs(self, items: list[INPUT]) -> None:
        if not items:
            return
        assert self.user32 is not None
        array_type = INPUT * len(items)
        array = array_type(*items)
        sent = int(self.user32.SendInput(len(items), array, ctypes.sizeof(INPUT)))
        if sent != len(items):
            raise RuntimeError("Windows rejected one or more desktop input events.")

    def _mouse_input(self, flags: int, mouse_data: int = 0) -> INPUT:
        return INPUT(
            type=INPUT_MOUSE,
            union=INPUTUNION(
                mi=MOUSEINPUT(
                    dx=0,
                    dy=0,
                    mouseData=int(mouse_data),
                    dwFlags=flags,
                    time=0,
                    dwExtraInfo=0,
                )
            ),
        )

    def _keyboard_input(self, vk: int, scan: int, flags: int) -> INPUT:
        return INPUT(
            type=INPUT_KEYBOARD,
            union=INPUTUNION(
                ki=KEYBDINPUT(
                    wVk=int(vk),
                    wScan=int(scan),
                    dwFlags=flags,
                    time=0,
                    dwExtraInfo=0,
                )
            ),
        )

    def _virtual_key(self, token: str) -> int:
        mapping = {
            "ctrl": 0x11,
            "shift": 0x10,
            "alt": 0x12,
            "win": 0x5B,
            "tab": 0x09,
            "esc": 0x1B,
            "enter": 0x0D,
            "backspace": 0x08,
            "delete": 0x2E,
            "insert": 0x2D,
            "space": 0x20,
            "up": 0x26,
            "down": 0x28,
            "left": 0x25,
            "right": 0x27,
            "home": 0x24,
            "end": 0x23,
            "pageup": 0x21,
            "pagedown": 0x22,
        }
        if token in mapping:
            return mapping[token]
        if len(token) == 1 and token.isalpha():
            return ord(token.upper())
        if len(token) == 1 and token.isdigit():
            return ord(token)
        if re.fullmatch(r"f([1-9]|1[0-2])", token):
            return 0x70 + int(token[1:]) - 1
        raise RuntimeError(f"Unsupported hotkey token '{token}'.")

    def _ensure_mouse_enabled(self) -> None:
        if not bool(getattr(self.config.desktop_input, "mouse_enabled", True)):
            raise RuntimeError("Mouse control is disabled in desktop_input.mouse_enabled.")

    def _ensure_keyboard_enabled(self) -> None:
        if not bool(getattr(self.config.desktop_input, "keyboard_enabled", True)):
            raise RuntimeError("Keyboard control is disabled in desktop_input.keyboard_enabled.")

    def _ensure_clipboard_enabled(self) -> None:
        if not bool(getattr(self.config.desktop_input, "clipboard_enabled", True)):
            raise RuntimeError("Clipboard control is disabled in desktop_input.clipboard_enabled.")

    @staticmethod
    def _utf16_units(text: str) -> list[int]:
        encoded = text.encode("utf-16-le")
        return [int.from_bytes(encoded[index : index + 2], "little") for index in range(0, len(encoded), 2)]
