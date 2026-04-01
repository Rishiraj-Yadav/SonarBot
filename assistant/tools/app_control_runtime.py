"""Windows app and window control runtime."""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from assistant.tools.windows_desktop import RECT, get_window_title, load_desktop_libraries, query_process_path
SW_RESTORE = 9
SW_MINIMIZE = 6
SW_MAXIMIZE = 3
MONITOR_DEFAULTTONEAREST = 2


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", wintypes.DWORD),
    ]


@dataclass(slots=True)
class AppWindow:
    hwnd: int
    title: str
    process_name: str
    executable_path: str
    is_visible: bool
    is_foreground: bool
    is_minimized: bool

    @property
    def window_id(self) -> str:
        return str(self.hwnd)

    def to_dict(self) -> dict[str, Any]:
        return {
            "window_id": self.window_id,
            "title": self.title,
            "process_name": self.process_name,
            "executable_path": self.executable_path,
            "is_visible": self.is_visible,
            "is_foreground": self.is_foreground,
            "is_minimized": self.is_minimized,
        }


class AppControlRuntime:
    def __init__(self, config) -> None:
        self.config = config
        self.known_apps = {
            str(alias).strip().lower(): Path(path)
            for alias, path in getattr(config.desktop_apps, "known_apps", {}).items()
        }
        self._enabled = bool(getattr(config.desktop_apps, "enabled", False))
        self._available = sys.platform == "win32"
        self._availability_error = ""
        if self._available:
            self.user32, self.kernel32, self._availability_error = load_desktop_libraries()
            if self.user32 is None or self.kernel32 is None:
                self._available = False
        else:
            self.user32 = None
            self.kernel32 = None

    @property
    def available(self) -> bool:
        return self._enabled and self._available

    def ensure_available(self) -> None:
        if not self._enabled:
            raise RuntimeError("Desktop app control is not enabled.")
        if sys.platform != "win32":
            raise RuntimeError("Desktop app control is only available on Windows hosts.")
        if not self._available:
            detail = f" ({self._availability_error})" if self._availability_error else ""
            raise RuntimeError(f"Desktop app control is unavailable on this Windows host{detail}.")

    def list_windows(self) -> dict[str, Any]:
        self.ensure_available()
        windows = [window.to_dict() for window in self._enumerate_windows()]
        return {"windows": windows}

    def open_app(self, target: str, args: list[str] | None = None) -> dict[str, Any]:
        self.ensure_available()
        alias, executable_path = self.resolve_launch_target(target, self.known_apps)
        launch_args = [str(item) for item in (args or []) if str(item).strip()]
        launch_target: Path | None = None
        launch_error: RuntimeError | None = None
        try:
            launch_target = self._resolve_installed_launch_target(alias, executable_path)
        except RuntimeError as exc:
            launch_error = exc

        app_user_model_id = self._app_user_model_id(alias)
        if launch_target is None and app_user_model_id is not None:
            if launch_args:
                raise RuntimeError(
                    f"App target '{alias}' is installed as a packaged Windows app and cannot receive launch arguments in this mode."
                )
            explorer_path = Path("C:/Windows/explorer.exe")
            process = subprocess.Popen([str(explorer_path), f"shell:AppsFolder\\{app_user_model_id}"], cwd=str(explorer_path.parent))
            return {
                "alias": alias,
                "path": f"shell:AppsFolder\\{app_user_model_id}",
                "pid": int(process.pid),
                "launched": True,
                "args": [],
                "via_app_id": True,
            }

        if launch_target is None:
            assert launch_error is not None
            raise launch_error

        if launch_target.suffix.lower() == ".lnk":
            if launch_args:
                raise RuntimeError(
                    f"App target '{alias}' resolved to the shortcut '{launch_target}', which cannot receive launch arguments. "
                    f"Please update desktop_apps.known_apps['{alias}'] to point to the actual executable."
                )
            os.startfile(str(launch_target))
            return {
                "alias": alias,
                "path": str(launch_target),
                "pid": None,
                "launched": True,
                "args": [],
                "via_shortcut": True,
            }
        process = subprocess.Popen([str(launch_target), *launch_args], cwd=str(launch_target.parent))
        return {
            "alias": alias,
            "path": str(launch_target),
            "pid": int(process.pid),
            "launched": True,
            "args": launch_args,
        }

    def focus_window(self, target: str) -> dict[str, Any]:
        self.ensure_available()
        window = self.resolve_window_target(target, self._enumerate_windows(), self.known_apps)
        assert self.user32 is not None
        self.user32.ShowWindow(window.hwnd, SW_RESTORE)
        self.user32.SetForegroundWindow(window.hwnd)
        return {"target": target, "window": window.to_dict(), "action": "focus"}

    def minimize_window(self, target: str) -> dict[str, Any]:
        self.ensure_available()
        window = self.resolve_window_target(target, self._enumerate_windows(), self.known_apps)
        assert self.user32 is not None
        self.user32.ShowWindow(window.hwnd, SW_MINIMIZE)
        payload = window.to_dict()
        payload["is_minimized"] = True
        return {"target": target, "window": payload, "action": "minimize"}

    def maximize_window(self, target: str) -> dict[str, Any]:
        self.ensure_available()
        window = self.resolve_window_target(target, self._enumerate_windows(), self.known_apps)
        assert self.user32 is not None
        self.user32.ShowWindow(window.hwnd, SW_MAXIMIZE)
        return {"target": target, "window": window.to_dict(), "action": "maximize"}

    def restore_window(self, target: str) -> dict[str, Any]:
        self.ensure_available()
        window = self.resolve_window_target(target, self._enumerate_windows(), self.known_apps)
        assert self.user32 is not None
        self.user32.ShowWindow(window.hwnd, SW_RESTORE)
        payload = window.to_dict()
        payload["is_minimized"] = False
        return {"target": target, "window": payload, "action": "restore"}

    def snap_window(self, target: str, position: str) -> dict[str, Any]:
        self.ensure_available()
        if not bool(getattr(self.config.desktop_apps, "allow_layout_changes", True)):
            raise RuntimeError("Window layout changes are disabled in desktop_apps.allow_layout_changes.")
        normalized_position = self.normalize_snap_position(position)
        window = self.resolve_window_target(target, self._enumerate_windows(), self.known_apps)
        assert self.user32 is not None
        self.user32.ShowWindow(window.hwnd, SW_RESTORE)
        left, top, right, bottom = self._monitor_work_area(window.hwnd)
        width = max(100, right - left)
        height = max(100, bottom - top)
        half_width = max(100, width // 2)
        target_left = left if normalized_position == "left" else left + half_width
        target_width = half_width if normalized_position == "left" else width - half_width
        self.user32.MoveWindow(window.hwnd, target_left, top, target_width, height, True)
        return {
            "target": target,
            "window": window.to_dict(),
            "action": "snap",
            "position": normalized_position,
        }

    @staticmethod
    def normalize_snap_position(position: str) -> str:
        normalized = position.strip().lower()
        if normalized not in {"left", "right"}:
            raise ValueError("Window snap position must be 'left' or 'right'.")
        return normalized

    @staticmethod
    def resolve_launch_target(target: str, known_apps: dict[str, Path]) -> tuple[str, Path]:
        normalized_target = AppControlRuntime._normalize_token(target)
        if normalized_target in known_apps:
            return normalized_target, known_apps[normalized_target]
        candidate = Path(target.strip().strip("\"'")).expanduser()
        candidate_text = AppControlRuntime._normalize_path(candidate)
        for alias, configured_path in known_apps.items():
            if AppControlRuntime._normalize_path(configured_path) == candidate_text:
                return alias, configured_path
        raise RuntimeError(f"I couldn't find a configured app target for '{target}'.")

    @staticmethod
    def resolve_window_target(target: str, windows: list[AppWindow], known_apps: dict[str, Path]) -> AppWindow:
        stripped = target.strip().strip("\"'")
        if not stripped:
            raise RuntimeError("Please provide an app or window target.")
        if stripped.isdigit():
            for window in windows:
                if window.window_id == stripped:
                    return window
        matches = AppControlRuntime.match_windows(windows, stripped, known_apps)
        if not matches:
            raise RuntimeError(f"I couldn't find a window matching '{target}'.")
        if len(matches) > 1:
            titles = ", ".join(window.title for window in matches[:5])
            raise RuntimeError(f"Multiple windows match '{target}': {titles}")
        return matches[0]

    @staticmethod
    def match_windows(windows: list[AppWindow], target: str, known_apps: dict[str, Path]) -> list[AppWindow]:
        compact_target = AppControlRuntime._normalize_token(target)
        if not compact_target:
            return []
        exact_title = [window for window in windows if AppControlRuntime._normalize_token(window.title) == compact_target]
        if exact_title:
            return exact_title

        exact_process = [
            window
            for window in windows
            if AppControlRuntime._normalize_token(window.process_name) == compact_target
        ]
        if exact_process:
            return exact_process

        alias_path = known_apps.get(compact_target)
        if alias_path is not None:
            expected_process = AppControlRuntime._normalize_token(alias_path.stem)
            alias_matches = [
                window
                for window in windows
                if AppControlRuntime._normalize_token(window.process_name) == expected_process
            ]
            if alias_matches:
                return alias_matches

        unique_title_contains = [
            window
            for window in windows
            if compact_target in AppControlRuntime._normalize_token(window.title)
        ]
        if len(unique_title_contains) == 1:
            return unique_title_contains

        unique_process_contains = [
            window
            for window in windows
            if compact_target in AppControlRuntime._normalize_token(window.process_name)
        ]
        if len(unique_process_contains) == 1:
            return unique_process_contains

        return []

    def _enumerate_windows(self) -> list[AppWindow]:
        self.ensure_available()
        assert self.user32 is not None
        foreground = int(self.user32.GetForegroundWindow())
        windows: list[AppWindow] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def enum_proc(hwnd: int, _lparam: int) -> bool:
            if not bool(self.user32.IsWindowVisible(hwnd)):
                return True
            length = int(self.user32.GetWindowTextLengthW(hwnd))
            if length <= 0:
                return True
            title = get_window_title(self.user32, int(hwnd))
            if not title:
                return True
            pid = wintypes.DWORD()
            self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            executable_path = query_process_path(self.kernel32, int(pid.value))
            process_name = Path(executable_path).stem if executable_path else ""
            windows.append(
                AppWindow(
                    hwnd=int(hwnd),
                    title=title,
                    process_name=process_name,
                    executable_path=executable_path,
                    is_visible=True,
                    is_foreground=int(hwnd) == foreground,
                    is_minimized=bool(self.user32.IsIconic(hwnd)),
                )
            )
            return True

        self.user32.EnumWindows(enum_proc, 0)
        return windows

    def _monitor_work_area(self, hwnd: int) -> tuple[int, int, int, int]:
        assert self.user32 is not None
        monitor = self.user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if monitor and self.user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return info.rcWork.left, info.rcWork.top, info.rcWork.right, info.rcWork.bottom
        rect = RECT()
        self.user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0)
        return rect.left, rect.top, rect.right, rect.bottom

    @staticmethod
    def _normalize_token(value: str) -> str:
        text = value.strip().strip("\"'").lower()
        text = text.removeprefix("the ").removeprefix("app ").removeprefix("window ")
        return "".join(char for char in text if char.isalnum())

    @staticmethod
    def _normalize_path(path: Path) -> str:
        return str(path.expanduser()).replace("\\", "/").rstrip("/").lower()

    def _resolve_installed_launch_target(self, alias: str, configured_path: Path) -> Path:
        seen: set[str] = set()
        for candidate in self._candidate_launch_targets(alias, configured_path):
            key = self._normalize_path(candidate)
            if key in seen:
                continue
            seen.add(key)
            if candidate.exists():
                return candidate

        shortcut = self._find_start_menu_shortcut(alias)
        if shortcut is not None:
            return shortcut

        executable_name = configured_path.name.strip()
        if executable_name:
            which_match = shutil.which(executable_name)
            if which_match:
                return Path(which_match)

        alias_match = shutil.which(alias)
        if alias_match:
            return Path(alias_match)

        raise RuntimeError(
            f"Configured app target '{alias}' does not exist at '{configured_path}', and I couldn't find an installed fallback."
        )

    def _candidate_launch_targets(self, alias: str, configured_path: Path) -> list[Path]:
        program_files = Path(os.environ.get("ProgramFiles", "C:/Program Files"))
        program_files_x86 = Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)"))
        local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        candidates: list[Path] = [configured_path]

        if alias == "vscode":
            candidates.extend(
                [
                    local_app_data / "Programs" / "Microsoft VS Code" / "Code.exe",
                    program_files / "Microsoft VS Code" / "Code.exe",
                    program_files_x86 / "Microsoft VS Code" / "Code.exe",
                ]
            )
            for drive_letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
                candidates.append(Path(f"{drive_letter}:/Microsoft VS Code/Code.exe"))
        elif alias in {"word", "excel", "outlook"}:
            executable_name = {
                "word": "WINWORD.EXE",
                "excel": "EXCEL.EXE",
                "outlook": "OUTLOOK.EXE",
            }[alias]
            office_roots = [
                program_files / "Microsoft Office" / "root" / "Office16",
                program_files_x86 / "Microsoft Office" / "root" / "Office16",
                program_files / "Microsoft Office" / "root" / "Office15",
                program_files_x86 / "Microsoft Office" / "root" / "Office15",
                program_files / "Microsoft Office" / "Office16",
                program_files_x86 / "Microsoft Office" / "Office16",
                program_files / "Microsoft Office" / "Office15",
                program_files_x86 / "Microsoft Office" / "Office15",
                program_files / "Microsoft Office" / "Office14",
                program_files_x86 / "Microsoft Office" / "Office14",
            ]
            candidates.extend(root / executable_name for root in office_roots)
        elif alias == "whatsapp":
            candidates.extend(
                [
                    local_app_data / "WhatsApp" / "WhatsApp.exe",
                    local_app_data / "Programs" / "WhatsApp" / "WhatsApp.exe",
                ]
            )
            for drive_letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
                candidates.append(Path(f"{drive_letter}:/WhatsApp/WhatsApp.exe"))
        elif alias == "taskmanager":
            candidates.append(Path("C:/Windows/System32/taskmgr.exe"))
        elif alias == "settings":
            candidates.append(Path("C:/Windows/ImmersiveControlPanel/SystemSettings.exe"))
        elif alias == "calculator":
            candidates.append(Path("C:/Windows/System32/calc.exe"))
        elif alias == "cmd":
            candidates.append(Path("C:/Windows/System32/cmd.exe"))
        elif alias == "powershell":
            candidates.append(Path("C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"))

        return candidates

    def _find_start_menu_shortcut(self, alias: str) -> Path | None:
        user_programs = Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        all_users_programs = Path(os.environ.get("ProgramData", "C:/ProgramData")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        keywords = [self._normalize_token(item) for item in self._shortcut_keywords(alias)]
        for root in (user_programs, all_users_programs):
            if not root.exists():
                continue
            for shortcut in root.rglob("*.lnk"):
                shortcut_name = self._normalize_token(shortcut.stem)
                if any(keyword == shortcut_name or keyword in shortcut_name for keyword in keywords):
                    return shortcut
        return None

    @staticmethod
    def _shortcut_keywords(alias: str) -> list[str]:
        alias_map = {
            "taskmanager": ["task manager"],
            "vscode": ["visual studio code", "vscode", "code"],
            "excel": ["excel"],
            "word": ["word"],
            "outlook": ["outlook"],
            "whatsapp": ["whatsapp"],
            "notepad": ["notepad"],
            "explorer": ["explorer", "file explorer"],
            "powershell": ["powershell"],
            "cmd": ["command prompt", "cmd"],
            "calculator": ["calculator"],
            "settings": ["settings"],
        }
        return alias_map.get(alias, [alias])

    @staticmethod
    def _app_user_model_id(alias: str) -> str | None:
        package_map = {
            "whatsapp": "5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App",
            "settings": "windows.immersivecontrolpanel_cw5n1h2txyewy!microsoft.windows.immersivecontrolpanel",
            "calculator": "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App",
        }
        return package_map.get(alias)
