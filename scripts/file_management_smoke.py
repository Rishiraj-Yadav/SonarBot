from __future__ import annotations

import asyncio
from pathlib import Path

from assistant.config.schema import AppConfig
from assistant.system_access import SystemAccessManager
from assistant.tools.file_tool import build_file_tools
from assistant.tools.host_file_tool import build_host_file_tools


def _build_config(base: Path) -> AppConfig:
    workspace = base / "workspace"
    home = base / "home"
    assistant_home = base / ".assistant"
    logs = base / "logs"
    backups = base / "backups"

    workspace.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    assistant_home.mkdir(parents=True, exist_ok=True)

    for name in ["SOUL.md", "AGENTS.md", "USER.md", "IDENTITY.md", "TOOLS.md", "MEMORY.md", "STANDING_ORDERS.md", "BOOT.md"]:
        (workspace / name).write_text("x", encoding="utf-8")

    config = AppConfig.model_validate(
        {
            "assistant_home": str(assistant_home),
            "gateway": {"host": "127.0.0.1", "port": 8765, "token": "secret-token"},
            "agent": {
                "workspace_dir": str(workspace),
                "model": "gemini-test",
                "max_tokens": 2048,
                "context_window": 512,
            },
            "llm": {"gemini_api_key": "fake-key"},
            "channels": {"enabled": []},
        }
    )
    config.system_access.enabled = True
    config.system_access.home_root = home
    config.system_access.protected_roots = []
    config.system_access.audit_log_path = logs / "system_actions.jsonl"
    config.system_access.backup_root = backups
    config.system_access.path_rules = [
        {
            "path": str(home),
            "read": "auto_allow",
            "write": "auto_allow",
            "overwrite": "auto_allow",
            "delete": "auto_allow",
            "execute": "ask_once",
        }
    ]
    config.ensure_runtime_dirs()
    return config


async def main() -> None:
    base = Path(".tmp/file-management-smoke").resolve()
    base.mkdir(parents=True, exist_ok=True)
    config = _build_config(base)

    workspace_tools = {tool.name: tool for tool in build_file_tools(config.agent.workspace_dir)}
    assert set(workspace_tools) == {"read_file", "write_file"}
    assert "create_folder" not in workspace_tools
    assert "organize_folders" not in workspace_tools

    nested_file = config.agent.workspace_dir / "notes" / "draft.txt"
    await workspace_tools["write_file"].handler({"path": "notes/draft.txt", "content": "workspace note"})
    assert nested_file.exists()
    assert (await workspace_tools["read_file"].handler({"path": "notes/draft.txt"}))["content"] == "workspace note"

    manager = SystemAccessManager(config)
    await manager.initialize()
    host_tools = {tool.name: tool for tool in build_host_file_tools(manager)}
    assert {"read_host_file", "write_host_file", "delete_host_file", "copy_host_file", "move_host_file", "list_host_dir", "search_host_files"}.issubset(
        host_tools
    )
    assert "create_folder" not in host_tools
    assert "organize_folders" not in host_tools
    assert "rename_host_file" not in host_tools

    original = config.system_access.home_root / "Documents" / "report.txt"
    copied = config.system_access.home_root / "Documents" / "report-copy.txt"
    renamed = config.system_access.home_root / "Documents" / "report-renamed.txt"

    await host_tools["write_host_file"].handler(
        {
            "path": str(original),
            "content": "hello world",
            "session_key": "main",
            "session_id": "smoke",
            "user_id": "default",
        }
    )
    assert original.exists()

    await host_tools["copy_host_file"].handler(
        {
            "source": str(original),
            "destination": str(copied),
            "session_key": "main",
            "session_id": "smoke",
            "user_id": "default",
        }
    )
    assert copied.exists()

    await host_tools["move_host_file"].handler(
        {
            "source": str(copied),
            "destination": str(renamed),
            "session_key": "main",
            "session_id": "smoke",
            "user_id": "default",
        }
    )
    assert renamed.exists() and not copied.exists()

    listing = await host_tools["list_host_dir"].handler(
        {"path": str(config.system_access.home_root / "Documents"), "session_id": "smoke", "user_id": "default"}
    )
    assert {"report.txt", "report-renamed.txt"}.issubset({entry["name"] for entry in listing["entries"]})

    search = await host_tools["search_host_files"].handler(
        {
            "root": str(config.system_access.home_root),
            "pattern": "*",
            "text": "",
            "name_query": "report-renamed",
            "directories_only": False,
            "files_only": True,
            "limit": 10,
            "session_id": "smoke",
            "user_id": "default",
        }
    )
    assert search["matches"] and search["matches"][0]["name"] == "report-renamed.txt"

    delete_task = asyncio.create_task(
        host_tools["delete_host_file"].handler(
            {
                "path": str(renamed),
                "session_key": "main",
                "session_id": "smoke",
                "user_id": "default",
            }
        )
    )
    await asyncio.sleep(0.05)
    approvals = await manager.list_approvals("default")
    await manager.decide_approval(approvals[0]["approval_id"], "approved")
    await delete_task
    assert not renamed.exists()

    print("file-management smoke check passed")


if __name__ == "__main__":
    asyncio.run(main())
