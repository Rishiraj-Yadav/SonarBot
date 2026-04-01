from __future__ import annotations

import pytest

from assistant.desktop_coworker import DesktopCoworkerService


class CoworkerToolRegistry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.document_content = "Heading\nBody"
        self.summary_result = "Summary bullets"

    def has(self, tool_name: str) -> bool:
        return tool_name in {
            "task_manager_open",
            "task_manager_summary",
            "system_open_settings",
            "system_bluetooth_status",
            "vscode_open_target",
            "document_read",
            "document_replace_text",
            "preset_run",
            "desktop_keyboard_hotkey",
            "desktop_clipboard_read",
            "desktop_active_window",
            "desktop_window_screenshot",
            "desktop_read_screen",
            "apps_list_windows",
            "llm_task",
        }

    async def dispatch(self, tool_name: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append((tool_name, dict(payload)))
        if tool_name == "desktop_active_window":
            return {"active_window": {"title": "Visual Studio Code", "process_name": "Code"}}
        if tool_name == "apps_list_windows":
            return {
                "windows": [
                    {"title": "Visual Studio Code", "process_name": "Code", "is_foreground": True},
                ]
            }
        if tool_name == "desktop_window_screenshot":
            return {
                "path": "workspace/desktop/window-1.png",
                "active_window": {"title": "Visual Studio Code", "process_name": "Code"},
            }
        if tool_name == "desktop_read_screen":
            return {
                "path": "workspace/desktop/window-2.png",
                "active_window": {"title": "Visual Studio Code", "process_name": "Code"},
                "content": "Visible text",
            }
        if tool_name == "task_manager_open":
            return {
                "status": "completed",
                "summary": {
                    "cpu_percent": 22.0,
                    "memory": {"used_percent": 48.0},
                    "disk": {"used_percent": 63.0},
                },
            }
        if tool_name == "task_manager_summary":
            return {
                "cpu_percent": 22.0,
                "memory": {"used_percent": 48.0},
                "disk": {"used_percent": 63.0},
            }
        if tool_name == "vscode_open_target":
            return {"status": "completed", "path": "R:/6_semester/mini_project"}
        if tool_name == "document_read":
            return {
                "status": "completed",
                "path": str(payload.get("path", "")),
                "content": self.document_content,
                "bytes_read": len(self.document_content.encode("utf-8")),
                "line_count": len(self.document_content.splitlines()),
            }
        if tool_name == "document_replace_text":
            find_text = str(payload.get("find_text", ""))
            replace_text = str(payload.get("replace_text", ""))
            self.document_content = self.document_content.replace(find_text, replace_text)
            return {
                "status": "completed",
                "path": str(payload.get("path", "")),
                "replacements": 1,
            }
        if tool_name == "desktop_keyboard_hotkey":
            return {"status": "completed", "hotkey": "ctrl+c"}
        if tool_name == "desktop_clipboard_read":
            return {"status": "completed", "content": "selected clipboard text", "char_count": 22}
        if tool_name == "llm_task":
            return {"content": self.summary_result}
        raise AssertionError(f"Unexpected tool call: {tool_name}")


@pytest.mark.asyncio
async def test_coworker_service_runs_task_manager_summary_flow(app_config) -> None:
    app_config.desktop_coworker.enabled = True
    registry = CoworkerToolRegistry()
    service = DesktopCoworkerService(app_config, registry)
    await service.initialize()

    task = await service.run_task_request(
        user_id=app_config.users.default_user_id,
        session_key="webchat_main",
        request_text="open task manager and summarize system usage",
    )

    assert task["status"] == "completed"
    assert task["current_step_index"] == 2
    assert len(task["transcript"]) == 2
    assert [name for name, _payload in registry.calls if name.startswith("task_manager")] == [
        "task_manager_open",
        "task_manager_summary",
    ]


@pytest.mark.asyncio
async def test_coworker_service_verifies_document_replacement(app_config) -> None:
    app_config.desktop_coworker.enabled = True
    registry = CoworkerToolRegistry()
    service = DesktopCoworkerService(app_config, registry)
    await service.initialize()

    task = await service.run_task_request(
        user_id=app_config.users.default_user_id,
        session_key="webchat_main",
        request_text="open R:/6_semester/notes.docx, replace Heading with New Heading, save it and verify the change",
    )

    assert task["status"] == "completed"
    assert task["current_step_index"] == 3
    assert "New Heading" in task["latest_state"]["last_document_content"]
    assert len(task["transcript"]) == 3


@pytest.mark.asyncio
async def test_coworker_service_copies_and_summarizes_selected_text(app_config) -> None:
    app_config.desktop_coworker.enabled = True
    registry = CoworkerToolRegistry()
    service = DesktopCoworkerService(app_config, registry)
    await service.initialize()

    task = await service.run_task_request(
        user_id=app_config.users.default_user_id,
        session_key="webchat_main",
        request_text="copy selected text and summarize it",
    )

    assert task["status"] == "completed"
    assert task["current_step_index"] == 3
    assert task["latest_state"]["clipboard_text"] == "selected clipboard text"
    assert task["latest_state"]["last_summary_text"] == "Summary bullets"
