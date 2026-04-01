from __future__ import annotations

import pytest

from assistant.tools.desktop_input_runtime import DesktopInputRuntime


def test_desktop_input_runtime_normalizes_hotkeys() -> None:
    assert DesktopInputRuntime.normalize_hotkey("Ctrl C") == "ctrl+c"
    assert DesktopInputRuntime.normalize_hotkey("Page Up") == "pageup"
    assert DesktopInputRuntime.normalize_hotkey("Shift-Tab") == "shift+tab"


def test_desktop_input_runtime_marks_safe_hotkeys_from_config(app_config) -> None:
    app_config.desktop_input.enabled = True
    runtime = DesktopInputRuntime(app_config)

    assert runtime.is_safe_hotkey("Ctrl C") is True
    assert runtime.is_safe_hotkey("Ctrl V") is False


def test_desktop_input_runtime_blocks_expected_window_mismatch(monkeypatch, app_config) -> None:
    app_config.desktop_input.enabled = True
    runtime = DesktopInputRuntime(app_config)
    monkeypatch.setattr(
        runtime,
        "_active_window_snapshot",
        lambda: {"title": "Visual Studio Code", "process_name": "Code"},
    )

    with pytest.raises(RuntimeError, match="expected the active window title"):
        runtime._guard_expected_window("Google Chrome", "")


def test_desktop_input_runtime_limits_typed_text(app_config) -> None:
    app_config.desktop_input.enabled = True
    app_config.desktop_input.max_type_chars = 5
    runtime = DesktopInputRuntime(app_config)

    with pytest.raises(RuntimeError, match="Typing is limited to 5 characters"):
        runtime.type_text(text="too-long")

