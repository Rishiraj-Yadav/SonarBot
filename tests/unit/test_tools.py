from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

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
