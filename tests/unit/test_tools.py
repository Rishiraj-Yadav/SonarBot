from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from assistant.tools import create_default_tool_registry
from assistant.tools.desktop_input_tool import build_desktop_input_tools
from assistant.tools.exec_tool import build_exec_tool
from assistant.tools.file_tool import build_file_tools
from assistant.system_access import SystemAccessManager


@pytest.mark.asyncio
async def test_file_tool_blocks_path_traversal(tmp_path: Path) -> None:
    tool = build_file_tools(tmp_path)[0]
    with pytest.raises(ValueError):
        await tool.handler({"path": "../outside.txt"})


@pytest.mark.asyncio
async def test_exec_tool_returns_stdout(tmp_path: Path) -> None:
    tool = build_exec_tool(tmp_path)
    result = await tool.handler({"command": "python -c \"print('hello')\"", "timeout": 5})
    assert result["exit_code"] == 0
    assert "hello" in result["stdout"]


@pytest.mark.asyncio
async def test_exec_tool_redacts_host_output(app_config, tmp_path: Path) -> None:
    home_root = tmp_path / "home"
    home_root.mkdir(parents=True, exist_ok=True)
    app_config.system_access.enabled = True
    app_config.system_access.home_root = home_root
    app_config.system_access.protected_roots = []
    app_config.system_access.backup_root = tmp_path / "backups"
    app_config.system_access.audit_log_path = tmp_path / "logs" / "system_actions.jsonl"
    app_config.ensure_runtime_dirs()

    manager = SystemAccessManager(app_config)
    await manager.initialize()
    tool = build_exec_tool(tmp_path, system_access_manager=manager)

    task = asyncio.create_task(
        tool.handler(
            {
                "command": "echo hello",
                "timeout": 5,
                "host": True,
                "session_id": "sess-host",
                "user_id": "default",
            }
        )
    )
    await asyncio.sleep(0.05)
    approvals = await manager.list_approvals("default")
    await manager.decide_approval(approvals[0]["approval_id"], "approved")
    result = await task
    persisted = tool.redactor({"host": True}, result) if tool.redactor else result

    assert "hello" in result["stdout"].lower()
    assert "stdout" not in persisted
    assert persisted["audit_id"]


def test_default_tool_registry_skips_app_tools_when_disabled(app_config) -> None:
    registry = create_default_tool_registry(app_config)

    assert "apps_list_windows" not in registry.names()
    assert "apps_open" not in registry.names()


def test_default_tool_registry_registers_app_tools_when_enabled(app_config) -> None:
    app_config.desktop_apps.enabled = True

    registry = create_default_tool_registry(app_config)

    assert "apps_list_windows" in registry.names()
    assert "apps_open" in registry.names()
    assert "apps_snap" in registry.names()


def test_default_tool_registry_skips_desktop_vision_tools_when_disabled(app_config) -> None:
    registry = create_default_tool_registry(app_config)

    assert "desktop_active_window" not in registry.names()
    assert "desktop_screenshot" not in registry.names()


def test_default_tool_registry_registers_desktop_vision_tools_when_enabled(app_config) -> None:
    app_config.desktop_vision.enabled = True

    registry = create_default_tool_registry(app_config)

    assert "desktop_active_window" in registry.names()
    assert "desktop_screenshot" in registry.names()
    assert "desktop_read_screen" in registry.names()


def test_default_tool_registry_skips_desktop_input_tools_when_disabled(app_config) -> None:
    registry = create_default_tool_registry(app_config)

    assert "desktop_mouse_position" not in registry.names()
    assert "desktop_keyboard_type" not in registry.names()


def test_default_tool_registry_registers_desktop_input_tools_when_enabled(app_config) -> None:
    app_config.desktop_input.enabled = True

    registry = create_default_tool_registry(app_config)

    assert "desktop_mouse_position" in registry.names()
    assert "desktop_mouse_click" in registry.names()
    assert "desktop_clipboard_write" in registry.names()


@pytest.mark.asyncio
async def test_desktop_keyboard_type_accepts_content_alias(app_config) -> None:
    app_config.desktop_input.enabled = True
    app_config.desktop_input.confirm_typing = False
    tools, runtime = build_desktop_input_tools(app_config)
    tool = next(item for item in tools if item.name == "desktop_keyboard_type")

    runtime.type_text = lambda **kwargs: {"characters_typed": len(str(kwargs["text"]))}

    result = await tool.handler({"content": "R:/6_semester/test.txt"})

    assert result["status"] == "completed"
    assert result["characters_typed"] == len("R:/6_semester/test.txt")


def test_desktop_input_tools_redact_sensitive_arguments(app_config) -> None:
    app_config.desktop_input.enabled = True
    registry = create_default_tool_registry(app_config)

    redacted_type = registry.redact_input("desktop_keyboard_type", {"text": "super secret"})
    redacted_clipboard = registry.redact_input("desktop_clipboard_write", {"text": "copy me"})

    assert redacted_type == {"text_chars": 12}
    assert redacted_clipboard == {"text_chars": 7}


def test_default_tool_registry_skips_app_skill_tools_when_disabled(app_config) -> None:
    registry = create_default_tool_registry(app_config)

    assert "vscode_open_target" not in registry.names()
    assert "document_create" not in registry.names()
    assert "task_manager_summary" not in registry.names()


def test_default_tool_registry_registers_app_skill_tools_when_enabled(app_config) -> None:
    app_config.desktop_apps.enabled = True
    app_config.app_skills.enabled = True

    registry = create_default_tool_registry(app_config)

    assert "vscode_open_target" in registry.names()
    assert "document_create" in registry.names()
    assert "excel_preview" in registry.names()
    assert "system_volume_status" in registry.names()
    assert "task_manager_summary" in registry.names()
    assert "preset_run" in registry.names()
