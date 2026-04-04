from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import pytest

from assistant.agent.queue import QueueMode
from assistant.gateway.router import GatewayRouter


class DummyAgentLoop:
    def __init__(self) -> None:
        self.enqueued = []
        self.queue = type("Queue", (), {"pending_count": lambda self: 0})()

    async def enqueue(self, request) -> None:
        self.enqueued.append(request)

    def status(self) -> dict[str, object]:
        return {"running": False, "pending": 0, "current_session_key": None}


class DummyConnectionManager:
    def active_count(self) -> int:
        return 0

    def active_channels(self) -> list[str]:
        return []

    def get_connection(self, _connection_id: str):
        return None


class DummySessionManager:
    def __init__(self) -> None:
        self.messages = []
        self.session = type("Session", (), {"session_key": "webchat_main"})()

    async def load_or_create(self, session_key: str):
        self.session.session_key = session_key
        return self.session

    async def append_message(self, session, message):
        self.messages.append(message)

    async def session_history(self, _session_key: str, limit: int = 20):
        if limit <= 0:
            return []
        return self.messages[-limit:]

    def active_count(self) -> int:
        return 1


class DummyHookRunner:
    async def fire_event(self, *_args, **_kwargs):
        return type("HookEvent", (), {"messages": []})()


class DummySkillRegistry:
    def __init__(self, enabled=None, matches=None) -> None:
        self._enabled = enabled or []
        self._matches = matches or []

    def active_count(self) -> int:
        return len(self._enabled)

    def find_user_invocable(self, _name: str):
        return None

    def list_enabled(self) -> list[object]:
        return list(self._enabled)

    def match_natural_language(self, _message: str):
        return list(self._matches)

    def load_skill_prompt(self, name: str) -> str:
        return f"Skill prompt for {name}"


class DummyPresenceRegistry:
    def snapshot(self) -> list[object]:
        return []


class DummyOAuthFlowManager:
    async def start_oauth_flow(self, provider: str):
        return {"provider": provider, "authorize_url": "https://example.com", "redirect_uri": "http://127.0.0.1"}

    @property
    def token_manager(self):
        class _TokenManager:
            async def list_connected(self):
                return []

        return _TokenManager()


class DummyToolRegistry:
    def __init__(
        self,
        *,
        llm_task_response: dict[str, object] | None = None,
        has_llm_task: bool = True,
        host_tools_enabled: bool = False,
        app_tools_enabled: bool = False,
        screen_tools_enabled: bool = False,
        input_tools_enabled: bool = False,
        app_skill_tools_enabled: bool = False,
        browser_active_url: str = "https://github.com",
    ) -> None:
        self.calls = []
        self.llm_task_response = llm_task_response or {"content": json.dumps({"skill": "daily-briefing", "confidence": 0.91})}
        self.has_llm_task = has_llm_task
        self.host_tools_enabled = host_tools_enabled
        self.app_tools_enabled = app_tools_enabled
        self.screen_tools_enabled = screen_tools_enabled
        self.input_tools_enabled = input_tools_enabled
        self.app_skill_tools_enabled = app_skill_tools_enabled
        self.browser_active_url = browser_active_url
        self.browser_runtime = type(
            "BrowserRuntimeStub",
            (),
            {
                "current_state": lambda self: {
                    "headless": False,
                    "tabs": [{"tab_id": "tab-1", "title": "GitHub", "url": browser_active_url}],
                    "active_tab": {"tab_id": "tab-1", "title": "GitHub", "url": browser_active_url},
                    "active_profile": {"site_name": "github.com", "profile_name": "work", "status": "active"},
                }
            },
        )()

    def has(self, tool_name: str) -> bool:
        if tool_name in {
            "browser_sessions_list",
            "browser_tabs_list",
            "browser_downloads_list",
            "browser_logs",
            "browser_tab_open",
            "browser_tab_switch",
            "browser_tab_close",
            "browser_screenshot",
            "browser_login",
        }:
            return True
        if self.app_tools_enabled and tool_name in {
            "apps_list_windows",
            "apps_open",
            "apps_focus",
            "apps_minimize",
            "apps_maximize",
            "apps_restore",
            "apps_snap",
        }:
            return True
        if self.screen_tools_enabled and tool_name in {
            "desktop_active_window",
            "desktop_screenshot",
            "desktop_window_screenshot",
            "desktop_ocr",
            "desktop_read_screen",
        }:
            return True
        if self.input_tools_enabled and tool_name in {
            "desktop_mouse_position",
            "desktop_mouse_move",
            "desktop_mouse_click",
            "desktop_mouse_scroll",
            "desktop_keyboard_type",
            "desktop_keyboard_hotkey",
            "desktop_clipboard_read",
            "desktop_clipboard_write",
        }:
            return True
        if self.app_skill_tools_enabled and tool_name in {
            "vscode_open_target",
            "vscode_search",
            "document_create",
            "document_read",
            "document_replace_text",
            "excel_create_workbook",
            "excel_append_row",
            "excel_preview",
            "browser_workspace_open",
            "system_open_settings",
            "system_volume_status",
            "system_volume_set",
            "system_brightness_status",
            "system_brightness_set",
            "system_bluetooth_status",
            "system_bluetooth_set",
            "system_snapshot",
            "task_manager_open",
            "task_manager_summary",
            "preset_list",
            "preset_run",
        }:
            return True
        if self.host_tools_enabled and tool_name in {"list_host_dir", "search_host_files", "read_host_file", "exec_shell", "write_host_file"}:
            return True
        return self.has_llm_task and tool_name == "llm_task"

    async def dispatch(self, tool_name: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append((tool_name, payload))
        if tool_name == "gmail_search":
            return {
                "query": payload.get("query", "in:inbox"),
                "count": 2,
                "threads": [
                    {
                        "thread_id": "thread-1",
                        "from": "sender@example.com",
                        "subject": "First subject",
                        "date": "Mon, 01 Jan 2026 10:00:00 +0000",
                        "snippet": "First snippet",
                    },
                    {
                        "thread_id": "thread-2",
                        "from": "another@example.com",
                        "subject": "Second subject",
                        "date": "Mon, 01 Jan 2026 09:00:00 +0000",
                        "snippet": "Second snippet",
                    },
                ],
            }
        if tool_name == "gmail_latest_email":
            return {
                "found": True,
                "from": "sender@example.com",
                "subject": "Test subject",
                "date": "Mon, 01 Jan 2026 10:00:00 +0000",
                "snippet": "Snippet text",
                "body": "Body preview",
            }
        if tool_name == "gmail_send":
            return {
                "id": "message-123",
                "thread_id": "thread-send-123",
                "label_ids": ["SENT"],
            }
        if tool_name == "gmail_create_draft":
            return {
                "draft_id": "draft-123",
                "message_id": "message-draft-123",
                "thread_id": "thread-draft-123",
            }
        if tool_name == "github_list_repos":
            return {"repositories": [{"full_name": "octo/repo-1"}, {"full_name": "octo/repo-2"}]}
        if tool_name == "github_list_pull_requests":
            return {
                "owner": payload["owner"],
                "repo": payload["repo"],
                "pull_requests": [
                    {
                        "number": 7,
                        "title": "Improve routing",
                        "user": "octocat",
                        "html_url": "https://github.com/octo/repo/pull/7",
                    }
                ],
            }
        if tool_name == "github_list_branches":
            return {
                "owner": payload["owner"],
                "repo": payload["repo"],
                "branches": [
                    {"name": "main", "protected": True, "sha": "sha-main"},
                    {"name": "Nick", "protected": False, "sha": "sha-nick"},
                    {"name": "desktop-agent", "protected": False, "sha": "sha-desktop"},
                ],
            }
        if tool_name == "github_compare_branches":
            head = str(payload.get("head", ""))
            base = str(payload.get("base", ""))
            if head == "main" and base == "main":
                return {"status": "identical", "ahead_by": 0, "behind_by": 0, "total_commits": 0}
            return {"status": "ahead", "ahead_by": 2, "behind_by": 0, "total_commits": 2}
        if tool_name == "github_create_pull_request":
            return {
                "owner": payload["owner"],
                "repo": payload["repo"],
                "number": 12,
                "title": payload["title"],
                "head": payload["head"],
                "base": payload["base"],
                "html_url": "https://github.com/Rishiraj-Yadav/Personal-AI-Assistant/pull/12",
            }
        if tool_name == "llm_task":
            return self.llm_task_response
        if tool_name == "list_host_dir":
            requested_path = str(payload["path"])
            if requested_path in {"R:/", "R:\\"}:
                return {
                    "path": requested_path,
                    "entries": [
                        {"name": "college", "path": "R:/college", "is_dir": True, "size": 0},
                        {"name": "notes.txt", "path": "R:/notes.txt", "is_dir": False, "size": 256},
                    ],
                }
            if requested_path.endswith("/5sem") or requested_path.endswith("\\5sem"):
                return {
                    "path": requested_path,
                    "entries": [
                        {"name": "dbms", "path": f"{requested_path}/dbms", "is_dir": True, "size": 0},
                        {"name": "os-notes.pdf", "path": f"{requested_path}/os-notes.pdf", "is_dir": False, "size": 1024},
                    ],
                }
            if requested_path.endswith("/6_semester") or requested_path.endswith("\\6_semester"):
                return {
                    "path": requested_path,
                    "entries": [
                        {"name": "SPCC", "path": f"{requested_path}/SPCC", "is_dir": True, "size": 0},
                        {"name": "timepass.txt", "path": f"{requested_path}/timepass.txt", "is_dir": False, "size": 512},
                    ],
                }
            if requested_path.replace("\\", "/") == "C:/Users/Ritesh/Downloads":
                return {
                    "path": requested_path,
                    "entries": [
                        {"name": "Resume.pdf", "path": "C:/Users/Ritesh/Downloads/Resume.pdf", "is_dir": False, "size": 1024},
                        {"name": "notes", "path": "C:/Users/Ritesh/Downloads/notes", "is_dir": True, "size": 0},
                    ],
                }
            return {
                "path": payload["path"],
                "entries": [
                    {"name": "Resume.pdf", "path": "C:/Users/Ritesh/Downloads/Resume.pdf", "is_dir": False, "size": 1024},
                    {"name": "notes", "path": "C:/Users/Ritesh/Downloads/notes", "is_dir": True, "size": 0},
                ],
            }
        if tool_name == "search_host_files":
            query = str(payload.get("name_query", ""))
            query_compact = re.sub(r"[^a-z0-9]+", "", query.lower())
            root = str(payload.get("root", "@allowed"))
            if query_compact == "5sem" and root == "R:/":
                return {
                    "root": "R:/",
                    "searched_roots": ["R:/"],
                    "matches": [
                        {"name": "5sem", "path": "R:/college/5sem", "is_dir": True},
                    ],
                    "directories_only": bool(payload.get("directories_only")),
                }
            if query_compact == "spcc":
                return {
                    "root": root,
                    "searched_roots": [root] if root != "@allowed" else ["R:/"],
                    "matches": [
                        {"name": "SPCC", "path": "R:/6_semester/SPCC", "is_dir": True},
                    ],
                    "directories_only": bool(payload.get("directories_only")),
                }
            if query_compact == "6semester":
                return {
                    "root": root,
                    "searched_roots": [root] if root != "@allowed" else ["R:/"],
                    "matches": [
                        {"name": "6_semester", "path": "R:/6_semester", "is_dir": True},
                    ],
                    "directories_only": bool(payload.get("directories_only")),
                }
            if query_compact == "archive":
                return {
                    "root": root,
                    "searched_roots": [root] if root != "@allowed" else ["R:/", "C:/Users/Ritesh/Documents"],
                    "matches": [
                        {"name": "Archive", "path": "R:/6_semester/Archive", "is_dir": True},
                        {"name": "Archive", "path": "C:/Users/Ritesh/Documents/Archive", "is_dir": True},
                    ],
                    "directories_only": bool(payload.get("directories_only")),
                }
            if query_compact == "report.pdf" or query_compact == "reportpdf":
                return {
                    "root": root,
                    "searched_roots": [root] if root != "@allowed" else ["C:/Users/Ritesh/Documents"],
                    "matches": [
                        {"name": "report.pdf", "path": f"{root.rstrip('/\\')}/report.pdf", "is_dir": False},
                    ],
                    "directories_only": bool(payload.get("directories_only")),
                }
            if query_compact == "testing.txt" or query_compact == "testingtxt":
                resolved_root = root.rstrip("/\\")
                base_path = "R:/5_SEM" if root in {"R:/", "@allowed"} else resolved_root
                return {
                    "root": root,
                    "searched_roots": [root] if root != "@allowed" else ["R:/"],
                    "matches": [
                        {"name": "testing.txt", "path": f"{base_path}/testing.txt", "is_dir": False},
                    ],
                    "directories_only": bool(payload.get("directories_only")),
                    "files_only": bool(payload.get("files_only")),
                }
            if query_compact == "cpractice":
                return {
                    "root": root,
                    "searched_roots": [root] if root != "@allowed" else ["R:/"],
                    "matches": [
                        {"name": "C practice", "path": "R:/C practice", "is_dir": True},
                    ],
                    "directories_only": bool(payload.get("directories_only")),
                    "files_only": bool(payload.get("files_only")),
                }
            if query_compact == "testing123.docx" or query_compact == "testing123docx":
                return {
                    "root": root,
                    "searched_roots": [root] if root != "@allowed" else ["R:/"],
                    "matches": [
                        {"name": "testing123.docx", "path": "R:/C practice/testing123.docx", "is_dir": False},
                    ],
                    "directories_only": bool(payload.get("directories_only")),
                    "files_only": bool(payload.get("files_only")),
                }
            return {
                "root": root,
                "searched_roots": ["C:/Users/Ritesh/Documents", "R:/"] if root == "@allowed" else [root],
                "matches": [
                    {"name": "5sem", "path": "C:/Users/Ritesh/Documents/college/5sem", "is_dir": True},
                    {"name": "5sem-notes.txt", "path": "C:/Users/Ritesh/Documents/5sem-notes.txt", "is_dir": False},
                ],
                "directories_only": bool(payload.get("directories_only")),
            }
        if tool_name == "exec_shell":
            return {
                "stdout": "",
                "stderr": "",
                "exit_code": 0,
                "status": "completed",
                "approval_mode": "session_cache",
                "approval_category": "ask_once",
                "audit_id": "audit-1",
                "host": True,
            }
        if tool_name == "write_host_file":
            return {
                "path": payload["path"],
                "bytes_written": len(str(payload["content"]).encode("utf-8")),
                "status": "completed",
                "approval_mode": "session_cache",
                "approval_category": "ask_once",
                "audit_id": "audit-2",
                "host": True,
            }
        if tool_name == "read_host_file":
            requested_path = str(payload["path"]).replace("\\", "/")
            if requested_path.endswith("/testing.txt"):
                content = "hello rishiraj this is the testing file"
            elif requested_path.endswith("/testing123.docx"):
                content = "abcd"
            else:
                content = ""
            return {
                "path": payload["path"],
                "content": content,
                "bytes_read": len(content.encode("utf-8")),
                "line_count": len(content.splitlines()),
            }
        if tool_name == "browser_sessions_list":
            return {
                "sessions": [
                    {"site_name": "github.com", "profile_name": "work", "status": "active"},
                    {"site_name": "leetcode.com", "profile_name": "personal", "status": "stale"},
                ]
            }
        if tool_name == "browser_tabs_list":
            return {
                "current_tab_id": "tab-1",
                "tabs": [
                    {"tab_id": "tab-1", "title": "GitHub", "url": "https://github.com"},
                    {"tab_id": "tab-2", "title": "LeetCode", "url": "https://leetcode.com"},
                ],
            }
        if tool_name == "browser_downloads_list":
            return {"downloads": [{"filename": "report.csv", "path": "workspace/inbox/browser_downloads/work/report.csv"}]}
        if tool_name == "browser_logs":
            return {"logs": [{"kind": "console", "message": "Loaded dashboard"}, {"kind": "request_failed", "message": "GET /api/test -> 500"}]}
        if tool_name == "browser_tab_open":
            return {"tab_id": "tab-3", "title": "Docs", "url": str(payload.get("url", ""))}
        if tool_name == "browser_tab_switch":
            return {"tab_id": payload["tab_id"], "title": "LeetCode", "url": "https://leetcode.com"}
        if tool_name == "browser_tab_close":
            return {"current_tab_id": "tab-1"}
        if tool_name == "browser_screenshot":
            return {"path": "workspace/browser/screenshot-tab-1.png", "tab_id": "tab-1", "url": "https://github.com"}
        if tool_name == "browser_login":
            return {"site_name": payload["site_name"], "profile_name": payload.get("profile_name", "default"), "status": "active", "url": f"https://{payload['site_name']}"}
        if tool_name == "apps_list_windows":
            return {
                "windows": [
                    {
                        "window_id": "101",
                        "title": "Visual Studio Code",
                        "process_name": "Code",
                        "is_foreground": True,
                        "is_minimized": False,
                    },
                    {
                        "window_id": "202",
                        "title": "Google Chrome",
                        "process_name": "chrome",
                        "is_foreground": False,
                        "is_minimized": False,
                    },
                ]
            }
        if tool_name == "apps_open":
            target = str(payload["target"])
            return {"alias": target, "path": f"C:/Program Files/{target}/{target}.exe", "pid": 4321, "launched": True}
        if tool_name == "apps_focus":
            target = str(payload["target"])
            return {"action": "focus", "target": target, "window": {"title": target.title(), "process_name": target}}
        if tool_name == "apps_minimize":
            target = str(payload["target"])
            return {"action": "minimize", "target": target, "window": {"title": target.title(), "process_name": target}}
        if tool_name == "apps_maximize":
            target = str(payload["target"])
            return {"action": "maximize", "target": target, "window": {"title": target.title(), "process_name": target}}
        if tool_name == "apps_restore":
            target = str(payload["target"])
            return {"action": "restore", "target": target, "window": {"title": target.title(), "process_name": target}}
        if tool_name == "apps_snap":
            target = str(payload["target"])
            return {
                "action": "snap",
                "target": target,
                "position": payload["position"],
                "window": {"title": target.title(), "process_name": target},
            }
        if tool_name == "desktop_active_window":
            return {
                "active_window": {
                    "window_id": "101",
                    "title": "Visual Studio Code",
                    "process_name": "Code",
                    "executable_path": "C:/Users/Ritesh/AppData/Local/Programs/Microsoft VS Code/Code.exe",
                    "is_minimized": False,
                    "is_visible": True,
                }
            }
        if tool_name == "desktop_screenshot":
            return {
                "path": "workspace/desktop/desktop-20260331-120000.png",
                "scope": "desktop",
                "width": 1920,
                "height": 1080,
                "active_window": {"title": "Visual Studio Code", "process_name": "Code"},
            }
        if tool_name == "desktop_window_screenshot":
            return {
                "path": "workspace/desktop/window-20260331-120001.png",
                "scope": "window",
                "width": 1280,
                "height": 800,
                "active_window": {"title": "Visual Studio Code", "process_name": "Code"},
            }
        if tool_name == "desktop_read_screen":
            target = str(payload.get("target", "desktop"))
            return {
                "target": target,
                "path": f"workspace/desktop/{target}-20260331-120002.png",
                "active_window": {"title": "Visual Studio Code", "process_name": "Code"},
                "content": "This is visible screen text",
                "truncated": False,
            }
        if tool_name == "desktop_mouse_position":
            return {
                "x": 400,
                "y": 300,
                "coordinate_space": "screen",
                "active_window": {"title": "Visual Studio Code", "process_name": "Code"},
                "status": "completed",
            }
        if tool_name == "desktop_mouse_move":
            return {
                "x": int(payload.get("x", 0)),
                "y": int(payload.get("y", 0)),
                "coordinate_space": str(payload.get("coordinate_space", "screen")),
                "status": "completed",
            }
        if tool_name == "desktop_mouse_click":
            return {
                "x": int(payload.get("x", 0)),
                "y": int(payload.get("y", 0)),
                "coordinate_space": str(payload.get("coordinate_space", "screen")),
                "button": str(payload.get("button", "left")),
                "count": int(payload.get("count", 1)),
                "status": "completed",
            }
        if tool_name == "desktop_mouse_scroll":
            return {
                "direction": str(payload.get("direction", "down")),
                "amount": int(payload.get("amount", 1)),
                "status": "completed",
            }
        if tool_name == "desktop_keyboard_type":
            return {
                "characters_typed": len(str(payload.get("text", ""))),
                "status": "completed",
            }
        if tool_name == "desktop_keyboard_hotkey":
            return {
                "hotkey": str(payload.get("hotkey", "")).replace(" ", "+").lower(),
                "status": "completed",
            }
        if tool_name == "desktop_clipboard_read":
            return {
                "content": "copied text",
                "char_count": 11,
                "line_count": 1,
                "status": "completed",
            }
        if tool_name == "desktop_clipboard_write":
            return {
                "char_count": len(str(payload.get("text", ""))),
                "status": "completed",
            }
        if tool_name == "vscode_open_target":
            return {
                "path": "R:/6_semester/mini_project",
                "target_type": "directory",
                "alias": "vscode",
                "status": "completed",
            }
        if tool_name == "vscode_search":
            return {
                "matches": [
                    {"name": "mini_project", "path": "R:/6_semester/mini_project", "is_dir": True},
                    {"name": "app.py", "path": "R:/6_semester/mini_project/app.py", "is_dir": False},
                ]
            }
        if tool_name == "document_create":
            return {"path": str(payload.get("path", "")), "status": "completed", "approval_category": "ask_once", "approval_mode": "session_cache"}
        if tool_name == "document_read":
            return {"path": str(payload.get("path", "")), "content": "hello world", "bytes_read": 11, "line_count": 1}
        if tool_name == "document_replace_text":
            return {"path": str(payload.get("path", "")), "status": "completed", "replacements": 1, "approval_category": "always_ask", "approval_mode": "approval"}
        if tool_name == "excel_create_workbook":
            return {
                "path": str(payload.get("path", "")),
                "status": "completed",
                "sheet_name": "Sheet1",
                "preview": {"sheet_name": "Sheet1", "row_count": 1},
                "approval_category": "ask_once",
                "approval_mode": "session_cache",
            }
        if tool_name == "excel_append_row":
            return {
                "path": str(payload.get("path", "")),
                "status": "completed",
                "preview": {"sheet_name": "Sheet1", "row_count": 2},
                "approval_category": "always_ask",
                "approval_mode": "approval",
            }
        if tool_name == "excel_preview":
            return {
                "path": str(payload.get("path", "")),
                "sheet_name": "Sheet1",
                "rows": [["Name", "Marks"], ["Ritesh", "95"]],
                "row_count": 2,
            }
        if tool_name == "browser_workspace_open":
            workspace = str(payload.get("workspace", "study"))
            return {"workspace": workspace, "opened": [{"title": "Docs", "url": "https://example.com"}], "count": 1}
        if tool_name == "system_open_settings":
            return {"page": str(payload.get("page", "settings")), "status": "completed"}
        if tool_name == "system_volume_status":
            return {"volume_percent": 35}
        if tool_name == "system_volume_set":
            return {"volume_percent": int(payload.get("percent", 0)), "status": "completed", "approval_category": "always_ask", "approval_mode": "approval"}
        if tool_name == "system_brightness_status":
            return {"supported": True, "brightness_percent": 60}
        if tool_name == "system_brightness_set":
            return {"supported": True, "brightness_percent": int(payload.get("percent", 0)), "status": "completed", "approval_category": "always_ask", "approval_mode": "approval"}
        if tool_name == "system_bluetooth_status":
            return {"available": True, "service_status": "Running", "device_count": 2, "radio_state": "On"}
        if tool_name == "system_bluetooth_set":
            return {
                "status": "completed",
                "requested_state": str(payload.get("mode", "off")).capitalize(),
                "radio_state_after": str(payload.get("mode", "off")).capitalize(),
            }
        if tool_name == "system_snapshot":
            return {
                "cpu_percent": 18.5,
                "memory": {"used_percent": 42.0, "used_gb": 6.5, "total_gb": 15.8},
                "disk": {"used_percent": 63.0, "drive": "C:\\", "free_gb": 120.5, "total_gb": 512.0},
                "volume": {"volume_percent": 35},
            }
        if tool_name == "task_manager_open":
            return {
                "status": "completed",
                "summary": {
                    "cpu_percent": 22.0,
                    "memory": {"used_percent": 48.0, "used_gb": 7.2, "total_gb": 15.8},
                    "disk": {"used_percent": 63.0, "drive": "C:\\", "free_gb": 120.5, "total_gb": 512.0},
                    "top_processes": [{"name": "Code", "cpu_seconds": 120.0, "memory_mb": 350.0}],
                },
            }
        if tool_name == "task_manager_summary":
            return {
                "cpu_percent": 22.0,
                "memory": {"used_percent": 48.0, "used_gb": 7.2, "total_gb": 15.8},
                "disk": {"used_percent": 63.0, "drive": "C:\\", "free_gb": 120.5, "total_gb": 512.0},
                "top_processes": [{"name": "Code", "cpu_seconds": 120.0, "memory_mb": 350.0}],
            }
        if tool_name == "preset_list":
            return {"presets": [{"name": "study-mode", "description": "Open study apps."}]}
        if tool_name == "preset_run":
            return {"preset": str(payload.get("name", "study-mode")), "actions": ["Opened chrome", "Opened folder"], "status": "completed"}
        raise AssertionError(f"Unexpected tool: {tool_name}")


class DummyAutomationEngine:
    async def list_notifications(self, _user_id: str):
        return []

    async def list_runs(self, _user_id: str):
        return []

    async def list_rules(self, _user_id: str):
        return [*self.desktop_rules, *self.desktop_routines]

    async def pause_rule(self, _user_id: str, _rule_name: str) -> None:
        for rule in [*self.desktop_rules, *self.desktop_routines]:
            if rule["name"] == _rule_name:
                rule["paused"] = True
                return None
        raise KeyError(f"Unknown rule '{_rule_name}'.")

    async def resume_rule(self, _user_id: str, _rule_name: str) -> None:
        for rule in [*self.desktop_rules, *self.desktop_routines]:
            if rule["name"] == _rule_name:
                rule["paused"] = False
                return None
        raise KeyError(f"Unknown rule '{_rule_name}'.")

    async def delete_rule(self, _user_id: str, _rule_name: str) -> None:
        for collection in (self.desktop_rules, self.desktop_routines):
            for index, rule in enumerate(collection):
                if rule["name"] == _rule_name:
                    collection.pop(index)
                    return None
        raise KeyError(f"Unknown rule '{_rule_name}'.")

    async def replay_run(self, _run_id: str):
        return {"status": "ok"}

    async def list_approvals(self, _user_id: str):
        return []

    async def decide_approval(self, _approval_id: str, _decision: str) -> None:
        return None

    def __init__(self) -> None:
        self.dynamic_jobs: list[dict[str, object]] = []
        self.one_time_reminders: list[dict[str, object]] = []
        self.report_jobs: list[dict[str, object]] = []
        self.desktop_rules: list[dict[str, object]] = []
        self.desktop_routines: list[dict[str, object]] = []
        self.routine_runs: list[str] = []

    async def create_dynamic_cron_job(self, user_id: str, schedule: str, message: str) -> dict[str, object]:
        job = {
            "cron_id": "cron-user-1",
            "user_id": user_id,
            "schedule": schedule,
            "message": message,
            "paused": False,
        }
        self.dynamic_jobs.append(job)
        return job

    async def list_dynamic_cron_jobs(self, _user_id: str) -> list[dict[str, object]]:
        return list(self.dynamic_jobs)

    async def pause_dynamic_cron_job(self, _user_id: str, cron_id: str) -> dict[str, object]:
        for job in self.dynamic_jobs:
            if job["cron_id"] == cron_id:
                job["paused"] = True
                return job
        raise KeyError(f"Unknown cron job '{cron_id}'.")

    async def resume_dynamic_cron_job(self, _user_id: str, cron_id: str) -> dict[str, object]:
        for job in self.dynamic_jobs:
            if job["cron_id"] == cron_id:
                job["paused"] = False
                return job
        raise KeyError(f"Unknown cron job '{cron_id}'.")

    async def delete_dynamic_cron_job(self, _user_id: str, cron_id: str) -> bool:
        for index, job in enumerate(self.dynamic_jobs):
            if job["cron_id"] == cron_id:
                self.dynamic_jobs.pop(index)
                return True
        raise KeyError(f"Unknown cron job '{cron_id}'.")

    async def create_one_time_reminder(self, user_id: str, run_at, message: str) -> dict[str, object]:
        reminder = {
            "reminder_id": "once-user-1",
            "user_id": user_id,
            "run_at": run_at.isoformat(),
            "message": message,
            "paused": False,
            "fired": False,
        }
        self.one_time_reminders.append(reminder)
        return reminder

    async def list_one_time_reminders(self, _user_id: str) -> list[dict[str, object]]:
        return list(self.one_time_reminders)

    async def pause_one_time_reminder(self, _user_id: str, reminder_id: str) -> dict[str, object]:
        for reminder in self.one_time_reminders:
            if reminder["reminder_id"] == reminder_id:
                reminder["paused"] = True
                return reminder
        raise KeyError(f"Unknown reminder '{reminder_id}'.")

    async def resume_one_time_reminder(self, _user_id: str, reminder_id: str) -> dict[str, object]:
        for reminder in self.one_time_reminders:
            if reminder["reminder_id"] == reminder_id:
                reminder["paused"] = False
                return reminder
        raise KeyError(f"Unknown reminder '{reminder_id}'.")

    async def delete_one_time_reminder(self, _user_id: str, reminder_id: str) -> bool:
        for index, reminder in enumerate(self.one_time_reminders):
            if reminder["reminder_id"] == reminder_id:
                self.one_time_reminders.pop(index)
                return True
        return False

    async def list_report_jobs(self, _user_id: str | None = None) -> list[dict[str, object]]:
        return list(self.report_jobs)

    async def create_desktop_automation_rule(self, user_id: str, **payload) -> dict[str, object]:
        rule = {
            "rule_id": "desktop-rule-1",
            "user_id": user_id,
            "name": "desktop:desktop-rule-1",
            "display_name": payload.get("name", "Desktop automation"),
            "trigger": "desktop",
            "trigger_type": payload.get("trigger_type", "file_watch"),
            "watch_path": payload.get("watch_path", ""),
            "schedule": payload.get("schedule", ""),
            "event_types": payload.get("event_types", []),
            "file_extensions": payload.get("file_extensions", []),
            "filename_pattern": payload.get("filename_pattern", "*"),
            "action_type": payload.get("action_type", "notify"),
            "destination_path": payload.get("destination_path", ""),
            "paused": False,
        }
        self.desktop_rules = [rule]
        return rule

    async def create_desktop_routine_rule(self, user_id: str, **payload) -> dict[str, object]:
        routine = {
            "routine_id": "routine-rule-1",
            "user_id": user_id,
            "name": payload.get("name", "Study mode"),
            "trigger_type": payload.get("trigger_type", "manual"),
            "steps": payload.get("steps", []),
            "summary": payload.get("summary", "desktop routine"),
            "schedule": payload.get("schedule", ""),
            "run_at": payload.get("run_at", ""),
            "watch_path": payload.get("watch_path", ""),
            "event_types": payload.get("event_types", []),
            "file_extensions": payload.get("file_extensions", []),
            "filename_pattern": payload.get("filename_pattern", "*"),
            "paused": False,
        }
        payload_rule = {
            "name": "routine:routine-rule-1",
            "display_name": routine["name"],
            "trigger": "desktop_routine",
            "trigger_type": routine["trigger_type"],
            "schedule": routine["schedule"],
            "run_at": routine["run_at"],
            "watch_path": routine["watch_path"],
            "event_types": routine["event_types"],
            "file_extensions": routine["file_extensions"],
            "filename_pattern": routine["filename_pattern"],
            "summary": routine["summary"],
            "steps": routine["steps"],
            "step_count": len(routine["steps"]),
            "risky_step_count": sum(
                1
                for step in routine["steps"]
                if str(step.get("type", "")).lower()
                in {
                    "move_host_file",
                    "copy_host_file",
                    "write_host_file",
                    "delete_host_file",
                    "move_host_dir_contents",
                    "copy_host_dir_contents",
                }
            ),
            "paused": False,
            "routine": True,
        }
        self.desktop_routines = [payload_rule]
        return routine

    async def run_desktop_routine_now(self, user_id: str, routine_id: str, *, notify: bool = False) -> dict[str, object]:
        self.routine_runs.append(routine_id)
        return {
            "status": "completed",
            "message": f"Ran routine {routine_id}.",
            "summary": "desktop routine",
            "steps": [],
            "notify": notify,
            "user_id": user_id,
        }


class DummyUserProfiles:
    async def resolve_user_id(self, _identity_type: str, _identity_value: str, _metadata=None) -> str:
        return "default"


class DummySystemAccessManager:
    def __init__(self, approvals: list[dict[str, object]] | None = None) -> None:
        self.approvals = approvals or []
        self.decisions: list[tuple[str, str]] = []

    async def list_approvals(self, _user_id: str, limit: int = 20) -> list[dict[str, object]]:
        return list(self.approvals)[:limit]

    async def decide_approval(self, approval_id: str, decision: str) -> dict[str, object]:
        self.decisions.append((approval_id, decision))
        return {
            "approval_id": approval_id,
            "action_kind": "write_host_file",
            "target_summary": "C:/Users/Ritesh/Desktop/todo.txt",
            "category": "ask_once",
            "status": decision,
            "payload": {"path": "C:/Users/Ritesh/Desktop/todo.txt"},
        }


class DummyCoworkerService:
    def __init__(self) -> None:
        self.planned: list[str] = []
        self.ran: list[str] = []

    async def analyze_request(self, *, session_key: str, request_text: str) -> dict[str, object]:  # noqa: ARG002
        lowered = request_text.lower()
        desktop_ui_task = (
            "task manager" in lowered
            or "copy selected text and summarize it" in lowered
            or "turn off the bluetooth" in lowered
            or "turn on the bluetooth" in lowered
            or "disable bluetooth" in lowered
            or "enable bluetooth" in lowered
            or "see on screen" in lowered
            or "on screen now" in lowered
            or "visible file" in lowered
            or bool(re.match(r"^(?:click(?:\s+on)?|select|double click|double-click)\s+(?!at\b)(?:the\s+)?[a-z0-9][\w\s._()&-]*$", lowered))
        )
        return {
            "desktop_ui_task": desktop_ui_task,
            "task_kind": "structured" if "task manager" in lowered or "bluetooth" in lowered else ("visual" if desktop_ui_task else "non_desktop"),
            "summary": request_text.strip(),
            "normalized_request": request_text.strip(),
            "requires_visual_context": False,
            "route_kind": "structured" if "task manager" in lowered or "bluetooth" in lowered else ("visual" if desktop_ui_task else "none"),
        }

    async def can_handle_request(self, request_text: str) -> bool:
        lowered = request_text.lower()
        return (
            "task manager" in lowered
            or "copy selected text and summarize it" in lowered
            or "turn off the bluetooth" in lowered
            or "turn on the bluetooth" in lowered
            or "disable bluetooth" in lowered
            or "enable bluetooth" in lowered
            or "see on screen" in lowered
            or "on screen now" in lowered
            or "visible file" in lowered
            or bool(re.match(r"^(?:click(?:\s+on)?|select|double click|double-click)\s+(?!at\b)(?:the\s+)?[a-z0-9][\w\s._()&-]*$", lowered))
        )

    async def plan_task(self, *, user_id: str, session_key: str, request_text: str) -> dict[str, object]:
        self.planned.append(request_text)
        return {
            "task_id": "coworker-1",
            "user_id": user_id,
            "session_key": session_key,
            "request_text": request_text,
            "status": "planned",
            "summary": "Open Task Manager and summarize system usage.",
            "steps": [
                {"type": "task_manager_open", "title": "Open Task Manager", "verification": {"kind": "tool_status"}},
                {"type": "task_manager_summary", "title": "Summarize CPU, memory, and disk usage", "verification": {"kind": "summary_has_keys"}},
            ],
            "current_step_index": 0,
            "total_steps": 2,
            "latest_state": {},
            "transcript": [],
        }

    async def run_task_request(self, *, user_id: str, session_key: str, request_text: str, request_analysis: dict[str, object] | None = None, connection_id: str = "", channel_name: str = "") -> dict[str, object]:  # noqa: ARG002
        self.ran.append(request_text)
        return {
            "task_id": "coworker-1",
            "user_id": user_id,
            "session_key": session_key,
            "request_text": request_text,
            "status": "completed",
            "summary": "Open Task Manager and summarize system usage.",
            "steps": [
                {"type": "task_manager_open", "title": "Open Task Manager"},
                {"type": "task_manager_summary", "title": "Summarize CPU, memory, and disk usage"},
            ],
            "current_step_index": 2,
            "total_steps": 2,
            "latest_state": {"active_window": {"title": "Task Manager", "process_name": "Taskmgr"}},
            "transcript": [
                {"step_index": 0, "step_type": "task_manager_open", "summary": "Open Task Manager: completed."},
                {"step_index": 1, "step_type": "task_manager_summary", "summary": "Summarize CPU, memory, and disk usage: CPU 22.0%, memory and disk summary captured."},
            ],
        }

    async def run_task(self, *, user_id: str, task_id: str, connection_id: str = "", channel_name: str = "") -> dict[str, object]:
        return await self.run_task_request(user_id=user_id, session_key="webchat_main", request_text=task_id, connection_id=connection_id, channel_name=channel_name)

    async def step_task(self, *, user_id: str, task_id: str, connection_id: str = "", channel_name: str = "") -> dict[str, object]:
        return await self.run_task(user_id=user_id, task_id=task_id, connection_id=connection_id, channel_name=channel_name)

    async def get_task(self, *, user_id: str, task_id: str) -> dict[str, object]:
        return await self.run_task(user_id=user_id, task_id=task_id)

    async def stop_task(self, *, user_id: str, task_id: str) -> dict[str, object]:
        task = await self.run_task(user_id=user_id, task_id=task_id)
        task["status"] = "stopped"
        return task

    async def list_tasks(self, *, user_id: str, limit: int = 20) -> list[dict[str, object]]:
        return [
            {
                "task_id": "coworker-1",
                "summary": "Open Task Manager and summarize system usage.",
                "status": "completed",
                "current_step_index": 2,
                "total_steps": 2,
            }
        ]


class FakeSkill:
    def __init__(
        self,
        name: str,
        *,
        description: str = "Skill description",
        aliases: list[str] | None = None,
        activation_examples: list[str] | None = None,
        keywords: list[str] | None = None,
        user_invocable: bool = True,
        natural_language_enabled: bool = True,
    ) -> None:
        self.name = name
        self.description = description
        self.aliases = aliases or []
        self.activation_examples = activation_examples or []
        self.keywords = keywords or []
        self.user_invocable = user_invocable
        self.natural_language_enabled = natural_language_enabled
        self.priority = 0


class FakeSkillMatch:
    def __init__(self, skill, score: int, *, exact: bool = False) -> None:
        self.skill = skill
        self.score = score
        self.exact = exact


@pytest.mark.asyncio
async def test_router_shortcuts_latest_email_without_model(app_config) -> None:
    tool_registry = DummyToolRegistry()
    session_manager = DummySessionManager()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=session_manager,
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-1",
        request_id="req-1",
        session_key="webchat_main",
        message="what is the last mail i got",
        metadata={"trace_id": "trace-1"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is False
    assert "Latest email in your inbox" in response.payload["command_response"]
    assert tool_registry.calls[0][0] == "gmail_latest_email"
    assert [message["role"] for message in session_manager.messages] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_router_shortcuts_bare_mail_prefers_gmail_tool(app_config) -> None:
    tool_registry = DummyToolRegistry()
    session_manager = DummySessionManager()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=session_manager,
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-1",
        request_id="req-mail-short",
        session_key="webchat_main",
        message="mail",
        metadata={"trace_id": "trace-mail-short"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is False
    assert "Latest email in your inbox" in response.payload["command_response"]
    assert tool_registry.calls[0][0] == "gmail_latest_email"


@pytest.mark.asyncio
async def test_router_shortcuts_check_my_mails_uses_gmail_search(app_config) -> None:
    tool_registry = DummyToolRegistry()
    session_manager = DummySessionManager()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=session_manager,
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-1",
        request_id="req-mail-list",
        session_key="webchat_main",
        message="check my mails",
        metadata={"trace_id": "trace-mail-list"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is False
    assert "Here are your recent Gmail messages:" in response.payload["command_response"]
    assert tool_registry.calls[0][0] == "gmail_search"


@pytest.mark.asyncio
async def test_router_shortcuts_recent_mails_with_count_uses_gmail_search(app_config) -> None:
    tool_registry = DummyToolRegistry()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-1",
        request_id="req-mail-count",
        session_key="webchat_main",
        message="what are the 5 recent mails that i have received",
        metadata={"trace_id": "trace-mail-count"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is False
    assert "Here are your recent Gmail messages:" in response.payload["command_response"]
    assert tool_registry.calls[0][0] == "gmail_search"
    assert tool_registry.calls[0][1]["limit"] == 5


@pytest.mark.asyncio
async def test_router_shortcuts_send_mail_uses_gmail_send(app_config) -> None:
    tool_registry = DummyToolRegistry()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-1",
        request_id="req-mail-send",
        session_key="webchat_main",
        message="send a mail to ashish.232933105@vcet.edu.in with content hello",
        metadata={"trace_id": "trace-mail-send"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is False
    assert "Sent your Gmail message to ashish.232933105@vcet.edu.in." in response.payload["command_response"]
    assert tool_registry.calls[0][0] == "gmail_send"
    assert tool_registry.calls[0][1]["to"] == "ashish.232933105@vcet.edu.in"
    assert tool_registry.calls[0][1]["body"] == "hello"


@pytest.mark.asyncio
async def test_router_shortcuts_typoed_send_mail_still_uses_gmail_send_before_coworker(app_config) -> None:
    class EmailHijackCoworkerService(DummyCoworkerService):
        async def analyze_request(self, *, session_key: str, request_text: str) -> dict[str, object]:  # noqa: ARG002
            lowered = request_text.lower()
            if "mail" in lowered or "email" in lowered:
                return {
                    "desktop_ui_task": True,
                    "task_kind": "visual",
                    "summary": request_text.strip(),
                    "normalized_request": request_text.strip(),
                    "requires_visual_context": False,
                    "route_kind": "visual",
                }
            return await super().analyze_request(session_key=session_key, request_text=request_text)

    app_config.desktop_coworker.enabled = True
    coworker_service = EmailHijackCoworkerService()
    tool_registry = DummyToolRegistry()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
        coworker_service=coworker_service,
    )

    response = await router.route_user_message(
        connection_id="conn-1",
        request_id="req-mail-send-typo",
        session_key="webchat_main",
        message="ssend a mail to ashish.232933105@vcet.edu.in with content hello",
        metadata={"trace_id": "trace-mail-send-typo"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is False
    assert "Sent your Gmail message to ashish.232933105@vcet.edu.in." in response.payload["command_response"]
    assert tool_registry.calls[0][0] == "gmail_send"
    assert coworker_service.ran == []


@pytest.mark.asyncio
async def test_router_shortcuts_create_draft_uses_gmail_create_draft(app_config) -> None:
    tool_registry = DummyToolRegistry()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-1",
        request_id="req-mail-draft",
        session_key="webchat_main",
        message="create a draft email to ashish.232933105@vcet.edu.in with subject hello with content test body",
        metadata={"trace_id": "trace-mail-draft"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is False
    assert "Created a Gmail draft for ashish.232933105@vcet.edu.in." in response.payload["command_response"]
    assert tool_registry.calls[0][0] == "gmail_create_draft"
    assert tool_registry.calls[0][1]["subject"] == "hello"
    assert tool_registry.calls[0][1]["body"] == "test body"


@pytest.mark.asyncio
async def test_router_shortcuts_open_outlook_does_not_hit_gmail_tool(app_config) -> None:
    tool_registry = DummyToolRegistry()
    agent_loop = DummyAgentLoop()
    router = GatewayRouter(
        config=app_config,
        agent_loop=agent_loop,
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-1",
        request_id="req-outlook-open",
        session_key="webchat_main",
        message="open outlook",
        metadata={"trace_id": "trace-outlook-open"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert not any(tool_name == "gmail_latest_email" for tool_name, _payload in tool_registry.calls)
    assert len(agent_loop.enqueued) <= 1


@pytest.mark.asyncio
async def test_router_shortcuts_repo_count_without_model(app_config) -> None:
    tool_registry = DummyToolRegistry()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-1",
        request_id="req-2",
        session_key="webchat_main",
        message="how many repos do i have",
        metadata={"trace_id": "trace-2"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is False
    assert "2 repositories" in response.payload["command_response"]
    assert tool_registry.calls[0][0] == "github_list_repos"


@pytest.mark.asyncio
async def test_router_shortcuts_pull_request_check_uses_recent_repo_context(app_config) -> None:
    tool_registry = DummyToolRegistry()
    session_manager = DummySessionManager()
    await session_manager.append_message(
        session_manager.session,
        {
            "id": "msg-1",
            "role": "assistant",
            "content": "The repository is Rishiraj-Yadav/Personal-AI-Assistant.",
        },
    )
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=session_manager,
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-1",
        request_id="req-3",
        session_key="webchat_main",
        message="is there any pull request to this repo",
        metadata={"trace_id": "trace-3"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is False
    assert "Rishiraj-Yadav/Personal-AI-Assistant" in response.payload["command_response"]
    assert "#7: Improve routing" in response.payload["command_response"]
    assert tool_registry.calls[-1][0] == "github_list_pull_requests"


@pytest.mark.asyncio
async def test_router_shortcuts_create_pull_request_uses_real_github_write_flow(app_config) -> None:
    tool_registry = DummyToolRegistry(
        browser_active_url="https://github.com/Rishiraj-Yadav/Personal-AI-Assistant"
    )
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-pr-1",
        request_id="req-pr-1",
        session_key="webchat_main",
        message="create a pull request\ntitle: hello\nbranch: Nick\nbase branch: main\ndescription: testing pr",
        metadata={"trace_id": "trace-pr-1"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is False
    assert "Created pull request #12" in response.payload["command_response"]
    assert "Nick -> main" in response.payload["command_response"]
    assert [call[0] for call in tool_registry.calls[-3:]] == [
        "github_list_branches",
        "github_compare_branches",
        "github_create_pull_request",
    ]


@pytest.mark.asyncio
async def test_router_shortcuts_create_pull_request_followup_uses_recent_context(app_config) -> None:
    tool_registry = DummyToolRegistry(
        browser_active_url="https://github.com/Rishiraj-Yadav/Personal-AI-Assistant"
    )
    session_manager = DummySessionManager()
    await session_manager.append_message(
        session_manager.session,
        {
            "id": "msg-pr-1",
            "role": "assistant",
            "content": "I can create the pull request, but I still need: title, source branch, base branch.",
        },
    )
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=session_manager,
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-pr-2",
        request_id="req-pr-2",
        session_key="webchat_main",
        message="title: hello\nbranch: Nick\nbase branch: main\ndescription: testing pr",
        metadata={"trace_id": "trace-pr-2"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "Created pull request #12" in response.payload["command_response"]
    assert tool_registry.calls[-1][0] == "github_create_pull_request"


@pytest.mark.asyncio
async def test_router_shortcuts_win_before_skill_activation(app_config) -> None:
    tool_registry = DummyToolRegistry()
    skill = FakeSkill("gmail-triage", aliases=["check my inbox"], keywords=["inbox", "email"])
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(matches=[FakeSkillMatch(skill, 100, exact=True)]),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-1",
        request_id="req-4",
        session_key="webchat_main",
        message="what is the last email i received",
        metadata={"trace_id": "trace-4"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is False
    assert tool_registry.calls[0][0] == "gmail_latest_email"


@pytest.mark.asyncio
async def test_router_host_shortcut_lists_downloads(app_config) -> None:
    tool_registry = DummyToolRegistry(host_tools_enabled=True)
    session_manager = DummySessionManager()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=session_manager,
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-host-1",
        request_id="req-host-1",
        session_key="telegram:123",
        message="show me files in my Downloads",
        metadata={"trace_id": "trace-host-1", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is False
    assert "Downloads" in response.payload["command_response"]
    assert "Resume.pdf" in response.payload["command_response"]
    assert tool_registry.calls[0][0] == "list_host_dir"


@pytest.mark.asyncio
async def test_router_host_shortcut_lists_downloads_without_show_me_phrase(app_config) -> None:
    tool_registry = DummyToolRegistry(host_tools_enabled=True)
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-host-1b",
        request_id="req-host-1b",
        session_key="telegram:123",
        message="show files in my downloads",
        metadata={"trace_id": "trace-host-1b", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is False
    assert "Downloads" in response.payload["command_response"]
    assert tool_registry.calls[0][0] == "list_host_dir"


@pytest.mark.asyncio
async def test_router_host_shortcut_lists_desktop_for_content_phrase(app_config) -> None:
    tool_registry = DummyToolRegistry(host_tools_enabled=True)
    app_config.system_access.path_rules = [
        {
            "path": "C:/Users/Ritesh/OneDrive/Desktop",
            "read": "auto_allow",
            "write": "ask_once",
            "overwrite": "always_ask",
            "delete": "always_ask",
            "execute": "ask_once",
        }
    ]
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-host-1c",
        request_id="req-host-1c",
        session_key="telegram:123",
        message="what is the content of the desktop folder",
        metadata={"trace_id": "trace-host-1c", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "Desktop" in response.payload["command_response"]
    assert tool_registry.calls[0][0] == "list_host_dir"
    assert tool_registry.calls[0][1]["path"] == "C:/Users/Ritesh/OneDrive/Desktop"


@pytest.mark.asyncio
async def test_router_host_shortcut_lists_downloads_for_typo_content_phrase(app_config) -> None:
    tool_registry = DummyToolRegistry(host_tools_enabled=True)
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-host-1d",
        request_id="req-host-1d",
        session_key="telegram:123",
        message="what is the content if the download folder",
        metadata={"trace_id": "trace-host-1d", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "Downloads" in response.payload["command_response"]
    assert tool_registry.calls[0][0] == "list_host_dir"
    assert tool_registry.calls[0][1]["path"] == "~/Downloads"


@pytest.mark.asyncio
async def test_router_host_shortcut_lists_configured_download2_folder(app_config) -> None:
    tool_registry = DummyToolRegistry(host_tools_enabled=True)
    app_config.system_access.path_rules = [
        {
            "path": "R:/Download2",
            "read": "auto_allow",
            "write": "ask_once",
            "overwrite": "always_ask",
            "delete": "always_ask",
            "execute": "ask_once",
        }
    ]
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-host-1e",
        request_id="req-host-1e",
        session_key="telegram:123",
        message="what is the content of the download2",
        metadata={"trace_id": "trace-host-1e", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert tool_registry.calls[0][0] == "list_host_dir"
    assert tool_registry.calls[0][1]["path"] == "R:/Download2"


@pytest.mark.asyncio
async def test_router_host_shortcut_searches_named_folder(app_config) -> None:
    tool_registry = DummyToolRegistry(host_tools_enabled=True)
    session_manager = DummySessionManager()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=session_manager,
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-host-2",
        request_id="req-host-2",
        session_key="telegram:123",
        message="search 5sem folder",
        metadata={"trace_id": "trace-host-2", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "5sem" in response.payload["command_response"]
    assert "Documents/college/5sem" in response.payload["command_response"]
    assert tool_registry.calls[0][0] == "search_host_files"
    assert tool_registry.calls[0][1]["root"] == "@allowed"


@pytest.mark.asyncio
async def test_router_host_shortcut_searches_named_folder_on_r_drive(app_config) -> None:
    tool_registry = DummyToolRegistry(host_tools_enabled=True)
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-host-2b",
        request_id="req-host-2b",
        session_key="telegram:123",
        message="5 sem folder in R drive",
        metadata={"trace_id": "trace-host-2b", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "R:/" in response.payload["command_response"]
    assert tool_registry.calls[0][0] == "search_host_files"
    assert tool_registry.calls[0][1]["root"] == "R:/"


@pytest.mark.asyncio
async def test_router_host_shortcut_opens_named_folder_and_lists_contents(app_config) -> None:
    tool_registry = DummyToolRegistry(host_tools_enabled=True)
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-host-2bb",
        request_id="req-host-2bb",
        session_key="telegram:123",
        message="oepn spcc folder",
        metadata={"trace_id": "trace-host-2bb", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "R:/6_semester/SPCC" in response.payload["command_response"]
    assert [call[0] for call in tool_registry.calls[:2]] == ["search_host_files", "list_host_dir"]


@pytest.mark.asyncio
async def test_router_host_shortcut_lists_explicit_c_path(app_config) -> None:
    tool_registry = DummyToolRegistry(host_tools_enabled=True)
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-host-2bc",
        request_id="req-host-2bc",
        session_key="telegram:123",
        message="show me the files in C:/Users/Ritesh/Downloads",
        metadata={"trace_id": "trace-host-2bc", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "Resume.pdf" in response.payload["command_response"]
    assert tool_registry.calls[0][0] == "list_host_dir"
    assert tool_registry.calls[0][1]["path"] == "C:/Users/Ritesh/Downloads"


@pytest.mark.asyncio
async def test_router_host_shortcut_searches_explicit_c_path(app_config) -> None:
    tool_registry = DummyToolRegistry(host_tools_enabled=True)
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-host-2bd",
        request_id="req-host-2bd",
        session_key="telegram:123",
        message="look for report.pdf in C:/Users/Ritesh/Documents",
        metadata={"trace_id": "trace-host-2bd", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "report.pdf" in response.payload["command_response"]
    assert tool_registry.calls[0][0] == "search_host_files"
    assert tool_registry.calls[0][1]["root"] == "C:/Users/Ritesh/Documents"


@pytest.mark.asyncio
async def test_router_host_shortcut_lists_folder_contents_when_user_asks_whats_inside(app_config) -> None:
    tool_registry = DummyToolRegistry(host_tools_enabled=True)
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-host-2c",
        request_id="req-host-2c",
        session_key="telegram:123",
        message="can you find the 5sem folder and tell me what is there inside it",
        metadata={"trace_id": "trace-host-2c", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "I found the folder at" in response.payload["command_response"]
    assert "dbms/" in response.payload["command_response"]
    assert [call[0] for call in tool_registry.calls[:2]] == ["search_host_files", "list_host_dir"]


@pytest.mark.asyncio
async def test_router_host_shortcut_disambiguates_duplicate_named_folders(app_config) -> None:
    tool_registry = DummyToolRegistry(host_tools_enabled=True)
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-host-2d",
        request_id="req-host-2d",
        session_key="telegram:123",
        message="open archive folder",
        metadata={"trace_id": "trace-host-2d", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "multiple folders matching 'archive'" in response.payload["command_response"].lower()
    assert "R:/6_semester/Archive" in response.payload["command_response"]
    assert "C:/Users/Ritesh/Documents/Archive" in response.payload["command_response"]
    assert [call[0] for call in tool_registry.calls] == ["search_host_files"]


@pytest.mark.asyncio
async def test_router_host_shortcut_opens_notepad(app_config) -> None:
    tool_registry = DummyToolRegistry(host_tools_enabled=True)
    session_manager = DummySessionManager()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=session_manager,
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-host-3",
        request_id="req-host-3",
        session_key="telegram:123",
        message="can you open notepad",
        metadata={"trace_id": "trace-host-3", "user_id": "default", "channel": "telegram"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "launched notepad" in response.payload["command_response"].lower()
    assert tool_registry.calls[0][0] == "exec_shell"
    assert tool_registry.calls[0][1]["host"] is True


@pytest.mark.asyncio
async def test_router_app_shortcut_returns_clear_message_when_disabled(app_config) -> None:
    tool_registry = DummyToolRegistry(app_tools_enabled=True)
    session_manager = DummySessionManager()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=session_manager,
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-app-disabled-1",
        request_id="req-app-disabled-1",
        session_key="telegram:123",
        message="maximize word",
        metadata={"trace_id": "trace-app-disabled-1", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["command_response"] == "Desktop app control is not enabled."
    assert tool_registry.calls == []


@pytest.mark.asyncio
async def test_router_screen_shortcut_returns_clear_message_when_disabled(app_config) -> None:
    tool_registry = DummyToolRegistry(screen_tools_enabled=True)
    session_manager = DummySessionManager()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=session_manager,
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-screen-disabled-1",
        request_id="req-screen-disabled-1",
        session_key="telegram:123",
        message="what app is active",
        metadata={"trace_id": "trace-screen-disabled-1", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["command_response"] == "Desktop vision is not enabled."
    assert tool_registry.calls == []


@pytest.mark.asyncio
async def test_router_screen_slash_command_flow(app_config) -> None:
    app_config.desktop_vision.enabled = True
    tool_registry = DummyToolRegistry(screen_tools_enabled=True)
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    active_response = await router.route_user_message(
        connection_id="conn-screen-1",
        request_id="req-screen-1",
        session_key="telegram:123",
        message="/screen active",
        metadata={"trace_id": "trace-screen-1", "user_id": "default"},
        mode=QueueMode.STEER,
    )
    capture_response = await router.route_user_message(
        connection_id="conn-screen-2",
        request_id="req-screen-2",
        session_key="telegram:123",
        message="/screen capture",
        metadata={"trace_id": "trace-screen-2", "user_id": "default"},
        mode=QueueMode.STEER,
    )
    window_response = await router.route_user_message(
        connection_id="conn-screen-3",
        request_id="req-screen-3",
        session_key="telegram:123",
        message="/screen window",
        metadata={"trace_id": "trace-screen-3", "user_id": "default"},
        mode=QueueMode.STEER,
    )
    read_response = await router.route_user_message(
        connection_id="conn-screen-4",
        request_id="req-screen-4",
        session_key="telegram:123",
        message="/screen read window",
        metadata={"trace_id": "trace-screen-4", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert active_response.ok is True
    assert "Active window: Visual Studio Code" in active_response.payload["command_response"]
    assert capture_response.ok is True
    assert "Captured a desktop screenshot." in capture_response.payload["command_response"]
    assert window_response.ok is True
    assert "Captured an active window screenshot." in window_response.payload["command_response"]
    assert read_response.ok is True
    assert "Read the window." in read_response.payload["command_response"]
    assert [call[0] for call in tool_registry.calls] == [
        "desktop_active_window",
        "desktop_screenshot",
        "desktop_window_screenshot",
        "desktop_read_screen",
    ]


@pytest.mark.asyncio
async def test_router_handles_natural_language_screen_shortcuts(app_config) -> None:
    app_config.desktop_vision.enabled = True
    tool_registry = DummyToolRegistry(screen_tools_enabled=True)
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    active_response = await router.route_user_message(
        connection_id="conn-screen-nl-1",
        request_id="req-screen-nl-1",
        session_key="telegram:123",
        message="what app is active",
        metadata={"trace_id": "trace-screen-nl-1", "user_id": "default"},
        mode=QueueMode.STEER,
    )
    capture_response = await router.route_user_message(
        connection_id="conn-screen-nl-2",
        request_id="req-screen-nl-2",
        session_key="telegram:123",
        message="take a screenshot of my desktop",
        metadata={"trace_id": "trace-screen-nl-2", "user_id": "default"},
        mode=QueueMode.STEER,
    )
    read_response = await router.route_user_message(
        connection_id="conn-screen-nl-3",
        request_id="req-screen-nl-3",
        session_key="telegram:123",
        message="read the active window",
        metadata={"trace_id": "trace-screen-nl-3", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert active_response.ok is True
    assert "Active window: Visual Studio Code" in active_response.payload["command_response"]
    assert capture_response.ok is True
    assert "Captured a desktop screenshot." in capture_response.payload["command_response"]
    assert read_response.ok is True
    assert "Read the window." in read_response.payload["command_response"]
    assert [call[0] for call in tool_registry.calls] == [
        "desktop_active_window",
        "desktop_screenshot",
        "desktop_read_screen",
    ]


@pytest.mark.asyncio
async def test_router_input_shortcut_returns_clear_message_when_disabled(app_config) -> None:
    tool_registry = DummyToolRegistry(input_tools_enabled=True)
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-input-disabled-1",
        request_id="req-input-disabled-1",
        session_key="telegram:123",
        message="move mouse to 400 300",
        metadata={"trace_id": "trace-input-disabled-1", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["command_response"] == "Desktop input is not enabled."
    assert tool_registry.calls == []


@pytest.mark.asyncio
async def test_router_input_slash_command_flow(app_config) -> None:
    app_config.desktop_input.enabled = True
    tool_registry = DummyToolRegistry(input_tools_enabled=True)
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    position_response = await router.route_user_message(
        connection_id="conn-input-1",
        request_id="req-input-1",
        session_key="telegram:123",
        message="/input position",
        metadata={"trace_id": "trace-input-1", "user_id": "default"},
        mode=QueueMode.STEER,
    )
    click_response = await router.route_user_message(
        connection_id="conn-input-2",
        request_id="req-input-2",
        session_key="telegram:123",
        message="/input click 400 300",
        metadata={"trace_id": "trace-input-2", "user_id": "default"},
        mode=QueueMode.STEER,
    )
    type_response = await router.route_user_message(
        connection_id="conn-input-3",
        request_id="req-input-3",
        session_key="telegram:123",
        message="/input type hello world",
        metadata={"trace_id": "trace-input-3", "user_id": "default"},
        mode=QueueMode.STEER,
    )
    clipboard_response = await router.route_user_message(
        connection_id="conn-input-4",
        request_id="req-input-4",
        session_key="telegram:123",
        message="/clipboard get",
        metadata={"trace_id": "trace-input-4", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert position_response.ok is True
    assert "Cursor position: (400, 300)" in position_response.payload["command_response"]
    assert click_response.ok is True
    assert "Clicked at (400, 300)." in click_response.payload["command_response"]
    assert type_response.ok is True
    assert "Typed 11 character(s)" in type_response.payload["command_response"]
    assert clipboard_response.ok is True
    assert "Clipboard text:" in clipboard_response.payload["command_response"]
    assert [call[0] for call in tool_registry.calls] == [
        "desktop_mouse_position",
        "desktop_mouse_click",
        "desktop_keyboard_type",
        "desktop_clipboard_read",
    ]


@pytest.mark.asyncio
async def test_router_handles_natural_language_input_shortcuts(app_config) -> None:
    app_config.desktop_input.enabled = True
    tool_registry = DummyToolRegistry(input_tools_enabled=True)
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    move_response = await router.route_user_message(
        connection_id="conn-input-nl-1",
        request_id="req-input-nl-1",
        session_key="telegram:123",
        message="move mouse to 400 300",
        metadata={"trace_id": "trace-input-nl-1", "user_id": "default"},
        mode=QueueMode.STEER,
    )
    hotkey_response = await router.route_user_message(
        connection_id="conn-input-nl-2",
        request_id="req-input-nl-2",
        session_key="telegram:123",
        message="press ctrl c",
        metadata={"trace_id": "trace-input-nl-2", "user_id": "default"},
        mode=QueueMode.STEER,
    )
    copy_response = await router.route_user_message(
        connection_id="conn-input-nl-3",
        request_id="req-input-nl-3",
        session_key="telegram:123",
        message="copy selected text",
        metadata={"trace_id": "trace-input-nl-3", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert move_response.ok is True
    assert "Moved the mouse to (400, 300)." in move_response.payload["command_response"]
    assert hotkey_response.ok is True
    assert "pressed ctrl+c." in hotkey_response.payload["command_response"].lower()
    assert copy_response.ok is True
    assert "Clipboard text:" in copy_response.payload["command_response"]
    assert [call[0] for call in tool_registry.calls] == [
        "desktop_mouse_move",
        "desktop_keyboard_hotkey",
        "desktop_keyboard_hotkey",
        "desktop_clipboard_read",
    ]


@pytest.mark.asyncio
async def test_router_apps_slash_command_flow(app_config) -> None:
    app_config.desktop_apps.enabled = True
    tool_registry = DummyToolRegistry(app_tools_enabled=True)
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    list_response = await router.route_user_message(
        connection_id="conn-apps-1",
        request_id="req-apps-1",
        session_key="telegram:123",
        message="/apps list",
        metadata={"trace_id": "trace-apps-1", "user_id": "default"},
        mode=QueueMode.STEER,
    )
    open_response = await router.route_user_message(
        connection_id="conn-apps-2",
        request_id="req-apps-2",
        session_key="telegram:123",
        message="/apps open chrome",
        metadata={"trace_id": "trace-apps-2", "user_id": "default"},
        mode=QueueMode.STEER,
    )
    focus_response = await router.route_user_message(
        connection_id="conn-apps-3",
        request_id="req-apps-3",
        session_key="telegram:123",
        message="/apps focus vscode",
        metadata={"trace_id": "trace-apps-3", "user_id": "default"},
        mode=QueueMode.STEER,
    )
    maximize_response = await router.route_user_message(
        connection_id="conn-apps-4",
        request_id="req-apps-4",
        session_key="telegram:123",
        message="/apps maximize word",
        metadata={"trace_id": "trace-apps-4", "user_id": "default"},
        mode=QueueMode.STEER,
    )
    left_response = await router.route_user_message(
        connection_id="conn-apps-5",
        request_id="req-apps-5",
        session_key="telegram:123",
        message="/apps left chrome",
        metadata={"trace_id": "trace-apps-5", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert list_response.ok is True
    assert "Visible app windows:" in list_response.payload["command_response"]
    assert "Visual Studio Code" in list_response.payload["command_response"]
    assert open_response.ok is True
    assert "Launched chrome" in open_response.payload["command_response"]
    assert focus_response.ok is True
    assert "Focused 'Vscode'." in focus_response.payload["command_response"]
    assert maximize_response.ok is True
    assert "Maximized 'Word'." in maximize_response.payload["command_response"]
    assert left_response.ok is True
    assert "Snapped 'Chrome' to the left side." in left_response.payload["command_response"]
    assert [call[0] for call in tool_registry.calls] == [
        "apps_list_windows",
        "apps_open",
        "apps_focus",
        "apps_maximize",
        "apps_snap",
    ]


@pytest.mark.asyncio
async def test_router_handles_natural_language_app_shortcuts(app_config) -> None:
    app_config.desktop_apps.enabled = True
    tool_registry = DummyToolRegistry(app_tools_enabled=True)
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    open_response = await router.route_user_message(
        connection_id="conn-app-nl-1",
        request_id="req-app-nl-1",
        session_key="telegram:123",
        message="open chrome",
        metadata={"trace_id": "trace-app-nl-1", "user_id": "default"},
        mode=QueueMode.STEER,
    )
    focus_response = await router.route_user_message(
        connection_id="conn-app-nl-2",
        request_id="req-app-nl-2",
        session_key="telegram:123",
        message="switch to vscode",
        metadata={"trace_id": "trace-app-nl-2", "user_id": "default"},
        mode=QueueMode.STEER,
    )
    snap_response = await router.route_user_message(
        connection_id="conn-app-nl-3",
        request_id="req-app-nl-3",
        session_key="telegram:123",
        message="put chrome on left",
        metadata={"trace_id": "trace-app-nl-3", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert open_response.ok is True
    assert "Launched chrome" in open_response.payload["command_response"]
    assert focus_response.ok is True
    assert "Focused 'Vscode'." in focus_response.payload["command_response"]
    assert snap_response.ok is True
    assert "Snapped 'Chrome' to the left side." in snap_response.payload["command_response"]
    assert [call[0] for call in tool_registry.calls] == ["apps_open", "apps_focus", "apps_snap"]


@pytest.mark.asyncio
async def test_router_host_shortcut_creates_desktop_note_when_filename_and_content_are_given(app_config) -> None:
    tool_registry = DummyToolRegistry(host_tools_enabled=True)
    session_manager = DummySessionManager()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=session_manager,
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-host-4",
        request_id="req-host-4",
        session_key="telegram:123",
        message="create a note called todo on my Desktop with content Buy milk",
        metadata={"trace_id": "trace-host-4", "user_id": "default", "channel": "telegram"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "created todo.txt in your desktop" in response.payload["command_response"].lower()
    assert tool_registry.calls[0][0] == "write_host_file"
    assert tool_registry.calls[0][1]["path"] == "~/Desktop/todo.txt"


@pytest.mark.asyncio
async def test_router_host_shortcut_creates_file_in_recent_host_folder_context(app_config) -> None:
    tool_registry = DummyToolRegistry(host_tools_enabled=True)
    session_manager = DummySessionManager()
    await session_manager.append_message(
        session_manager.session,
        {
            "id": "msg-host-folder-1",
            "role": "assistant",
            "content": "Here's what's inside the R:\\6_semester\\SPCC folder:\n- SPCC Experiment 5.pdf",
        },
    )
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=session_manager,
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-host-4b",
        request_id="req-host-4b",
        session_key="telegram:123",
        message="Create a timepass.txt file there in which it is written hello world",
        metadata={"trace_id": "trace-host-4b", "user_id": "default", "channel": "telegram"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "created timepass.txt in your r:\\6_semester\\spcc" in response.payload["command_response"].lower()
    assert tool_registry.calls[0][0] == "write_host_file"
    assert tool_registry.calls[0][1]["path"] == "R:\\6_semester\\SPCC/timepass.txt"
    assert tool_registry.calls[0][1]["content"] == "hello world"


@pytest.mark.asyncio
async def test_router_host_shortcut_creates_file_with_inside_this_after_folder_listing(app_config) -> None:
    tool_registry = DummyToolRegistry(host_tools_enabled=True)
    session_manager = DummySessionManager()
    await session_manager.append_message(
        session_manager.session,
        {
            "id": "msg-host-folder-2",
            "role": "assistant",
            "content": "Here's what's inside the C:/Users/Ritesh/OneDrive/Desktop folder:\n- Cursor.lnk",
        },
    )
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=session_manager,
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-host-4bb",
        request_id="req-host-4bb",
        session_key="telegram:123",
        message="create a new.pdf file with the content hello world inside this",
        metadata={"trace_id": "trace-host-4bb", "user_id": "default", "channel": "telegram"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert tool_registry.calls[0][0] == "write_host_file"
    assert tool_registry.calls[0][1]["path"] == "C:/Users/Ritesh/OneDrive/Desktop/new.pdf"


@pytest.mark.asyncio
async def test_router_host_shortcut_creates_file_with_in_this_folder_after_r_drive_listing(app_config) -> None:
    tool_registry = DummyToolRegistry(host_tools_enabled=True)
    session_manager = DummySessionManager()
    await session_manager.append_message(
        session_manager.session,
        {
            "id": "msg-host-folder-r-1",
            "role": "assistant",
            "content": "Here is the content of the `5_sem` folder on your R drive:\n\n**Files:**\n\n*   testing.txt",
        },
    )
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=session_manager,
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-host-4bb-r",
        request_id="req-host-4bb-r",
        session_key="telegram:123",
        message="create time.pdf file with content python is running in this folder",
        metadata={"trace_id": "trace-host-4bb-r", "user_id": "default", "channel": "telegram"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert tool_registry.calls[0][0] == "search_host_files"
    assert tool_registry.calls[1][0] == "write_host_file"
    assert tool_registry.calls[1][1]["path"] == "R:/college/5sem/time.pdf"


@pytest.mark.asyncio
async def test_router_host_shortcut_creates_file_in_named_folder_context(app_config) -> None:
    tool_registry = DummyToolRegistry(host_tools_enabled=True)
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-host-4c",
        request_id="req-host-4c",
        session_key="telegram:123",
        message="create a timepass.py file in the SPCC folder with content print('hello')",
        metadata={"trace_id": "trace-host-4c", "user_id": "default", "channel": "telegram"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "created timepass.py in your r:/6_semester/spcc" in response.payload["command_response"].lower()
    assert tool_registry.calls[0][0] == "search_host_files"
    assert tool_registry.calls[1][0] == "write_host_file"
    assert tool_registry.calls[1][1]["path"] == "R:/6_semester/SPCC/timepass.py"
    assert tool_registry.calls[1][1]["content"] == "print('hello')"


@pytest.mark.asyncio
async def test_router_host_shortcut_creates_pdf_file_when_extension_is_provided(app_config) -> None:
    tool_registry = DummyToolRegistry(host_tools_enabled=True)
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-host-4d",
        request_id="req-host-4d",
        session_key="telegram:123",
        message="create a report.pdf file in the SPCC folder with content hello world",
        metadata={"trace_id": "trace-host-4d", "user_id": "default", "channel": "telegram"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert tool_registry.calls[1][0] == "write_host_file"
    assert tool_registry.calls[1][1]["path"] == "R:/6_semester/SPCC/report.pdf"


@pytest.mark.asyncio
async def test_router_host_shortcut_creates_file_in_named_r_drive_folder_without_being_treated_as_search(app_config) -> None:
    tool_registry = DummyToolRegistry(host_tools_enabled=True)
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-host-4de",
        request_id="req-host-4de",
        session_key="telegram:123",
        message="create a new.pdf file with content hello world into the 6 semester folder in R drive",
        metadata={"trace_id": "trace-host-4de", "user_id": "default", "channel": "telegram"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert [call[0] for call in tool_registry.calls[:2]] == ["search_host_files", "write_host_file"]
    assert tool_registry.calls[1][1]["path"] == "R:/6_semester/new.pdf"


@pytest.mark.asyncio
async def test_router_host_shortcut_creates_file_in_explicit_c_path(app_config) -> None:
    tool_registry = DummyToolRegistry(host_tools_enabled=True)
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-host-4e",
        request_id="req-host-4e",
        session_key="telegram:123",
        message="create a todo.txt file in C:/Users/Ritesh/Documents with content buy milk",
        metadata={"trace_id": "trace-host-4e", "user_id": "default", "channel": "telegram"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert tool_registry.calls[0][0] == "write_host_file"
    assert tool_registry.calls[0][1]["path"] == "C:/Users/Ritesh/Documents/todo.txt"
    assert tool_registry.calls[0][1]["content"] == "buy milk"


@pytest.mark.asyncio
async def test_router_host_shortcut_creates_file_in_configured_desktop_without_folder_word(app_config) -> None:
    tool_registry = DummyToolRegistry(host_tools_enabled=True)
    app_config.system_access.path_rules = [
        {
            "path": "C:/Users/Ritesh/OneDrive/Desktop",
            "read": "auto_allow",
            "write": "ask_once",
            "overwrite": "always_ask",
            "delete": "always_ask",
            "execute": "ask_once",
        }
    ]
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-host-4ea",
        request_id="req-host-4ea",
        session_key="telegram:123",
        message="create a new.pdf file with the content hello world inside desktop",
        metadata={"trace_id": "trace-host-4ea", "user_id": "default", "channel": "telegram"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert tool_registry.calls[0][0] == "write_host_file"
    assert tool_registry.calls[0][1]["path"] == "C:/Users/Ritesh/OneDrive/Desktop/new.pdf"


@pytest.mark.asyncio
async def test_router_host_shortcut_creates_file_in_configured_download2_without_folder_word(app_config) -> None:
    tool_registry = DummyToolRegistry(host_tools_enabled=True)
    app_config.system_access.path_rules = [
        {
            "path": "R:/Download2",
            "read": "auto_allow",
            "write": "ask_once",
            "overwrite": "always_ask",
            "delete": "always_ask",
            "execute": "ask_once",
        }
    ]
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-host-4eb",
        request_id="req-host-4eb",
        session_key="telegram:123",
        message="create a new.pdf file with the content hello world inside download2",
        metadata={"trace_id": "trace-host-4eb", "user_id": "default", "channel": "telegram"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert tool_registry.calls[0][0] == "write_host_file"
    assert tool_registry.calls[0][1]["path"] == "R:/Download2/new.pdf"


@pytest.mark.asyncio
async def test_router_host_shortcut_reads_file_from_named_folder_in_r_drive(app_config) -> None:
    tool_registry = DummyToolRegistry(host_tools_enabled=True)
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-host-read-1",
        request_id="req-host-read-1",
        session_key="telegram:123",
        message="open the testing.txt file in the 5_sem folder in R drive",
        metadata={"trace_id": "trace-host-read-1", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "hello rishiraj this is the testing file" in response.payload["command_response"]
    assert [call[0] for call in tool_registry.calls[:2]] == ["search_host_files", "read_host_file"]
    assert tool_registry.calls[1][1]["path"] == "R:/college/5sem/testing.txt"


@pytest.mark.asyncio
async def test_router_host_shortcut_reads_file_from_recent_host_folder_context(app_config) -> None:
    tool_registry = DummyToolRegistry(host_tools_enabled=True)
    session_manager = DummySessionManager()
    await session_manager.append_message(
        session_manager.session,
        {
            "id": "msg-host-read-ctx-1",
            "role": "assistant",
            "content": "Here's what's inside the R:/5_SEM folder:\n- testing.txt",
        },
    )
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=session_manager,
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-host-read-2",
        request_id="req-host-read-2",
        session_key="telegram:123",
        message="what is the content of the testing.txt file",
        metadata={"trace_id": "trace-host-read-2", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "hello rishiraj this is the testing file" in response.payload["command_response"]
    assert tool_registry.calls[0][0] == "read_host_file"
    assert tool_registry.calls[0][1]["path"] == "R:/5_SEM/testing.txt"


@pytest.mark.asyncio
async def test_router_host_shortcut_overwrites_existing_file_in_named_folder(app_config) -> None:
    tool_registry = DummyToolRegistry(host_tools_enabled=True)
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-host-write-ovr-1",
        request_id="req-host-write-ovr-1",
        session_key="telegram:123",
        message="overwrite testing.txt in the 5_sem folder in R drive with content updated homework reminder",
        metadata={"trace_id": "trace-host-write-ovr-1", "user_id": "default", "channel": "telegram"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert [call[0] for call in tool_registry.calls[:2]] == ["search_host_files", "write_host_file"]
    assert tool_registry.calls[1][1]["path"] == "R:/college/5sem/testing.txt"
    assert tool_registry.calls[1][1]["content"] == "updated homework reminder"


@pytest.mark.asyncio
async def test_router_host_shortcut_updates_recently_read_host_file_with_pronoun_reference(app_config) -> None:
    tool_registry = DummyToolRegistry(host_tools_enabled=True)
    session_manager = DummySessionManager()
    await session_manager.append_message(
        session_manager.session,
        {
            "id": "msg-host-folder-c-practice",
            "role": "assistant",
            "content": "Here is the content of the `C practice` folder on your R drive:\n- testing123.docx",
        },
    )
    await session_manager.append_message(
        session_manager.session,
        {
            "id": "msg-host-file-c-practice",
            "role": "assistant",
            "content": "Here is the content of `testing123.docx`:\n\nabcd",
        },
    )
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=session_manager,
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-host-update-1",
        request_id="req-host-update-1",
        session_key="telegram:123",
        message="change it to xyz",
        metadata={"trace_id": "trace-host-update-1", "user_id": "default", "channel": "telegram"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert tool_registry.calls[0][0] == "search_host_files"
    assert tool_registry.calls[1][0] == "write_host_file"
    assert tool_registry.calls[1][1]["path"] == "R:/C practice/testing123.docx"
    assert tool_registry.calls[1][1]["content"] == "xyz"


@pytest.mark.asyncio
async def test_router_creates_desktop_file_watch_rule_from_natural_language(app_config) -> None:
    app_config.automation.desktop.enabled = True
    automation_engine = DummyAutomationEngine()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(host_tools_enabled=True),
        automation_engine=automation_engine,
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-desktop-auto-1",
        request_id="req-desktop-auto-1",
        session_key="telegram:123",
        message="when a pdf file appears in download2, move it to documents/pdfs",
        metadata={"trace_id": "trace-desktop-auto-1", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert automation_engine.desktop_rules
    assert automation_engine.desktop_rules[0]["trigger_type"] == "file_watch"
    assert automation_engine.desktop_rules[0]["action_type"] == "move"
    assert automation_engine.desktop_rules[0]["file_extensions"] == ["pdf"]


@pytest.mark.asyncio
async def test_router_creates_desktop_schedule_rule_from_natural_language(app_config) -> None:
    app_config.automation.desktop.enabled = True
    app_config.system_access.path_rules = [
        {
            "path": "C:/Users/Ritesh/OneDrive/Desktop",
            "read": "auto_allow",
            "write": "ask_once",
            "overwrite": "always_ask",
            "delete": "always_ask",
            "execute": "ask_once",
        }
    ]
    automation_engine = DummyAutomationEngine()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(host_tools_enabled=True),
        automation_engine=automation_engine,
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-desktop-auto-2",
        request_id="req-desktop-auto-2",
        session_key="telegram:123",
        message="every weekday at 9 am organize my desktop",
        metadata={"trace_id": "trace-desktop-auto-2", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert automation_engine.desktop_rules
    assert automation_engine.desktop_rules[0]["trigger_type"] == "schedule"
    assert automation_engine.desktop_rules[0]["action_type"] == "organize"


@pytest.mark.asyncio
async def test_router_creates_desktop_watch_notify_rule_from_broader_phrase(app_config) -> None:
    app_config.automation.desktop.enabled = True
    automation_engine = DummyAutomationEngine()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(host_tools_enabled=True),
        automation_engine=automation_engine,
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-desktop-auto-3",
        request_id="req-desktop-auto-3",
        session_key="telegram:123",
        message="watch download2 and notify me for new zip files",
        metadata={"trace_id": "trace-desktop-auto-3", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert automation_engine.desktop_rules
    assert automation_engine.desktop_rules[0]["trigger_type"] == "file_watch"
    assert automation_engine.desktop_rules[0]["action_type"] == "notify"
    assert automation_engine.desktop_rules[0]["file_extensions"] == ["zip"]


@pytest.mark.asyncio
async def test_router_creates_desktop_schedule_rule_from_named_time_phrase(app_config) -> None:
    app_config.automation.desktop.enabled = True
    app_config.system_access.path_rules = [
        {
            "path": "C:/Users/Ritesh/OneDrive/Desktop",
            "read": "auto_allow",
            "write": "ask_once",
            "overwrite": "always_ask",
            "delete": "always_ask",
            "execute": "ask_once",
        }
    ]
    automation_engine = DummyAutomationEngine()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(host_tools_enabled=True),
        automation_engine=automation_engine,
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-desktop-auto-4",
        request_id="req-desktop-auto-4",
        session_key="telegram:123",
        message="every night organize my desktop",
        metadata={"trace_id": "trace-desktop-auto-4", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert automation_engine.desktop_rules
    assert automation_engine.desktop_rules[0]["trigger_type"] == "schedule"
    assert automation_engine.desktop_rules[0]["schedule"] == "0 21 * * *"


@pytest.mark.asyncio
async def test_router_lists_desktop_automations_from_natural_language(app_config) -> None:
    app_config.automation.desktop.enabled = True
    automation_engine = DummyAutomationEngine()
    automation_engine.desktop_rules = [
        {
            "rule_id": "desktop-rule-1",
            "name": "desktop:desktop-rule-1",
            "display_name": "Watch Download2",
            "trigger": "desktop",
            "trigger_type": "file_watch",
            "watch_path": "R:/Download2",
            "file_extensions": ["zip"],
            "action_type": "notify",
            "paused": False,
        }
    ]
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(host_tools_enabled=True),
        automation_engine=automation_engine,
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-desktop-list-1",
        request_id="req-desktop-list-1",
        session_key="telegram:123",
        message="list my desktop automations",
        metadata={"trace_id": "trace-desktop-list-1", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "Desktop automations:" in response.payload["command_response"]
    assert "Watch Download2" in response.payload["command_response"]


@pytest.mark.asyncio
async def test_router_pauses_desktop_automation_from_natural_language(app_config) -> None:
    app_config.automation.desktop.enabled = True
    automation_engine = DummyAutomationEngine()
    automation_engine.desktop_rules = [
        {
            "rule_id": "desktop-rule-1",
            "name": "desktop:desktop-rule-1",
            "display_name": "Watch Download2",
            "trigger": "desktop",
            "trigger_type": "file_watch",
            "watch_path": "R:/Download2",
            "file_extensions": ["zip"],
            "action_type": "notify",
            "paused": False,
        }
    ]
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(host_tools_enabled=True),
        automation_engine=automation_engine,
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-desktop-pause-1",
        request_id="req-desktop-pause-1",
        session_key="telegram:123",
        message="pause desktop automation watch download2",
        metadata={"trace_id": "trace-desktop-pause-1", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "Paused desktop automation 'Watch Download2'." in response.payload["command_response"]
    assert automation_engine.desktop_rules[0]["paused"] is True


@pytest.mark.asyncio
async def test_router_desktop_slash_command_flow(app_config) -> None:
    app_config.automation.desktop.enabled = True
    automation_engine = DummyAutomationEngine()
    automation_engine.desktop_rules = [
        {
            "rule_id": "desktop-rule-1",
            "name": "desktop:desktop-rule-1",
            "display_name": "Watch Download2",
            "trigger": "desktop",
            "trigger_type": "file_watch",
            "watch_path": "R:/Download2",
            "file_extensions": ["zip"],
            "action_type": "notify",
            "paused": False,
        }
    ]
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(host_tools_enabled=True),
        automation_engine=automation_engine,
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    list_response = await router.route_user_message(
        connection_id="conn-desktop-cmd-1",
        request_id="req-desktop-cmd-1",
        session_key="telegram:123",
        message="/desktop list",
        metadata={"trace_id": "trace-desktop-cmd-1", "user_id": "default"},
        mode=QueueMode.STEER,
    )
    pause_response = await router.route_user_message(
        connection_id="conn-desktop-cmd-2",
        request_id="req-desktop-cmd-2",
        session_key="telegram:123",
        message="/desktop pause Watch Download2",
        metadata={"trace_id": "trace-desktop-cmd-2", "user_id": "default"},
        mode=QueueMode.STEER,
    )
    resume_response = await router.route_user_message(
        connection_id="conn-desktop-cmd-3",
        request_id="req-desktop-cmd-3",
        session_key="telegram:123",
        message="/desktop resume Watch Download2",
        metadata={"trace_id": "trace-desktop-cmd-3", "user_id": "default"},
        mode=QueueMode.STEER,
    )
    delete_response = await router.route_user_message(
        connection_id="conn-desktop-cmd-4",
        request_id="req-desktop-cmd-4",
        session_key="telegram:123",
        message="/desktop delete Watch Download2",
        metadata={"trace_id": "trace-desktop-cmd-4", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert list_response.ok is True
    assert "Watch Download2" in list_response.payload["command_response"]
    assert pause_response.ok is True
    assert "Paused desktop automation 'Watch Download2'." in pause_response.payload["command_response"]
    assert resume_response.ok is True
    assert "Resumed desktop automation 'Watch Download2'." in resume_response.payload["command_response"]
    assert delete_response.ok is True
    assert "Deleted desktop automation 'Watch Download2'." in delete_response.payload["command_response"]
    assert automation_engine.desktop_rules == []


@pytest.mark.asyncio
async def test_router_creates_manual_desktop_routine_from_natural_language(app_config) -> None:
    app_config.automation.desktop.enabled = True
    app_config.desktop_apps.enabled = True
    app_config.system_access.path_rules = [
        {
            "path": "R:/",
            "read": "auto_allow",
            "write": "ask_once",
            "overwrite": "always_ask",
            "delete": "always_ask",
            "execute": "ask_once",
        }
    ]
    automation_engine = DummyAutomationEngine()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(host_tools_enabled=True, app_tools_enabled=True),
        automation_engine=automation_engine,
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-routine-create-1",
        request_id="req-routine-create-1",
        session_key="telegram:123",
        message="create a study mode that opens chrome and 6_semester folder",
        metadata={"trace_id": "trace-routine-create-1", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "Created desktop routine 'study mode'." in response.payload["command_response"]
    assert automation_engine.desktop_routines
    assert automation_engine.desktop_routines[0]["trigger_type"] == "manual"
    assert [step["type"] for step in automation_engine.desktop_routines[0]["steps"]] == ["open_app", "open_host_path"]


@pytest.mark.asyncio
async def test_router_creates_reminder_desktop_routine_from_natural_language(app_config) -> None:
    app_config.automation.desktop.enabled = True
    app_config.system_access.path_rules = [
        {
            "path": "R:/",
            "read": "auto_allow",
            "write": "ask_once",
            "overwrite": "always_ask",
            "delete": "always_ask",
            "execute": "ask_once",
        }
    ]
    automation_engine = DummyAutomationEngine()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(host_tools_enabled=True),
        automation_engine=automation_engine,
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-routine-create-2",
        request_id="req-routine-create-2",
        session_key="telegram:123",
        message="remind me tomorrow at 8 pm to study and open 6_semester folder",
        metadata={"trace_id": "trace-routine-create-2", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "Created desktop routine 'study'." in response.payload["command_response"]
    assert automation_engine.desktop_routines
    assert automation_engine.desktop_routines[0]["trigger_type"] == "reminder"
    assert automation_engine.desktop_routines[0]["run_at"]


@pytest.mark.asyncio
async def test_router_creates_file_watch_desktop_routine_from_natural_language(app_config) -> None:
    app_config.automation.desktop.enabled = True
    app_config.system_access.path_rules = [
        {
            "path": "R:/Download2",
            "read": "auto_allow",
            "write": "ask_once",
            "overwrite": "always_ask",
            "delete": "always_ask",
            "execute": "ask_once",
        },
        {
            "path": "C:/Users/Ritesh/OneDrive/Documents",
            "read": "auto_allow",
            "write": "ask_once",
            "overwrite": "always_ask",
            "delete": "always_ask",
            "execute": "ask_once",
        },
    ]
    automation_engine = DummyAutomationEngine()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(host_tools_enabled=True),
        automation_engine=automation_engine,
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-routine-create-3",
        request_id="req-routine-create-3",
        session_key="telegram:123",
        message="when a pdf file appears in download2, move it to documents and notify me",
        metadata={"trace_id": "trace-routine-create-3", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "Created desktop routine 'Process Download2'." in response.payload["command_response"]
    assert automation_engine.desktop_routines
    assert automation_engine.desktop_routines[0]["trigger_type"] == "file_watch"
    assert automation_engine.desktop_routines[0]["file_extensions"] == ["pdf"]
    assert automation_engine.desktop_routines[0]["steps"][0]["type"] == "move_host_file"


@pytest.mark.asyncio
async def test_router_creates_scheduled_file_move_desktop_routine_from_natural_language(app_config) -> None:
    app_config.automation.desktop.enabled = True
    app_config.system_access.path_rules = [
        {
            "path": "R:/Download2",
            "read": "auto_allow",
            "write": "ask_once",
            "overwrite": "always_ask",
            "delete": "always_ask",
            "execute": "ask_once",
        },
        {
            "path": "C:/Users/Ritesh/OneDrive/Documents",
            "read": "auto_allow",
            "write": "ask_once",
            "overwrite": "always_ask",
            "delete": "always_ask",
            "execute": "ask_once",
        },
    ]
    automation_engine = DummyAutomationEngine()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(host_tools_enabled=True),
        automation_engine=automation_engine,
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    create_response = await router.route_user_message(
        connection_id="conn-routine-create-4",
        request_id="req-routine-create-4",
        session_key="telegram:123",
        message="everyday at 9pm if any files come in download2 then move it to the document folder",
        metadata={"trace_id": "trace-routine-create-4", "user_id": "default"},
        mode=QueueMode.STEER,
    )
    list_response = await router.route_user_message(
        connection_id="conn-routine-create-5",
        request_id="req-routine-create-5",
        session_key="telegram:123",
        message="/routine list",
        metadata={"trace_id": "trace-routine-create-5", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert create_response.ok is True
    assert "Created desktop routine 'Move Download2 to Documents'." in create_response.payload["command_response"]
    assert automation_engine.desktop_routines
    assert automation_engine.desktop_routines[0]["trigger_type"] == "schedule"
    assert automation_engine.desktop_routines[0]["schedule"] == "0 21 * * *"
    assert automation_engine.desktop_routines[0]["steps"][0]["type"] == "move_host_dir_contents"
    assert "Desktop routines:" in list_response.payload["command_response"]
    assert "Move Download2 to Documents" in list_response.payload["command_response"]


@pytest.mark.asyncio
async def test_router_creates_scheduled_file_move_desktop_routine_from_natural_language_with_extra_after(app_config) -> None:
    app_config.automation.desktop.enabled = True
    app_config.system_access.path_rules = [
        {
            "path": "R:/Download2",
            "read": "auto_allow",
            "write": "ask_once",
            "overwrite": "always_ask",
            "delete": "always_ask",
            "execute": "ask_once",
        },
        {
            "path": "C:/Users/Ritesh/OneDrive/Documents",
            "read": "auto_allow",
            "write": "ask_once",
            "overwrite": "always_ask",
            "delete": "always_ask",
            "execute": "ask_once",
        },
    ]
    automation_engine = DummyAutomationEngine()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(host_tools_enabled=True),
        automation_engine=automation_engine,
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-routine-create-6",
        request_id="req-routine-create-6",
        session_key="telegram:123",
        message="everyday after at 9pm if any files come in download2 then move it to the document folder",
        metadata={"trace_id": "trace-routine-create-6", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "Created desktop routine 'Move Download2 to Documents'." in response.payload["command_response"]
    assert automation_engine.desktop_routines
    assert automation_engine.desktop_routines[0]["schedule"] == "0 21 * * *"


@pytest.mark.asyncio
async def test_router_creates_scheduled_file_move_desktop_routine_from_short_natural_language(app_config) -> None:
    app_config.automation.desktop.enabled = True
    app_config.system_access.path_rules = [
        {
            "path": "R:/Download2",
            "read": "auto_allow",
            "write": "ask_once",
            "overwrite": "always_ask",
            "delete": "always_ask",
            "execute": "ask_once",
        },
        {
            "path": "C:/Users/Ritesh/OneDrive/Documents",
            "read": "auto_allow",
            "write": "ask_once",
            "overwrite": "always_ask",
            "delete": "always_ask",
            "execute": "ask_once",
        },
    ]
    automation_engine = DummyAutomationEngine()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(host_tools_enabled=True),
        automation_engine=automation_engine,
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-routine-create-short",
        request_id="req-routine-create-short",
        session_key="telegram:123",
        message="every day at 9 pm move files from download2 to documents",
        metadata={"trace_id": "trace-routine-create-short", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "Created desktop routine 'Move Download2 to Documents'." in response.payload["command_response"]
    assert automation_engine.desktop_routines
    assert automation_engine.desktop_routines[0]["schedule"] == "0 21 * * *"


@pytest.mark.asyncio
async def test_router_desktop_routine_shortcut_falls_back_to_original_message_when_canonical_rewrite_misses(app_config) -> None:
    app_config.automation.desktop.enabled = True
    app_config.system_access.path_rules = [
        {
            "path": "R:/Download2",
            "read": "auto_allow",
            "write": "ask_once",
            "overwrite": "always_ask",
            "delete": "always_ask",
            "execute": "ask_once",
        },
        {
            "path": "C:/Users/Ritesh/OneDrive/Documents",
            "read": "auto_allow",
            "write": "ask_once",
            "overwrite": "always_ask",
            "delete": "always_ask",
            "execute": "ask_once",
        },
    ]
    automation_engine = DummyAutomationEngine()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(host_tools_enabled=True),
        automation_engine=automation_engine,
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    async def fake_rewrite(message: str) -> str:
        return 'Every day at 9 PM, if any files are present in the "download2" folder, move them to the "document" folder.'

    async def fake_classify(_message: str) -> dict[str, object]:
        return {
            "intent": "file_op",
            "target": "file",
            "action": "move",
            "time_expr": "every day at 9 PM",
            "corrected": 'Every day at 9 PM, if any files are present in the "download2" folder, move them to the "document" folder.',
            "confidence": 0.95,
            "raw_slots": {"source_folder": "download2", "destination_folder": "document"},
        }

    router._nlp.rewrite_canonical = fake_rewrite
    router._nlp.classify = fake_classify

    response = await router.route_user_message(
        connection_id="conn-routine-create-7",
        request_id="req-routine-create-7",
        session_key="telegram:123",
        message="everyday at 9pm if any files come in download2 then move it to the document folder",
        metadata={"trace_id": "trace-routine-create-7", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "Created desktop routine 'Move Download2 to Documents'." in response.payload["command_response"]
    assert automation_engine.desktop_routines
    assert automation_engine.desktop_routines[0]["schedule"] == "0 21 * * *"


@pytest.mark.asyncio
async def test_router_desktop_routine_shortcut_falls_back_to_original_message_when_canonical_rewrite_returns_parse_error(
    app_config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_config.automation.desktop.enabled = True
    app_config.system_access.path_rules = [
        {
            "path": "R:/Download2",
            "read": "auto_allow",
            "write": "ask_once",
            "overwrite": "always_ask",
            "delete": "always_ask",
            "execute": "ask_once",
        },
        {
            "path": "C:/Users/Ritesh/OneDrive/Documents",
            "read": "auto_allow",
            "write": "ask_once",
            "overwrite": "always_ask",
            "delete": "always_ask",
            "execute": "ask_once",
        },
    ]
    automation_engine = DummyAutomationEngine()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(host_tools_enabled=True),
        automation_engine=automation_engine,
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    async def fake_rewrite(_message: str) -> str:
        return 'Every day at 9:00 PM, move the files from the "download2" directory to the "documents" directory.'

    original_parse = GatewayRouter._parse_desktop_routine_request

    async def fake_parse(self, session_key: str, message: str, lowered: str, metadata: dict[str, object]):
        if lowered == 'every day at 9:00 pm, move the files from the "download2" directory to the "documents" directory.':
            return {"response_text": "I couldn't understand that schedule."}
        return await original_parse(self, session_key, message, lowered, metadata)

    async def fake_classify(_message: str) -> dict[str, object]:
        return {
            "intent": "file_op",
            "target": "files",
            "action": "move",
            "time_expr": "every day at 9 PM",
            "corrected": 'Every day at 9:00 PM, move the files from the "download2" directory to the "documents" directory.',
            "confidence": 0.95,
            "raw_slots": {"source_folder": "download2", "destination_folder": "documents"},
        }

    router._nlp.rewrite_canonical = fake_rewrite
    router._nlp.classify = fake_classify
    monkeypatch.setattr(GatewayRouter, "_parse_desktop_routine_request", fake_parse)

    response = await router.route_user_message(
        connection_id="conn-routine-create-parse-error",
        request_id="req-routine-create-parse-error",
        session_key="telegram:123",
        message="every day at 9 pm move files from download2 to documents'",
        metadata={"trace_id": "trace-routine-create-parse-error", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "Created desktop routine 'Move Download2 to Documents'." in response.payload["command_response"]
    assert automation_engine.desktop_routines
    assert automation_engine.desktop_routines[0]["schedule"] == "0 21 * * *"


@pytest.mark.asyncio
async def test_router_routine_management_flow(app_config) -> None:
    app_config.automation.desktop.enabled = True
    automation_engine = DummyAutomationEngine()
    automation_engine.desktop_routines = [
        {
            "name": "routine:routine-rule-1",
            "display_name": "Study mode",
            "trigger": "desktop_routine",
            "trigger_type": "manual",
            "summary": "open chrome, open R:/6_semester",
            "steps": [{"type": "open_app", "target": "chrome"}],
            "step_count": 1,
            "risky_step_count": 0,
            "paused": False,
            "routine": True,
        }
    ]
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(host_tools_enabled=True, app_tools_enabled=True),
        automation_engine=automation_engine,
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    list_response = await router.route_user_message(
        connection_id="conn-routine-manage-1",
        request_id="req-routine-manage-1",
        session_key="telegram:123",
        message="show my routines",
        metadata={"trace_id": "trace-routine-manage-1", "user_id": "default"},
        mode=QueueMode.STEER,
    )
    run_response = await router.route_user_message(
        connection_id="conn-routine-manage-2",
        request_id="req-routine-manage-2",
        session_key="telegram:123",
        message="run study mode",
        metadata={"trace_id": "trace-routine-manage-2", "user_id": "default"},
        mode=QueueMode.STEER,
    )
    pause_response = await router.route_user_message(
        connection_id="conn-routine-manage-3",
        request_id="req-routine-manage-3",
        session_key="telegram:123",
        message="/routine pause Study mode",
        metadata={"trace_id": "trace-routine-manage-3", "user_id": "default"},
        mode=QueueMode.STEER,
    )
    resume_response = await router.route_user_message(
        connection_id="conn-routine-manage-4",
        request_id="req-routine-manage-4",
        session_key="telegram:123",
        message="/routine resume Study mode",
        metadata={"trace_id": "trace-routine-manage-4", "user_id": "default"},
        mode=QueueMode.STEER,
    )
    delete_response = await router.route_user_message(
        connection_id="conn-routine-manage-5",
        request_id="req-routine-manage-5",
        session_key="telegram:123",
        message="/routine delete Study mode",
        metadata={"trace_id": "trace-routine-manage-5", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert list_response.ok is True
    assert "Desktop routines:" in list_response.payload["command_response"]
    assert "Study mode" in list_response.payload["command_response"]
    assert run_response.ok is True
    assert "Ran routine routine-rule-1." in run_response.payload["command_response"]
    assert automation_engine.routine_runs == ["routine-rule-1"]
    assert pause_response.ok is True
    assert "Paused desktop routine 'Study mode'." in pause_response.payload["command_response"]
    assert resume_response.ok is True
    assert "Resumed desktop routine 'Study mode'." in resume_response.payload["command_response"]
    assert delete_response.ok is True
    assert "Deleted desktop routine 'Study mode'." in delete_response.payload["command_response"]
    assert automation_engine.desktop_routines == []


@pytest.mark.asyncio
async def test_router_host_shortcut_lists_contents_from_give_content_phrase(app_config) -> None:
    tool_registry = DummyToolRegistry(host_tools_enabled=True)
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-host-4ef",
        request_id="req-host-4ef",
        session_key="telegram:123",
        message="give the content of the 6_semester folder in R drive",
        metadata={"trace_id": "trace-host-4ef", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "R:/6_semester" in response.payload["command_response"]
    assert "timepass.txt" in response.payload["command_response"]
    assert [call[0] for call in tool_registry.calls[:2]] == ["search_host_files", "list_host_dir"]


@pytest.mark.asyncio
async def test_router_accepts_slash_commands_with_telegram_bot_suffix(app_config) -> None:
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(),
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-command-1",
        request_id="req-command-1",
        session_key="telegram:123",
        message="/skills@sonar_new_bot",
        metadata={"trace_id": "trace-command-1", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is False
    assert "No skills are currently enabled." in response.payload["command_response"]


@pytest.mark.asyncio
async def test_router_host_approve_without_id_uses_single_pending_approval(app_config) -> None:
    system_access_manager = DummySystemAccessManager(
        approvals=[
            {
                "approval_id": "approval-123",
                "status": "pending",
                "expired": False,
                "action_kind": "write_host_file",
                "target_summary": "C:/Users/Ritesh/Desktop/todo.txt",
            }
        ]
    )
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(),
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        system_access_manager=system_access_manager,
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-command-2",
        request_id="req-command-2",
        session_key="telegram:123",
        message="/host-approve",
        metadata={"trace_id": "trace-command-2", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "Approved host approval 'approval-123'." in response.payload["command_response"]
    assert system_access_manager.decisions == [("approval-123", "approved")]


@pytest.mark.asyncio
async def test_router_browser_commands_are_available_for_channel_use(app_config) -> None:
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(),
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    profiles = await router.route_user_message(
        connection_id="conn-browser-1",
        request_id="req-browser-1",
        session_key="telegram:123",
        message="/browser profiles",
        metadata={"trace_id": "trace-browser-1", "user_id": "default"},
        mode=QueueMode.STEER,
    )
    tabs = await router.route_user_message(
        connection_id="conn-browser-2",
        request_id="req-browser-2",
        session_key="telegram:123",
        message="/browser tabs",
        metadata={"trace_id": "trace-browser-2", "user_id": "default"},
        mode=QueueMode.STEER,
    )
    logs = await router.route_user_message(
        connection_id="conn-browser-3",
        request_id="req-browser-3",
        session_key="telegram:123",
        message="/browser logs 2",
        metadata={"trace_id": "trace-browser-3", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert profiles.ok is True
    assert "Saved browser profiles:" in profiles.payload["command_response"]
    assert "github.com/work" in profiles.payload["command_response"]
    assert tabs.ok is True
    assert "Open browser tabs:" in tabs.payload["command_response"]
    assert "tab-1 [active]" in tabs.payload["command_response"]
    assert logs.ok is True
    assert "Recent browser logs:" in logs.payload["command_response"]


@pytest.mark.asyncio
async def test_router_cron_add_list_pause_resume_delete_flow(app_config) -> None:
    automation_engine = DummyAutomationEngine()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(),
        automation_engine=automation_engine,
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    add_response = await router.route_user_message(
        connection_id="conn-cron-1",
        request_id="req-cron-1",
        session_key="telegram:123",
        message='/cron add "0 8 * * *" "Good morning briefing"',
        metadata={"trace_id": "trace-cron-1", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert add_response.ok is True
    assert "Created cron job 'cron-user-1'" in add_response.payload["command_response"]

    list_response = await router.route_user_message(
        connection_id="conn-cron-2",
        request_id="req-cron-2",
        session_key="telegram:123",
        message="/cron list",
        metadata={"trace_id": "trace-cron-2", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert list_response.ok is True
    assert "cron-user-1: active | 0 8 * * * | Good morning briefing" in list_response.payload["command_response"]

    pause_response = await router.route_user_message(
        connection_id="conn-cron-3",
        request_id="req-cron-3",
        session_key="telegram:123",
        message="/cron pause cron-user-1",
        metadata={"trace_id": "trace-cron-3", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert pause_response.ok is True
    assert "Paused cron job 'cron-user-1'." in pause_response.payload["command_response"]
    assert automation_engine.dynamic_jobs[0]["paused"] is True

    resume_response = await router.route_user_message(
        connection_id="conn-cron-4",
        request_id="req-cron-4",
        session_key="telegram:123",
        message="/cron resume cron-user-1",
        metadata={"trace_id": "trace-cron-4", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert resume_response.ok is True
    assert "Resumed cron job 'cron-user-1'." in resume_response.payload["command_response"]
    assert automation_engine.dynamic_jobs[0]["paused"] is False

    delete_response = await router.route_user_message(
        connection_id="conn-cron-5",
        request_id="req-cron-5",
        session_key="telegram:123",
        message="/cron delete cron-user-1",
        metadata={"trace_id": "trace-cron-5", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert delete_response.ok is True
    assert "Deleted cron job 'cron-user-1'." in delete_response.payload["command_response"]
    assert automation_engine.dynamic_jobs == []


@pytest.mark.asyncio
async def test_router_cron_add_accepts_pipe_syntax(app_config) -> None:
    automation_engine = DummyAutomationEngine()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(),
        automation_engine=automation_engine,
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-cron-6",
        request_id="req-cron-6",
        session_key="telegram:123",
        message="/cron add */10 * * * * | Cron test message",
        metadata={"trace_id": "trace-cron-6", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert automation_engine.dynamic_jobs[0]["schedule"] == "*/10 * * * *"
    assert automation_engine.dynamic_jobs[0]["message"] == "Cron test message"


@pytest.mark.asyncio
async def test_router_creates_dynamic_cron_from_daily_reminder_phrase(app_config) -> None:
    automation_engine = DummyAutomationEngine()
    session_manager = DummySessionManager()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=session_manager,
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(),
        automation_engine=automation_engine,
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-cron-nl-1",
        request_id="req-cron-nl-1",
        session_key="telegram:123",
        message="remind me every day at 8 am to go to college",
        metadata={"trace_id": "trace-cron-nl-1", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is False
    assert "Created cron job 'cron-user-1' on 0 8 * * *." in response.payload["command_response"]
    assert automation_engine.dynamic_jobs[0]["schedule"] == "0 8 * * *"
    assert automation_engine.dynamic_jobs[0]["message"] == "Reminder: go to college"
    assert [message["role"] for message in session_manager.messages] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_router_creates_dynamic_cron_from_named_day_reminder_phrase(app_config) -> None:
    automation_engine = DummyAutomationEngine()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(),
        automation_engine=automation_engine,
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-cron-nl-2",
        request_id="req-cron-nl-2",
        session_key="telegram:123",
        message="create a cron job to remind me every monday at 9:30 pm to submit attendance",
        metadata={"trace_id": "trace-cron-nl-2", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is False
    assert automation_engine.dynamic_jobs[0]["schedule"] == "30 21 * * 1"
    assert automation_engine.dynamic_jobs[0]["message"] == "Reminder: submit attendance"


@pytest.mark.asyncio
async def test_router_creates_dynamic_cron_from_daily_suffix_phrase(app_config) -> None:
    automation_engine = DummyAutomationEngine()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(),
        automation_engine=automation_engine,
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-cron-nl-3",
        request_id="req-cron-nl-3",
        session_key="telegram:123",
        message="remind me at 6 pm daily to study",
        metadata={"trace_id": "trace-cron-nl-3", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is False
    assert automation_engine.dynamic_jobs[0]["schedule"] == "0 18 * * *"
    assert automation_engine.dynamic_jobs[0]["message"] == "Reminder: study"


@pytest.mark.asyncio
async def test_router_creates_dynamic_cron_from_weekdays_phrase(app_config) -> None:
    automation_engine = DummyAutomationEngine()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(),
        automation_engine=automation_engine,
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-cron-nl-4",
        request_id="req-cron-nl-4",
        session_key="telegram:123",
        message="set a reminder for weekdays at 7 am to exercise",
        metadata={"trace_id": "trace-cron-nl-4", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is False
    assert automation_engine.dynamic_jobs[0]["schedule"] == "0 7 * * 1-5"
    assert automation_engine.dynamic_jobs[0]["message"] == "Reminder: exercise"


@pytest.mark.asyncio
async def test_router_creates_dynamic_cron_from_time_of_day_phrase(app_config) -> None:
    automation_engine = DummyAutomationEngine()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(),
        automation_engine=automation_engine,
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-cron-nl-5",
        request_id="req-cron-nl-5",
        session_key="telegram:123",
        message="every evening remind me to study",
        metadata={"trace_id": "trace-cron-nl-5", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is False
    assert automation_engine.dynamic_jobs[0]["schedule"] == "0 18 * * *"
    assert automation_engine.dynamic_jobs[0]["message"] == "Reminder: study"


@pytest.mark.asyncio
async def test_router_creates_interval_cron_from_every_five_minutes_phrase(app_config) -> None:
    automation_engine = DummyAutomationEngine()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(),
        automation_engine=automation_engine,
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-cron-interval-1",
        request_id="req-cron-interval-1",
        session_key="telegram:123",
        message="remind me after every 5 minute that i have to go to VNPS",
        metadata={"trace_id": "trace-cron-interval-1", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is False
    assert "Created cron job 'cron-user-1' on */5 * * * *." in response.payload["command_response"]
    assert automation_engine.dynamic_jobs[0]["schedule"] == "*/5 * * * *"
    assert automation_engine.dynamic_jobs[0]["message"] == "Reminder: i have to go to VNPS"


@pytest.mark.asyncio
async def test_router_creates_one_time_reminder_for_tomorrow_phrase(app_config) -> None:
    automation_engine = DummyAutomationEngine()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(),
        automation_engine=automation_engine,
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-reminder-1",
        request_id="req-reminder-1",
        session_key="telegram:123",
        message="remind me tomorrow at 8 am to submit the form",
        metadata={"trace_id": "trace-reminder-1", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is False
    assert "Created one-time reminder 'once-user-1'" in response.payload["command_response"]
    assert automation_engine.one_time_reminders[0]["message"] == "Reminder: submit the form"


@pytest.mark.asyncio
async def test_router_creates_one_time_reminder_for_at_time_tomorrow_phrase(app_config) -> None:
    automation_engine = DummyAutomationEngine()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(),
        automation_engine=automation_engine,
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-reminder-2",
        request_id="req-reminder-2",
        session_key="telegram:123",
        message="remind me at 6 pm tomorrow to call home",
        metadata={"trace_id": "trace-reminder-2", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is False
    assert automation_engine.one_time_reminders[0]["message"] == "Reminder: call home"


@pytest.mark.asyncio
async def test_router_cron_list_includes_one_time_reminders(app_config) -> None:
    automation_engine = DummyAutomationEngine()
    automation_engine.dynamic_jobs.append(
        {
            "cron_id": "cron-user-1",
            "user_id": "default",
            "schedule": "0 8 * * *",
            "message": "Reminder: go to college",
            "paused": False,
        }
    )
    automation_engine.one_time_reminders.append(
        {
            "reminder_id": "once-user-1",
            "user_id": "default",
            "run_at": "2026-04-03T08:00:00+00:00",
            "message": "Reminder: submit the form",
            "paused": False,
            "fired": False,
        }
    )
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(),
        automation_engine=automation_engine,
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-cron-list-1",
        request_id="req-cron-list-1",
        session_key="telegram:123",
        message="/cron list",
        metadata={"trace_id": "trace-cron-list-1", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "cron-user-1: active | 0 8 * * * | Reminder: go to college" in response.payload["command_response"]
    assert "One-time reminders:" in response.payload["command_response"]
    assert "once-user-1: active | 2026-04-03T08:00:00+00:00 | Reminder: submit the form" in response.payload["command_response"]


@pytest.mark.asyncio
async def test_router_auto_activates_exact_skill_match(app_config) -> None:
    agent_loop = DummyAgentLoop()
    skill = FakeSkill(
        "gmail-triage",
        description="Inbox triage",
        aliases=["check my inbox"],
        activation_examples=["triage my emails"],
        keywords=["email", "inbox"],
    )
    router = GatewayRouter(
        config=app_config,
        agent_loop=agent_loop,
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(enabled=[skill], matches=[FakeSkillMatch(skill, 100, exact=True)]),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(),
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-1",
        request_id="req-5",
        session_key="webchat_main",
        message="check my inbox",
        metadata={"trace_id": "trace-5"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is True
    assert response.payload["activated_skill"] == "gmail-triage"
    enqueued = agent_loop.enqueued[-1]
    assert enqueued.metadata["activated_skill"] == "gmail-triage"
    assert enqueued.metadata["skill_activation_source"] == "exact"
    assert "## Active Skill" in (enqueued.system_suffix or "")


@pytest.mark.asyncio
async def test_router_falls_back_when_skill_match_is_ambiguous(app_config) -> None:
    agent_loop = DummyAgentLoop()
    first = FakeSkill("daily-briefing", aliases=["daily briefing"], keywords=["briefing", "today"])
    second = FakeSkill("gmail-briefing", aliases=["inbox briefing"], keywords=["briefing", "email"])
    router = GatewayRouter(
        config=app_config,
        agent_loop=agent_loop,
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(
            enabled=[first, second],
            matches=[FakeSkillMatch(first, 5), FakeSkillMatch(second, 4)],
        ),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(has_llm_task=False),
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-1",
        request_id="req-6",
        session_key="webchat_main",
        message="give me a briefing",
        metadata={"trace_id": "trace-6"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is True
    enqueued = agent_loop.enqueued[-1]
    assert "activated_skill" not in enqueued.metadata


@pytest.mark.asyncio
async def test_router_uses_classifier_for_plausible_skill_candidates(app_config) -> None:
    agent_loop = DummyAgentLoop()
    target = FakeSkill("daily-briefing", aliases=["morning briefing"], keywords=["briefing", "today"])
    other = FakeSkill("gmail-briefing", aliases=["inbox briefing"], keywords=["briefing", "email"])
    router = GatewayRouter(
        config=app_config,
        agent_loop=agent_loop,
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(
            enabled=[target, other],
            matches=[FakeSkillMatch(target, 5), FakeSkillMatch(other, 4)],
        ),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(
            llm_task_response={"content": json.dumps({"skill": "daily-briefing", "confidence": 0.93})}
        ),
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-1",
        request_id="req-7",
        session_key="webchat_main",
        message="give me a briefing for today",
        metadata={"trace_id": "trace-7"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is True
    assert response.payload["activated_skill"] == "daily-briefing"
    enqueued = agent_loop.enqueued[-1]
    assert enqueued.metadata["skill_activation_source"] == "classifier"


@pytest.mark.asyncio
async def test_router_vscode_slash_command_opens_project(app_config) -> None:
    app_config.app_skills.enabled = True
    tool_registry = DummyToolRegistry(app_skill_tools_enabled=True)
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-skill-1",
        request_id="req-skill-1",
        session_key="telegram:123",
        message="/vscode open 6_semester",
        metadata={"trace_id": "trace-skill-1", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "opened r:/6_semester/mini_project in vs code" in response.payload["command_response"].lower()
    assert tool_registry.calls[0][0] == "vscode_open_target"


@pytest.mark.asyncio
async def test_router_doc_slash_command_creates_document(app_config) -> None:
    app_config.app_skills.enabled = True
    tool_registry = DummyToolRegistry(app_skill_tools_enabled=True)
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-skill-2",
        request_id="req-skill-2",
        session_key="telegram:123",
        message="/doc create R:/6_semester/notes.docx :: hello world",
        metadata={"trace_id": "trace-skill-2", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "created or updated" in response.payload["command_response"].lower()
    assert tool_registry.calls[0][0] == "document_create"


@pytest.mark.asyncio
async def test_router_excel_slash_command_previews_workbook(app_config) -> None:
    app_config.app_skills.enabled = True
    tool_registry = DummyToolRegistry(app_skill_tools_enabled=True)
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-skill-3",
        request_id="req-skill-3",
        session_key="telegram:123",
        message="/excel preview R:/marks.xlsx",
        metadata={"trace_id": "trace-skill-3", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "workbook preview" in response.payload["command_response"].lower()
    assert tool_registry.calls[0][0] == "excel_preview"


@pytest.mark.asyncio
async def test_router_natural_language_opens_task_manager_with_summary(app_config) -> None:
    app_config.app_skills.enabled = True
    tool_registry = DummyToolRegistry(app_skill_tools_enabled=True)
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-skill-4",
        request_id="req-skill-4",
        session_key="telegram:123",
        message="open task manager",
        metadata={"trace_id": "trace-skill-4", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "opened task manager" in response.payload["command_response"].lower()
    assert "cpu:" in response.payload["command_response"].lower()
    assert tool_registry.calls[0][0] == "task_manager_open"


@pytest.mark.asyncio
async def test_router_natural_language_opens_bluetooth_settings(app_config) -> None:
    app_config.app_skills.enabled = True
    tool_registry = DummyToolRegistry(app_skill_tools_enabled=True)
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-skill-5",
        request_id="req-skill-5",
        session_key="telegram:123",
        message="open bluetooth settings",
        metadata={"trace_id": "trace-skill-5", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "opened bluetooth settings" in response.payload["command_response"].lower()
    assert tool_registry.calls[0][0] == "system_open_settings"


@pytest.mark.asyncio
async def test_router_natural_language_runs_study_mode_preset(app_config) -> None:
    app_config.app_skills.enabled = True
    tool_registry = DummyToolRegistry(app_skill_tools_enabled=True)
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-skill-6",
        request_id="req-skill-6",
        session_key="telegram:123",
        message="study mode",
        metadata={"trace_id": "trace-skill-6", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "ran study-mode" in response.payload["command_response"].lower()
    assert tool_registry.calls[0][0] == "preset_run"


@pytest.mark.asyncio
async def test_router_coworker_plan_command_returns_preview(app_config) -> None:
    app_config.desktop_coworker.enabled = True
    coworker_service = DummyCoworkerService()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(),
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
        coworker_service=coworker_service,
    )

    response = await router.route_user_message(
        connection_id="conn-coworker-1",
        request_id="req-coworker-1",
        session_key="webchat_main",
        message="/coworker plan open task manager and summarize system usage",
        metadata={"trace_id": "trace-coworker-1"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "Coworker task coworker-1" in response.payload["command_response"]
    assert "Planned steps:" in response.payload["command_response"]
    assert coworker_service.planned == ["open task manager and summarize system usage"]


@pytest.mark.asyncio
async def test_router_natural_language_coworker_shortcut_runs_task(app_config) -> None:
    app_config.desktop_coworker.enabled = True
    coworker_service = DummyCoworkerService()
    session_manager = DummySessionManager()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=session_manager,
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(),
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
        coworker_service=coworker_service,
    )

    response = await router.route_user_message(
        connection_id="conn-coworker-2",
        request_id="req-coworker-2",
        session_key="webchat_main",
        message="help me open task manager and summarize system usage",
        metadata={"trace_id": "trace-coworker-2"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "Status: completed" in response.payload["command_response"]
    assert "Latest window: Task Manager" in response.payload["command_response"]
    assert coworker_service.ran == ["help me open task manager and summarize system usage"]
    assert [message["role"] for message in session_manager.messages] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_router_mail_shortcut_wins_before_coworker(app_config) -> None:
    app_config.desktop_coworker.enabled = True
    coworker_service = DummyCoworkerService()
    tool_registry = DummyToolRegistry()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
        coworker_service=coworker_service,
    )

    response = await router.route_user_message(
        connection_id="conn-coworker-mail",
        request_id="req-coworker-mail",
        session_key="webchat_main",
        message="check my mails",
        metadata={"trace_id": "trace-coworker-mail"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is False
    assert "Here are your recent Gmail messages:" in response.payload["command_response"]
    assert tool_registry.calls[0][0] == "gmail_search"
    assert coworker_service.ran == []


@pytest.mark.asyncio
async def test_router_recent_mails_shortcut_wins_before_coworker(app_config) -> None:
    app_config.desktop_coworker.enabled = True
    coworker_service = DummyCoworkerService()
    tool_registry = DummyToolRegistry()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
        coworker_service=coworker_service,
    )

    response = await router.route_user_message(
        connection_id="conn-coworker-mail-count",
        request_id="req-coworker-mail-count",
        session_key="webchat_main",
        message="what are the 5 recent mails that i have received",
        metadata={"trace_id": "trace-coworker-mail-count"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is False
    assert "Here are your recent Gmail messages:" in response.payload["command_response"]
    assert tool_registry.calls[0][0] == "gmail_search"
    assert tool_registry.calls[0][1]["limit"] == 5
    assert coworker_service.ran == []


@pytest.mark.asyncio
async def test_router_natural_language_visual_coworker_shortcut_runs_task(app_config) -> None:
    app_config.desktop_coworker.enabled = True
    coworker_service = DummyCoworkerService()
    session_manager = DummySessionManager()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=session_manager,
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(),
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
        coworker_service=coworker_service,
    )

    response = await router.route_user_message(
        connection_id="conn-coworker-3",
        request_id="req-coworker-3",
        session_key="webchat_main",
        message="open the file you see on screen now",
        metadata={"trace_id": "trace-coworker-3"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "Status: completed" in response.payload["command_response"]
    assert coworker_service.ran == ["open the file you see on screen now"]


@pytest.mark.asyncio
async def test_router_visual_click_phrase_routes_to_coworker(app_config) -> None:
    app_config.desktop_coworker.enabled = True
    coworker_service = DummyCoworkerService()
    session_manager = DummySessionManager()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=session_manager,
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(screen_tools_enabled=True, input_tools_enabled=True),
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
        coworker_service=coworker_service,
    )

    response = await router.route_user_message(
        connection_id="conn-coworker-visual-click",
        request_id="req-coworker-visual-click",
        session_key="webchat_main",
        message="click on the desktop",
        metadata={"trace_id": "trace-coworker-visual-click"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "Status: completed" in response.payload["command_response"]
    assert coworker_service.ran == ["click on the desktop"]


@pytest.mark.asyncio
async def test_router_telegram_bluetooth_toggle_phrase_routes_to_coworker(app_config) -> None:
    app_config.desktop_coworker.enabled = True
    coworker_service = DummyCoworkerService()
    session_manager = DummySessionManager()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=session_manager,
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(app_skill_tools_enabled=True, screen_tools_enabled=True, input_tools_enabled=True),
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
        coworker_service=coworker_service,
    )

    response = await router.route_user_message(
        connection_id="conn-coworker-telegram-bt",
        request_id="req-coworker-telegram-bt",
        session_key="telegram:123",
        message="open bluetooth settings and turn off the bluetooth",
        metadata={"trace_id": "trace-coworker-telegram-bt", "user_id": "default", "channel": "telegram"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "Status: completed" in response.payload["command_response"]
    assert coworker_service.ran == ["open bluetooth settings and turn off the bluetooth"]


@pytest.mark.asyncio
async def test_router_system_bluetooth_off_command_dispatches_direct_toggle(app_config) -> None:
    app_config.app_skills.enabled = True
    app_config.app_skills.system_enabled = True
    tool_registry = DummyToolRegistry(app_skill_tools_enabled=True)
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-system-bt-off",
        request_id="req-system-bt-off",
        session_key="webchat_main",
        message="/system bluetooth off",
        metadata={"user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "Bluetooth is now Off." in response.payload["command_response"]
    name, payload = tool_registry.calls[-1]
    assert name == "system_bluetooth_set"
    assert payload["mode"] == "off"
