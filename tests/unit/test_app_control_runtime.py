from __future__ import annotations

from pathlib import Path

import pytest

from assistant.tools.app_control_runtime import AppControlRuntime, AppWindow


def test_app_runtime_resolves_launch_target_from_alias(app_config) -> None:
    known_apps = {
        "chrome": Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
        "vscode": Path("C:/Users/Ritesh/AppData/Local/Programs/Microsoft VS Code/Code.exe"),
    }

    alias, path = AppControlRuntime.resolve_launch_target("chrome", known_apps)

    assert alias == "chrome"
    assert path == known_apps["chrome"]


def test_app_runtime_matches_window_from_configured_alias(app_config) -> None:
    windows = [
        AppWindow(
            hwnd=101,
            title="Google Chrome",
            process_name="chrome",
            executable_path="C:/Program Files/Google/Chrome/Application/chrome.exe",
            is_visible=True,
            is_foreground=False,
            is_minimized=False,
        ),
        AppWindow(
            hwnd=202,
            title="Visual Studio Code",
            process_name="Code",
            executable_path="C:/Users/Ritesh/AppData/Local/Programs/Microsoft VS Code/Code.exe",
            is_visible=True,
            is_foreground=True,
            is_minimized=False,
        ),
    ]
    known_apps = {
        "chrome": Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
        "vscode": Path("C:/Users/Ritesh/AppData/Local/Programs/Microsoft VS Code/Code.exe"),
    }

    matched = AppControlRuntime.resolve_window_target("vscode", windows, known_apps)

    assert matched.hwnd == 202
    assert matched.process_name == "Code"


def test_app_runtime_detects_ambiguous_window_matches(app_config) -> None:
    windows = [
        AppWindow(
            hwnd=101,
            title="Google Chrome - Docs",
            process_name="chrome",
            executable_path="C:/Program Files/Google/Chrome/Application/chrome.exe",
            is_visible=True,
            is_foreground=False,
            is_minimized=False,
        ),
        AppWindow(
            hwnd=102,
            title="Google Chrome - Gmail",
            process_name="chrome",
            executable_path="C:/Program Files/Google/Chrome/Application/chrome.exe",
            is_visible=True,
            is_foreground=True,
            is_minimized=False,
        ),
    ]
    known_apps = {"chrome": Path("C:/Program Files/Google/Chrome/Application/chrome.exe")}

    with pytest.raises(RuntimeError, match="Multiple windows match 'chrome'"):
        AppControlRuntime.resolve_window_target("chrome", windows, known_apps)


def test_app_runtime_validates_snap_direction() -> None:
    assert AppControlRuntime.normalize_snap_position("left") == "left"
    with pytest.raises(ValueError, match="left' or 'right"):
        AppControlRuntime.normalize_snap_position("top")


def test_app_runtime_adds_office_fallback_candidates(app_config) -> None:
    runtime = AppControlRuntime(app_config)

    candidates = runtime._candidate_launch_targets(
        "excel",
        Path("C:/Program Files/Microsoft Office/root/Office16/EXCEL.EXE"),
    )

    assert Path("C:/Program Files (x86)/Microsoft Office/root/Office16/EXCEL.EXE") in candidates


def test_app_runtime_has_whatsapp_store_app_fallback() -> None:
    assert AppControlRuntime._app_user_model_id("whatsapp") == "5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App"
