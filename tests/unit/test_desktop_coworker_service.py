from __future__ import annotations

import re

import pytest

from assistant.desktop_coworker import DesktopCoworkerService


class CoworkerToolRegistry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.document_content = "Heading\nBody"
        self.summary_result = "Summary bullets"
        self.visual_screen = "excel_recent"
        self.mouse_click_opens_target = True
        self.search_returns_excel_file_match = False

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
            "desktop_keyboard_type",
            "desktop_clipboard_read",
            "desktop_active_window",
            "desktop_window_screenshot",
            "desktop_read_screen",
            "desktop_mouse_click",
            "desktop_mouse_scroll",
            "apps_open",
            "search_host_files",
            "apps_list_windows",
            "llm_task",
        }

    async def dispatch(self, tool_name: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append((tool_name, dict(payload)))
        if tool_name == "desktop_active_window":
            if self.visual_screen == "excel_recent":
                return {"active_window": {"title": "Excel", "process_name": "EXCEL"}}
            if self.visual_screen == "excel_opened":
                return {"active_window": {"title": "hindi_english_parallel - Excel", "process_name": "EXCEL"}}
            if self.visual_screen == "settings_bluetooth_on":
                return {"active_window": {"title": "Bluetooth & devices > Devices - Settings", "process_name": "SystemSettings"}}
            if self.visual_screen == "settings_bluetooth_off":
                return {"active_window": {"title": "Bluetooth & devices > Devices - Settings", "process_name": "SystemSettings"}}
            if self.visual_screen == "explorer_home":
                return {"active_window": {"title": "Home - File Explorer", "process_name": "explorer"}}
            if self.visual_screen == "explorer_desktop":
                return {"active_window": {"title": "Desktop - File Explorer", "process_name": "explorer"}}
            return {"active_window": {"title": "Visual Studio Code", "process_name": "Code"}}
        if tool_name == "apps_list_windows":
            return {
                "windows": [
                    {"title": "Visual Studio Code", "process_name": "Code", "is_foreground": True},
                ]
            }
        if tool_name == "desktop_window_screenshot":
            path = "workspace/desktop/window-1.png"
            if self.visual_screen == "excel_recent":
                path = "workspace/desktop/window-2.png"
            elif self.visual_screen == "excel_opened":
                path = "workspace/desktop/window-3.png"
            elif self.visual_screen == "settings_bluetooth_on":
                path = "workspace/desktop/window-settings-on.png"
            elif self.visual_screen == "settings_bluetooth_off":
                path = "workspace/desktop/window-settings-off.png"
            elif self.visual_screen == "explorer_home":
                path = "workspace/desktop/window-explorer-home.png"
            elif self.visual_screen == "explorer_desktop":
                path = "workspace/desktop/window-explorer-desktop.png"
            return {
                "path": path,
                "width": 1200,
                "height": 800,
                "active_window": self._active_window_payload(),
            }
        if tool_name == "desktop_read_screen":
            if self.visual_screen == "excel_recent":
                return {
                    "path": "workspace/desktop/window-2.png",
                    "width": 1200,
                    "height": 800,
                    "active_window": {"title": "Excel", "process_name": "EXCEL"},
                    "content": "Recent\nhindi_english_parallel\nhousing_data.xlsx",
                }
            if self.visual_screen == "excel_opened":
                return {
                    "path": "workspace/desktop/window-3.png",
                    "width": 1200,
                    "height": 800,
                    "active_window": {"title": "hindi_english_parallel - Excel", "process_name": "EXCEL"},
                    "content": "hindi_english_parallel Workbook",
                }
            if self.visual_screen == "settings_bluetooth_on":
                return {
                    "path": "workspace/desktop/window-settings-on.png",
                    "width": 1440,
                    "height": 900,
                    "active_window": {"title": "Bluetooth & devices > Devices - Settings", "process_name": "SystemSettings"},
                    "content": "Bluetooth & devices\nDevices\nBluetooth\nOn\nAdd device\nUSB GAMING MOUSE",
                }
            if self.visual_screen == "settings_bluetooth_off":
                return {
                    "path": "workspace/desktop/window-settings-off.png",
                    "width": 1440,
                    "height": 900,
                    "active_window": {"title": "Bluetooth & devices > Devices - Settings", "process_name": "SystemSettings"},
                    "content": "Bluetooth & devices\nDevices\nBluetooth\nOff\nAdd device\nUSB GAMING MOUSE",
                }
            if self.visual_screen == "explorer_home":
                return {
                    "path": "workspace/desktop/window-explorer-home.png",
                    "width": 1400,
                    "height": 900,
                    "active_window": {"title": "Home - File Explorer", "process_name": "explorer"},
                    "content": "Home Quick access Desktop Download2 Documents 6_semester Recent 18",
                }
            if self.visual_screen == "explorer_desktop":
                return {
                    "path": "workspace/desktop/window-explorer-desktop.png",
                    "width": 1400,
                    "height": 900,
                    "active_window": {"title": "Desktop - File Explorer", "process_name": "explorer"},
                    "content": "Desktop File Explorer Name Date modified SonarBotTest notes.txt",
                }
            return {
                "path": "workspace/desktop/window-2.png",
                "width": 1200,
                "height": 800,
                "active_window": {"title": "Visual Studio Code", "process_name": "Code"},
                "content": "Visible text",
            }
        if tool_name == "desktop_mouse_click":
            if self.mouse_click_opens_target and str(payload.get("visual_target_label", "")) == "hindienglishparallel":
                self.visual_screen = "excel_opened"
                return {
                    "status": "completed",
                    "x": int(payload.get("x", 0)),
                    "y": int(payload.get("y", 0)),
                    "screen_text_after": "hindi_english_parallel Workbook",
                }
            if str(payload.get("visual_target_label", "")).lower() == "on" and self.visual_screen == "settings_bluetooth_on":
                self.visual_screen = "settings_bluetooth_off"
                return {
                    "status": "completed",
                    "x": int(payload.get("x", 0)),
                    "y": int(payload.get("y", 0)),
                    "screen_text_after": "Bluetooth & devices Devices Bluetooth Off Add device USB GAMING MOUSE",
                }
            if str(payload.get("visual_target_label", "")).lower() == "off" and self.visual_screen == "settings_bluetooth_off":
                self.visual_screen = "settings_bluetooth_on"
                return {
                    "status": "completed",
                    "x": int(payload.get("x", 0)),
                    "y": int(payload.get("y", 0)),
                    "screen_text_after": "Bluetooth & devices Devices Bluetooth On Add device USB GAMING MOUSE",
                }
            return {"status": "completed", "x": int(payload.get("x", 0)), "y": int(payload.get("y", 0))}
        if tool_name == "apps_open":
            target = str(payload.get("target", ""))
            args = [str(item) for item in payload.get("args", [])] if isinstance(payload.get("args", []), list) else []
            if target == "explorer" and args and args[0].replace("\\", "/").lower().endswith("/desktop"):
                self.visual_screen = "explorer_desktop"
            if target == "excel" and args and "hindi_english_parallel" in args[0].lower():
                self.visual_screen = "excel_opened"
            return {"status": "completed", "alias": target, "path": args[0] if args else target, "launched": True}
        if tool_name == "system_open_settings":
            if str(payload.get("page", "")).strip().lower() == "bluetooth":
                self.visual_screen = "settings_bluetooth_on"
            return {"page": str(payload.get("page", "")), "status": "completed"}
        if tool_name == "search_host_files":
            name_query = str(payload.get("name_query", "")).strip().lower()
            if re.sub(r"[^a-z0-9]+", "", name_query) == "desktop":
                return {"matches": [{"name": "Desktop", "path": "C:/Users/Ritesh/Desktop", "is_dir": True}]}
            if self.search_returns_excel_file_match and re.sub(r"[^a-z0-9]+", "", name_query) == "hindienglishparallel":
                return {"matches": [{"name": "hindi_english_parallel.xlsx", "path": "R:/Dec/python/machine/hindi_english_parallel.xlsx", "is_dir": False}]}
            return {"matches": []}
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
        if tool_name == "desktop_keyboard_type":
            return {"status": "completed", "characters_typed": len(str(payload.get("text", "")))}
        if tool_name == "desktop_mouse_scroll":
            return {
                "status": "completed",
                "direction": str(payload.get("direction", "down")),
                "amount": int(payload.get("amount", 1)),
            }
        if tool_name == "desktop_clipboard_read":
            return {"status": "completed", "content": "selected clipboard text", "char_count": 22}
        if tool_name == "llm_task":
            return {"content": self.summary_result}
        raise AssertionError(f"Unexpected tool call: {tool_name}")

    def _active_window_payload(self) -> dict[str, object]:
        if self.visual_screen == "excel_recent":
            return {"title": "Excel", "process_name": "EXCEL"}
        if self.visual_screen == "excel_opened":
            return {"title": "hindi_english_parallel - Excel", "process_name": "EXCEL"}
        if self.visual_screen == "settings_bluetooth_on":
            return {"title": "Bluetooth & devices > Devices - Settings", "process_name": "SystemSettings"}
        if self.visual_screen == "settings_bluetooth_off":
            return {"title": "Bluetooth & devices > Devices - Settings", "process_name": "SystemSettings"}
        if self.visual_screen == "explorer_home":
            return {"title": "Home - File Explorer", "process_name": "explorer"}
        if self.visual_screen == "explorer_desktop":
            return {"title": "Desktop - File Explorer", "process_name": "explorer"}
        return {"title": "Visual Studio Code", "process_name": "Code"}


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


@pytest.mark.asyncio
async def test_coworker_service_runs_visual_open_visible_file_flow(monkeypatch, app_config) -> None:
    from assistant.desktop_coworker.visual_reasoner import DesktopCoworkerVisualReasoner

    async def fake_request_decision(self, *, image_path, prompt: str) -> str:  # noqa: ARG001
        return (
            '{'
            '"screen_summary": "Excel recent files are visible.",'
            '"completion_state": "continue",'
            '"message": "",'
            '"goal_completed_if_verified": true,'
            '"candidates": ['
            '{"label": "hindi_english_parallel", "kind": "file", "confidence": 0.96, "x": 185, "y": 610, "click_action": "double_click"}'
            '],'
            '"action": {'
            '"type": "double_click",'
            '"target_label": "hindi_english_parallel",'
            '"confidence": 0.96,'
            '"x": 185,'
            '"y": 610,'
            '"expected_window_title": "hindi_english_parallel",'
            '"goal_completed_if_verified": true'
            "}"
            "}"
        )

    monkeypatch.setattr(DesktopCoworkerVisualReasoner, "_request_decision", fake_request_decision)
    app_config.desktop_coworker.enabled = True
    app_config.desktop_coworker.allow_semantic_clicks = True
    app_config.desktop_input.enabled = True
    app_config.desktop_vision.enabled = True
    registry = CoworkerToolRegistry()
    service = DesktopCoworkerService(app_config, registry)
    await service.initialize()
    capture_dir = app_config.agent.workspace_dir / "desktop"
    capture_dir.mkdir(parents=True, exist_ok=True)
    (capture_dir / "window-2.png").write_bytes(b"fake")
    (capture_dir / "window-3.png").write_bytes(b"fake")

    task = await service.run_task_request(
        user_id=app_config.users.default_user_id,
        session_key="webchat_main",
        request_text="open the file you see on screen now",
    )

    assert task["status"] == "completed"
    assert task["current_step_index"] == 1
    assert task["latest_state"]["active_window"]["title"] == "hindi_english_parallel - Excel"
    assert task["transcript"][0]["step_type"] == "visual_task"
    assert task["transcript"][0]["visual_substeps"][0]["status"] == "completed"
    assert [name for name, _payload in registry.calls if name == "desktop_mouse_click"] == ["desktop_mouse_click"]
    assert [name for name, _payload in registry.calls if name == "desktop_read_screen"] == []


@pytest.mark.asyncio
async def test_coworker_service_uses_deterministic_explorer_open_for_visible_folder(monkeypatch, app_config) -> None:
    from assistant.desktop_coworker.visual_reasoner import DesktopCoworkerVisualReasoner

    async def fake_request_decision(self, *, image_path, prompt: str) -> str:  # noqa: ARG001
        return (
            '{'
            '"screen_summary": "File Explorer home is visible.",'
            '"completion_state": "continue",'
            '"message": "",'
            '"goal_completed_if_verified": true,'
            '"candidates": ['
            '{"label": "Desktop", "kind": "row", "confidence": 0.97, "x": 90, "y": 175, "click_action": "click"}'
            '],'
            '"action": {'
            '"type": "click",'
            '"target_label": "Desktop",'
            '"confidence": 0.97,'
            '"x": 90,'
            '"y": 175,'
            '"goal_completed_if_verified": true'
            "}"
            "}"
        )

    monkeypatch.setattr(DesktopCoworkerVisualReasoner, "_request_decision", fake_request_decision)
    app_config.desktop_coworker.enabled = True
    app_config.desktop_coworker.allow_semantic_clicks = True
    app_config.desktop_input.enabled = True
    app_config.desktop_vision.enabled = True
    app_config.system_access.enabled = True
    registry = CoworkerToolRegistry()
    registry.visual_screen = "explorer_home"
    service = DesktopCoworkerService(app_config, registry)
    await service.initialize()
    capture_dir = app_config.agent.workspace_dir / "desktop"
    capture_dir.mkdir(parents=True, exist_ok=True)
    (capture_dir / "window-explorer-home.png").write_bytes(b"fake")
    (capture_dir / "window-explorer-desktop.png").write_bytes(b"fake")

    task = await service.run_task_request(
        user_id=app_config.users.default_user_id,
        session_key="webchat_main",
        request_text="click on the desktop",
    )

    assert task["status"] == "completed"
    assert task["latest_state"]["active_window"]["title"] == "Desktop - File Explorer"
    assert [name for name, _payload in registry.calls if name == "apps_open"] == ["apps_open"]
    assert [name for name, _payload in registry.calls if name == "desktop_mouse_click"] == []


@pytest.mark.asyncio
async def test_coworker_service_rejects_false_completed_visual_state(monkeypatch, app_config) -> None:
    from assistant.desktop_coworker.visual_reasoner import DesktopCoworkerVisualReasoner

    calls = {"count": 0}

    async def fake_request_decision(self, *, image_path, prompt: str) -> str:  # noqa: ARG001
        calls["count"] += 1
        if calls["count"] == 1:
            return (
                '{'
                '"screen_summary": "Excel recent files are visible.",'
                '"completion_state": "continue",'
                '"message": "",'
                '"goal_completed_if_verified": true,'
                '"candidates": ['
                '{"label": "hindi_english_parallel", "kind": "file", "confidence": 0.95, "x": 185, "y": 610, "click_action": "double_click"}'
                '],'
                '"action": {'
                '"type": "double_click",'
                '"target_label": "hindi_english_parallel",'
                '"confidence": 0.95,'
                '"x": 185,'
                '"y": 610,'
                '"expected_window_title": "hindi_english_parallel",'
                '"goal_completed_if_verified": true'
                "}"
                "}"
            )
        return (
            '{'
            '"screen_summary": "Excel recent files are still visible.",'
            '"completion_state": "completed",'
            '"message": "I opened the visible file.",'
            '"goal_completed_if_verified": true,'
            '"candidates": [],'
            '"action": {'
            '"type": "complete",'
            '"target_label": "hindi_english_parallel",'
            '"expected_window_title": "hindi_english_parallel",'
            '"goal_completed_if_verified": true'
            "}"
            "}"
        )

    monkeypatch.setattr(DesktopCoworkerVisualReasoner, "_request_decision", fake_request_decision)
    app_config.desktop_coworker.enabled = True
    app_config.desktop_coworker.allow_semantic_clicks = True
    app_config.desktop_input.enabled = True
    app_config.desktop_vision.enabled = True
    registry = CoworkerToolRegistry()
    registry.search_returns_excel_file_match = False
    registry.mouse_click_opens_target = False
    service = DesktopCoworkerService(app_config, registry)
    await service.initialize()
    capture_dir = app_config.agent.workspace_dir / "desktop"
    capture_dir.mkdir(parents=True, exist_ok=True)
    (capture_dir / "window-2.png").write_bytes(b"fake")

    task = await service.run_task_request(
        user_id=app_config.users.default_user_id,
        session_key="webchat_main",
        request_text="open the file you see on screen now",
    )

    assert task["status"] == "failed"
    assert calls["count"] >= 2
    assert "same screen" in str(task.get("error", "") or task["transcript"][-1]["summary"]).lower() or "evidence" in str(task["transcript"][-1]["summary"]).lower()


@pytest.mark.asyncio
async def test_coworker_service_prefers_deterministic_excel_open_for_visible_recent_file(monkeypatch, app_config) -> None:
    from assistant.desktop_coworker.visual_reasoner import DesktopCoworkerVisualReasoner

    async def fake_request_decision(self, *, image_path, prompt: str) -> str:  # noqa: ARG001
        return (
            '{'
            '"screen_summary": "Excel recent files are visible.",'
            '"completion_state": "continue",'
            '"message": "",'
            '"goal_completed_if_verified": true,'
            '"candidates": ['
            '{"label": "hindi_english_parallel", "kind": "file", "confidence": 0.98, "x": 185, "y": 610, "click_action": "double_click"}'
            '],'
            '"action": {'
            '"type": "double_click",'
            '"target_label": "hindi_english_parallel",'
            '"target_kind": "file",'
            '"confidence": 0.98,'
            '"x": 185,'
            '"y": 610,'
            '"expected_window_title": "hindi_english_parallel",'
            '"goal_completed_if_verified": true'
            "}"
            "}"
        )

    monkeypatch.setattr(DesktopCoworkerVisualReasoner, "_request_decision", fake_request_decision)
    app_config.desktop_coworker.enabled = True
    app_config.desktop_coworker.allow_semantic_clicks = True
    app_config.desktop_input.enabled = True
    app_config.desktop_vision.enabled = True
    app_config.system_access.enabled = True
    registry = CoworkerToolRegistry()
    registry.search_returns_excel_file_match = True
    service = DesktopCoworkerService(app_config, registry)
    await service.initialize()
    capture_dir = app_config.agent.workspace_dir / "desktop"
    capture_dir.mkdir(parents=True, exist_ok=True)
    (capture_dir / "window-2.png").write_bytes(b"fake")
    (capture_dir / "window-3.png").write_bytes(b"fake")

    task = await service.run_task_request(
        user_id=app_config.users.default_user_id,
        session_key="webchat_main",
        request_text="open the highlighted recent Excel file",
    )

    assert task["status"] == "completed"
    assert task["latest_state"]["active_window"]["title"] == "hindi_english_parallel - Excel"
    assert [name for name, _payload in registry.calls if name == "apps_open"] == ["apps_open"]
    assert [name for name, _payload in registry.calls if name == "desktop_mouse_click"] == []


@pytest.mark.asyncio
async def test_coworker_service_runs_bluetooth_toggle_visual_flow(monkeypatch, app_config) -> None:
    from assistant.desktop_coworker.visual_reasoner import DesktopCoworkerVisualReasoner

    async def fake_request_decision(self, *, image_path, prompt: str) -> str:  # noqa: ARG001
        return (
            '{'
            '"screen_summary": "Bluetooth settings are visible and Bluetooth is currently on.",'
            '"completion_state": "continue",'
            '"message": "",'
            '"goal_completed_if_verified": true,'
            '"candidates": ['
            '{"label": "On", "kind": "toggle", "confidence": 0.95, "x": 930, "y": 180, "click_action": "click"}'
            '],'
            '"action": {'
            '"type": "click",'
            '"target_label": "On",'
            '"target_kind": "toggle",'
            '"confidence": 0.95,'
            '"x": 930,'
            '"y": 180,'
            '"expected_text_after": "Off",'
            '"expected_window_title": "Devices",'
            '"expected_process_name": "SystemSettings",'
            '"goal_completed_if_verified": true'
            "}"
            "}"
        )

    monkeypatch.setattr(DesktopCoworkerVisualReasoner, "_request_decision", fake_request_decision)
    app_config.desktop_coworker.enabled = True
    app_config.desktop_coworker.allow_semantic_clicks = True
    app_config.desktop_input.enabled = True
    app_config.desktop_vision.enabled = True
    app_config.app_skills.enabled = True
    app_config.app_skills.system_enabled = True
    registry = CoworkerToolRegistry()
    service = DesktopCoworkerService(app_config, registry)
    await service.initialize()
    capture_dir = app_config.agent.workspace_dir / "desktop"
    capture_dir.mkdir(parents=True, exist_ok=True)
    (capture_dir / "window-settings-on.png").write_bytes(b"fake")
    (capture_dir / "window-settings-off.png").write_bytes(b"fake")

    task = await service.run_task_request(
        user_id=app_config.users.default_user_id,
        session_key="telegram:123",
        request_text="open bluetooth settings and turn off the bluetooth",
        channel_name="telegram",
    )

    assert task["status"] == "completed"
    assert len(task["transcript"]) == 2
    assert task["transcript"][0]["step_type"] == "system_open_settings"
    assert task["transcript"][1]["step_type"] == "visual_task"
    assert "off" in task["latest_state"]["screen_text"].lower()
    assert [name for name, _payload in registry.calls if name == "system_open_settings"] == ["system_open_settings"]
    assert [name for name, _payload in registry.calls if name == "desktop_mouse_click"] == ["desktop_mouse_click"]
    assert [name for name, _payload in registry.calls if name == "system_bluetooth_status"] == []
    assert [name for name, _payload in registry.calls if name == "desktop_read_screen"] == []


def test_visual_reasoner_normalizes_type_text_and_scroll_actions(app_config) -> None:
    from assistant.desktop_coworker.visual_reasoner import DesktopCoworkerVisualReasoner

    reasoner = DesktopCoworkerVisualReasoner(app_config)

    typed = reasoner._normalize_action(
        {
            "type": "type_text",
            "text": "hello world",
            "confidence": 0.84,
            "expected_text_after": "hello world",
        },
        candidates=[],
    )
    assert typed["type"] == "type_text"
    assert typed["text"] == "hello world"
    assert typed["expected_text_after"] == "hello world"

    scroll = reasoner._normalize_action(
        {
            "type": "scroll",
            "direction": "down",
            "amount": 3,
            "confidence": 0.61,
        },
        candidates=[],
    )
    assert scroll["type"] == "scroll"
    assert scroll["direction"] == "down"
    assert scroll["amount"] == 3


@pytest.mark.asyncio
async def test_visual_controller_executes_type_text_action(app_config) -> None:
    from assistant.desktop_coworker.visual_controller import DesktopCoworkerVisualController

    app_config.desktop_coworker.enabled = True
    app_config.desktop_input.enabled = True
    registry = CoworkerToolRegistry()
    controller = DesktopCoworkerVisualController(app_config, registry)

    result = await controller._execute_action(
        action={"type": "type_text", "text": "hello world"},
        state={"active_window": {"title": "Untitled - Notepad", "process_name": "notepad"}},
        session_key="webchat_main",
        user_id=app_config.users.default_user_id,
        connection_id="conn-type",
        channel_name="webchat",
    )

    assert result["status"] == "completed"
    type_calls = [payload for name, payload in registry.calls if name == "desktop_keyboard_type"]
    assert len(type_calls) == 1
    assert type_calls[0]["text"] == "hello world"
    assert type_calls[0]["expected_window_title"] == "Untitled - Notepad"
    assert type_calls[0]["expected_process_name"] == "notepad"


@pytest.mark.asyncio
async def test_visual_controller_executes_scroll_action(app_config) -> None:
    from assistant.desktop_coworker.visual_controller import DesktopCoworkerVisualController

    app_config.desktop_coworker.enabled = True
    app_config.desktop_input.enabled = True
    registry = CoworkerToolRegistry()
    controller = DesktopCoworkerVisualController(app_config, registry)

    result = await controller._execute_action(
        action={"type": "scroll", "direction": "down", "amount": 2},
        state={"active_window": {"title": "Home - File Explorer", "process_name": "explorer"}},
        session_key="webchat_main",
        user_id=app_config.users.default_user_id,
        connection_id="conn-scroll",
        channel_name="webchat",
    )

    assert result["status"] == "completed"
    scroll_calls = [payload for name, payload in registry.calls if name == "desktop_mouse_scroll"]
    assert len(scroll_calls) == 1
    assert scroll_calls[0]["direction"] == "down"
    assert scroll_calls[0]["amount"] == 2
    assert scroll_calls[0]["expected_window_title"] == "Home - File Explorer"
    assert scroll_calls[0]["expected_process_name"] == "explorer"


def test_visual_controller_verifies_type_text_and_scroll_from_screen_change(app_config) -> None:
    from assistant.desktop_coworker.visual_controller import DesktopCoworkerVisualController

    controller = DesktopCoworkerVisualController(app_config, CoworkerToolRegistry())

    typed_verification = controller._verify_action(
        action={"type": "type_text", "text": "hello", "expected_text_after": "hello"},
        before={
            "screen_text": "File name",
            "active_window": {"title": "Save As", "process_name": "explorer"},
        },
        after={
            "screen_text": "File name hello",
            "active_window": {"title": "Save As", "process_name": "explorer"},
        },
        tool_result={"status": "completed"},
    )
    assert typed_verification["ok"] is True

    scroll_verification = controller._verify_action(
        action={"type": "scroll", "direction": "down", "amount": 2, "expected_text_after": "More items"},
        before={
            "screen_text": "Item A Item B",
            "active_window": {"title": "Home - File Explorer", "process_name": "explorer"},
        },
        after={
            "screen_text": "Item C More items",
            "active_window": {"title": "Home - File Explorer", "process_name": "explorer"},
        },
        tool_result={"status": "completed"},
    )
    assert scroll_verification["ok"] is True


def test_visual_controller_verifies_click_from_screen_change_without_title_change(app_config) -> None:
    from assistant.desktop_coworker.visual_controller import DesktopCoworkerVisualController

    controller = DesktopCoworkerVisualController(app_config, CoworkerToolRegistry())

    verification = controller._verify_action(
        action={"type": "click", "target_label": "Add device", "confidence": 0.82},
        before={
            "screen_text": "Bluetooth On Add device",
            "active_window": {"title": "Bluetooth & devices > Devices - Settings", "process_name": "SystemSettings"},
        },
        after={
            "screen_text": "Bluetooth On Add device Choose a device",
            "active_window": {"title": "Bluetooth & devices > Devices - Settings", "process_name": "SystemSettings"},
        },
        tool_result={"status": "completed"},
    )

    assert verification["ok"] is True


def test_visual_controller_verifies_when_new_element_appears(app_config) -> None:
    from assistant.desktop_coworker.visual_controller import DesktopCoworkerVisualController

    controller = DesktopCoworkerVisualController(app_config, CoworkerToolRegistry())

    verification = controller._verify_action(
        action={"type": "click", "target_label": "More", "confidence": 0.76},
        before={
            "screen_text": "Menu More",
            "target_candidates": [{"label": "More"}],
            "active_window": {"title": "App", "process_name": "app"},
        },
        after={
            "screen_text": "Menu More Settings Help",
            "target_candidates": [{"label": "More"}, {"label": "Settings"}, {"label": "Help"}],
            "active_window": {"title": "App", "process_name": "app"},
        },
        tool_result={"status": "completed"},
    )

    assert verification["ok"] is True


def test_visual_controller_completion_accepts_high_confidence_fallback(app_config) -> None:
    from assistant.desktop_coworker.visual_controller import DesktopCoworkerVisualController

    controller = DesktopCoworkerVisualController(app_config, CoworkerToolRegistry())

    verification = controller._verify_completion_state(
        goal="click the visible settings button",
        decision={
            "action": {
                "type": "click",
                "target_label": "Settings",
                "confidence": 0.91,
            }
        },
        current_state={
            "screen_text": "Menu Settings",
            "active_window": {"title": "App", "process_name": "app"},
            "target_candidates": [{"label": "Settings"}],
        },
        previous_state={
            "screen_text": "Menu Settings",
            "active_window": {"title": "App", "process_name": "app"},
            "target_candidates": [{"label": "Settings"}],
        },
    )

    assert verification["ok"] is True


@pytest.mark.asyncio
async def test_visual_reasoner_can_handle_uses_llm_classifier(app_config) -> None:
    from assistant.desktop_coworker.visual_reasoner import DesktopCoworkerVisualReasoner

    app_config.llm.gemini_api_key = "test-key"
    reasoner = DesktopCoworkerVisualReasoner(app_config)

    async def fake_request_text_completion(*, payload, model_names):  # noqa: ARG001
        return (
            '{'
            '"desktop_ui_task": true,'
            '"task_kind": "visual",'
            '"summary": "Launch Chrome and open Google.",'
            '"normalized_request": "launch chrome and go to google.com"'
            '}'
        )

    reasoner._request_text_completion = fake_request_text_completion  # type: ignore[method-assign]

    assert await reasoner.can_handle("please launch Chrome and go to google.com") is True


@pytest.mark.asyncio
async def test_visual_reasoner_build_plan_uses_richer_analysis(app_config) -> None:
    from assistant.desktop_coworker.visual_reasoner import DesktopCoworkerVisualReasoner

    app_config.llm.gemini_api_key = "test-key"
    reasoner = DesktopCoworkerVisualReasoner(app_config)

    async def fake_request_text_completion(*, payload, model_names):  # noqa: ARG001
        return (
            '{'
            '"desktop_ui_task": true,'
            '"task_kind": "visual",'
            '"summary": "Launch Chrome and open Google.",'
            '"normalized_request": "launch chrome and go to google.com"'
            '}'
        )

    reasoner._request_text_completion = fake_request_text_completion  # type: ignore[method-assign]

    plan = await reasoner.build_plan("please launch Chrome and go to google.com")

    assert plan is not None
    assert plan["summary"] == "Launch Chrome and open Google."
    assert plan["steps"][0]["payload"]["goal"] == "launch chrome and go to google.com"


def test_visual_controller_immediate_verifier_accepts_high_confidence_fallback(app_config) -> None:
    from assistant.desktop_coworker.visual_controller import DesktopCoworkerVisualController

    controller = DesktopCoworkerVisualController(app_config, CoworkerToolRegistry())

    verification = controller._verify_action(
        action={"type": "click", "target_label": "Settings", "target_kind": "button", "confidence": 0.91},
        before={
            "screen_text": "Menu Settings",
            "active_window": {"title": "App", "process_name": "app"},
            "target_candidates": [{"label": "Settings"}],
        },
        after={
            "screen_text": "Menu Settings",
            "active_window": {"title": "App", "process_name": "app"},
            "target_candidates": [{"label": "Settings"}],
        },
        tool_result={"status": "completed"},
    )

    assert verification["ok"] is True
