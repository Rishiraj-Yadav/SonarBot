from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from assistant.system_access import SystemAccessManager
from assistant.tools.file_tool import build_file_tools
from assistant.tools.host_file_tool import build_host_file_tools


@pytest.mark.asyncio
async def test_workspace_file_tools_create_and_read_nested_files(app_config) -> None:
    workspace_tools = {tool.name: tool for tool in build_file_tools(app_config.agent.workspace_dir)}

    assert set(workspace_tools) == {"read_file", "write_file"}
    assert "delete_file" not in workspace_tools
    assert "copy_file" not in workspace_tools
    assert "move_file" not in workspace_tools
    assert "search_files" not in workspace_tools
    assert "create_folder" not in workspace_tools
    assert "organize_folders" not in workspace_tools

    target = app_config.agent.workspace_dir / "notes" / "draft.txt"
    result = await workspace_tools["write_file"].handler({"path": "notes/draft.txt", "content": "workspace note"})
    readback = await workspace_tools["read_file"].handler({"path": "notes/draft.txt"})

    assert result["path"] == str(target.resolve())
    assert target.exists()
    assert readback["content"] == "workspace note"


@pytest.mark.asyncio
async def test_host_file_tools_cover_manage_surface_end_to_end(app_config, tmp_path: Path) -> None:
    home_root = tmp_path / "home"
    home_root.mkdir(parents=True, exist_ok=True)

    app_config.system_access.enabled = True
    app_config.system_access.home_root = home_root
    app_config.system_access.protected_roots = []
    app_config.system_access.path_rules = [
        {
            "path": str(home_root),
            "read": "auto_allow",
            "write": "auto_allow",
            "overwrite": "auto_allow",
            "delete": "auto_allow",
            "execute": "ask_once",
        }
    ]
    app_config.ensure_runtime_dirs()

    manager = SystemAccessManager(app_config)
    await manager.initialize()

    tools = {tool.name: tool for tool in build_host_file_tools(manager)}
    expected = {
        "read_host_file",
        "read_host_document",
        "write_host_file",
        "delete_host_file",
        "copy_host_file",
        "move_host_file",
        "list_host_dir",
        "search_host_files",
    }
    assert expected.issubset(tools)
    assert "create_folder" not in tools
    assert "organize_folders" not in tools
    assert "rename_host_file" not in tools

    original = home_root / "Documents" / "report.txt"
    copied = home_root / "Documents" / "report-copy.txt"
    renamed = home_root / "Documents" / "report-renamed.txt"

    write_result = await tools["write_host_file"].handler(
        {
            "path": str(original),
            "content": "hello world",
            "session_key": "main",
            "session_id": "sess-file",
            "user_id": "default",
        }
    )
    assert original.exists()
    assert write_result["file_format"] == "text"

    read_result = await tools["read_host_file"].handler(
        {"path": str(original), "session_id": "sess-file", "user_id": "default"}
    )
    assert read_result["content"] == "hello world"

    copy_result = await tools["copy_host_file"].handler(
        {
            "source": str(original),
            "destination": str(copied),
            "session_key": "main",
            "session_id": "sess-file",
            "user_id": "default",
        }
    )
    assert copy_result["copied"] is True
    assert copied.read_text(encoding="utf-8") == "hello world"

    move_result = await tools["move_host_file"].handler(
        {
            "source": str(copied),
            "destination": str(renamed),
            "session_key": "main",
            "session_id": "sess-file",
            "user_id": "default",
        }
    )
    assert move_result["moved"] is True
    assert renamed.exists()
    assert not copied.exists()

    listing = await tools["list_host_dir"].handler(
        {"path": str(home_root / "Documents"), "session_id": "sess-file", "user_id": "default"}
    )
    listed_names = {entry["name"] for entry in listing["entries"]}
    assert {"report.txt", "report-renamed.txt"}.issubset(listed_names)

    search = await tools["search_host_files"].handler(
        {
            "root": str(home_root),
            "pattern": "*",
            "text": "",
            "name_query": "report-renamed",
            "directories_only": False,
            "files_only": True,
            "limit": 10,
            "session_id": "sess-file",
            "user_id": "default",
        }
    )
    assert search["matches"]
    assert search["matches"][0]["name"] == "report-renamed.txt"

    delete_task = asyncio.create_task(
        tools["delete_host_file"].handler(
            {
                "path": str(renamed),
                "session_key": "main",
                "session_id": "sess-file",
                "user_id": "default",
            }
        )
    )
    await asyncio.sleep(0.05)
    approvals = await manager.list_approvals("default")
    await manager.decide_approval(approvals[0]["approval_id"], "approved")
    delete_result = await delete_task
    assert delete_result["deleted"] is True
    assert not renamed.exists()
