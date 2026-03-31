from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from assistant.system_access import SystemAccessManager
from assistant.system_access.policy import classify_command
from assistant.tools.host_file_tool import build_host_file_tools


@pytest.mark.asyncio
async def test_system_access_policy_classifies_windows_commands() -> None:
    assert classify_command("Get-ChildItem ~")[0] == "auto_allow"
    assert classify_command("Remove-Item notes.txt")[0] == "always_ask"
    assert classify_command("Format-Volume -DriveLetter C")[0] == "deny"


@pytest.mark.asyncio
async def test_host_exec_approval_can_be_cached_per_session(app_config, tmp_path: Path) -> None:
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

    task = asyncio.create_task(
        manager.run_host_command(
            command="echo hello",
            session_key="main",
            session_id="sess-1",
            user_id="default",
        )
    )
    await asyncio.sleep(0.05)
    approvals = await manager.list_approvals("default")
    assert approvals
    await manager.decide_approval(approvals[0]["approval_id"], "approved")
    result = await task
    assert result["exit_code"] == 0
    assert "hello" in result["stdout"].lower()

    cached = await manager.run_host_command(
        command="echo hello again",
        session_key="main",
        session_id="sess-1",
        user_id="default",
    )
    assert cached["approval_mode"] == "session_cache"


@pytest.mark.asyncio
async def test_host_tools_redact_file_contents_and_restrict_paths(app_config, tmp_path: Path) -> None:
    home_root = tmp_path / "home"
    home_root.mkdir(parents=True, exist_ok=True)
    app_config.system_access.enabled = True
    app_config.system_access.home_root = home_root
    app_config.system_access.protected_roots = []
    app_config.system_access.backup_root = tmp_path / "backups"
    app_config.system_access.audit_log_path = tmp_path / "logs" / "system_actions.jsonl"
    app_config.ensure_runtime_dirs()

    target = home_root / "notes.txt"
    target.write_text("secret note", encoding="utf-8")
    manager = SystemAccessManager(app_config)
    await manager.initialize()
    read_tool = next(tool for tool in build_host_file_tools(manager) if tool.name == "read_host_file")

    result = await read_tool.handler({"path": str(target), "session_id": "sess-2", "user_id": "default"})
    persisted = read_tool.redactor({"path": str(target)}, result) if read_tool.redactor else result

    assert result["content"] == "secret note"
    assert "content" not in persisted
    assert persisted["audit_id"]

    denied_category, denied_reason = manager.runtime.classify_path_action(
        manager.runtime.resolve_host_path(str(tmp_path / ".." / "outside.txt")),
        "read",
    )
    assert denied_category == "deny"
    assert denied_reason == "outside_policy"


@pytest.mark.asyncio
async def test_restore_backup_recreates_previous_file_version(app_config, tmp_path: Path) -> None:
    home_root = tmp_path / "home"
    home_root.mkdir(parents=True, exist_ok=True)
    app_config.system_access.enabled = True
    app_config.system_access.home_root = home_root
    app_config.system_access.protected_roots = []
    app_config.system_access.backup_root = tmp_path / "backups"
    app_config.system_access.audit_log_path = tmp_path / "logs" / "system_actions.jsonl"
    app_config.ensure_runtime_dirs()

    target = home_root / "report.txt"
    target.write_text("old value", encoding="utf-8")
    manager = SystemAccessManager(app_config)
    await manager.initialize()

    task = asyncio.create_task(
        manager.write_host_file(
            path=str(target),
            content="new value",
            session_key="main",
            session_id="sess-3",
            user_id="default",
        )
    )
    await asyncio.sleep(0.05)
    approvals = await manager.list_approvals("default")
    await manager.decide_approval(approvals[0]["approval_id"], "approved")
    result = await task
    assert target.read_text(encoding="utf-8") == "new value"

    restored = await manager.restore_backup(str(result["backup_id"]), user_id="default")
    assert restored["backup_id"] == result["backup_id"]
    assert target.read_text(encoding="utf-8") == "old value"


@pytest.mark.asyncio
async def test_write_host_file_infers_document_formats_from_extension(app_config, tmp_path: Path) -> None:
    home_root = tmp_path / "home"
    home_root.mkdir(parents=True, exist_ok=True)
    app_config.system_access.enabled = True
    app_config.system_access.home_root = home_root
    app_config.system_access.protected_roots = []
    app_config.system_access.backup_root = tmp_path / "backups"
    app_config.system_access.audit_log_path = tmp_path / "logs" / "system_actions.jsonl"
    app_config.system_access.path_rules = [
        {
            "path": str(home_root),
            "read": "auto_allow",
            "write": "auto_allow",
            "overwrite": "auto_allow",
            "delete": "always_ask",
            "execute": "ask_once",
        }
    ]
    app_config.ensure_runtime_dirs()

    manager = SystemAccessManager(app_config)
    await manager.initialize()

    pdf_result = await manager.write_host_file(
        path=str(home_root / "report.pdf"),
        content="hello pdf",
        session_key="main",
        session_id="sess-doc-1",
        user_id="default",
    )
    docx_result = await manager.write_host_file(
        path=str(home_root / "report.docx"),
        content="hello docx",
        session_key="main",
        session_id="sess-doc-2",
        user_id="default",
    )

    pdf_bytes = (home_root / "report.pdf").read_bytes()
    docx_bytes = (home_root / "report.docx").read_bytes()

    assert pdf_result["file_format"] == "pdf"
    assert docx_result["file_format"] == "docx"
    assert pdf_bytes.startswith(b"%PDF-")
    assert docx_bytes.startswith(b"PK")


@pytest.mark.asyncio
async def test_read_host_file_extracts_docx_text(app_config, tmp_path: Path) -> None:
    home_root = tmp_path / "home"
    home_root.mkdir(parents=True, exist_ok=True)
    app_config.system_access.enabled = True
    app_config.system_access.home_root = home_root
    app_config.system_access.protected_roots = []
    app_config.system_access.backup_root = tmp_path / "backups"
    app_config.system_access.audit_log_path = tmp_path / "logs" / "system_actions.jsonl"
    app_config.system_access.path_rules = [
        {
            "path": str(home_root),
            "read": "auto_allow",
            "write": "auto_allow",
            "overwrite": "auto_allow",
            "delete": "always_ask",
            "execute": "ask_once",
        }
    ]
    app_config.ensure_runtime_dirs()

    manager = SystemAccessManager(app_config)
    await manager.initialize()
    target = home_root / "testing123.docx"
    await manager.write_host_file(
        path=str(target),
        content="hello from docx",
        session_key="main",
        session_id="sess-docx-read-1",
        user_id="default",
    )

    result = await manager.read_host_file(
        path=str(target),
        session_id="sess-docx-read-2",
        user_id="default",
    )

    assert result["file_format"] == "docx"
    assert "hello from docx" in result["content"]


@pytest.mark.asyncio
async def test_write_host_file_falls_back_to_new_file_when_target_is_locked(
    app_config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home_root = tmp_path / "home"
    home_root.mkdir(parents=True, exist_ok=True)
    app_config.system_access.enabled = True
    app_config.system_access.home_root = home_root
    app_config.system_access.protected_roots = []
    app_config.system_access.backup_root = tmp_path / "backups"
    app_config.system_access.audit_log_path = tmp_path / "logs" / "system_actions.jsonl"
    app_config.system_access.path_rules = [
        {
            "path": str(home_root),
            "read": "auto_allow",
            "write": "auto_allow",
            "overwrite": "auto_allow",
            "delete": "always_ask",
            "execute": "ask_once",
        }
    ]
    app_config.ensure_runtime_dirs()

    manager = SystemAccessManager(app_config)
    await manager.initialize()
    target = home_root / "locked.docx"
    await manager.write_host_file(
        path=str(target),
        content="hello from docx",
        session_key="main",
        session_id="sess-docx-lock-1",
        user_id="default",
    )

    original_write_content = manager.runtime.write_content

    async def _raise_only_for_target(path: Path, content: str):
        if path == target:
            raise PermissionError(13, "Permission denied", str(target))
        return await original_write_content(path, content)

    monkeypatch.setattr(manager.runtime, "write_content", _raise_only_for_target)

    result = await manager.write_host_file(
        path=str(target),
        content="updated value",
        session_key="main",
        session_id="sess-docx-lock-2",
        user_id="default",
    )

    assert result["status"] == "completed:fallback_new_file"
    assert result["path"].endswith("locked_updated.docx")
    assert result["fallback_from"] == str(target)
    assert Path(str(result["path"])).exists()


@pytest.mark.asyncio
async def test_search_host_files_prefers_named_directories_over_substring_noise(app_config, tmp_path: Path) -> None:
    home_root = tmp_path / "home"
    target_dir = home_root / "Documents" / "college" / "5sem"
    noise_file = home_root / "Desktop" / "project" / "node_modules" / "html5semantic.js"
    target_dir.mkdir(parents=True, exist_ok=True)
    noise_file.parent.mkdir(parents=True, exist_ok=True)
    noise_file.write_text("console.log('noise');", encoding="utf-8")

    app_config.system_access.enabled = True
    app_config.system_access.home_root = home_root
    app_config.system_access.protected_roots = []
    app_config.system_access.backup_root = tmp_path / "backups"
    app_config.system_access.audit_log_path = tmp_path / "logs" / "system_actions.jsonl"
    app_config.ensure_runtime_dirs()

    manager = SystemAccessManager(app_config)
    await manager.initialize()

    result = await manager.search_host_files(
        root=str(home_root),
        pattern="*",
        name_query="5sem",
        directories_only=True,
        limit=10,
        text="",
        session_id="sess-4",
        user_id="default",
    )

    matches = result["matches"]
    assert matches
    assert matches[0]["is_dir"] is True
    assert matches[0]["name"] == "5sem"
    assert all(item["is_dir"] for item in matches)


@pytest.mark.asyncio
async def test_search_host_files_matches_compact_names_with_spaced_query(app_config, tmp_path: Path) -> None:
    home_root = tmp_path / "home"
    target_dir = home_root / "Documents" / "college" / "5sem"
    target_dir.mkdir(parents=True, exist_ok=True)

    app_config.system_access.enabled = True
    app_config.system_access.home_root = home_root
    app_config.system_access.protected_roots = []
    app_config.system_access.backup_root = tmp_path / "backups"
    app_config.system_access.audit_log_path = tmp_path / "logs" / "system_actions.jsonl"
    app_config.ensure_runtime_dirs()

    manager = SystemAccessManager(app_config)
    await manager.initialize()

    result = await manager.search_host_files(
        root=str(home_root),
        pattern="*",
        name_query="5 sem",
        directories_only=True,
        limit=10,
        text="",
        session_id="sess-4b",
        user_id="default",
    )

    assert result["matches"]
    assert result["matches"][0]["name"] == "5sem"


@pytest.mark.asyncio
async def test_path_rules_use_most_specific_match_and_protected_roots_win(app_config, tmp_path: Path) -> None:
    drive_root = tmp_path / "drive-c"
    allowed = drive_root / "Users" / "Ritesh" / "Documents"
    readonly = allowed / "readonly"
    protected = allowed / "System Volume Information"
    readonly.mkdir(parents=True, exist_ok=True)
    protected.mkdir(parents=True, exist_ok=True)

    app_config.system_access.enabled = True
    app_config.system_access.home_root = drive_root
    app_config.system_access.path_rules = [
        {
            "path": str(allowed),
            "read": "auto_allow",
            "write": "ask_once",
            "overwrite": "always_ask",
            "delete": "always_ask",
            "execute": "ask_once",
        },
        {
            "path": str(readonly),
            "read": "auto_allow",
            "write": "deny",
            "overwrite": "deny",
            "delete": "deny",
            "execute": "deny",
        },
    ]
    app_config.system_access.protected_roots = [protected]
    app_config.system_access.backup_root = tmp_path / "backups"
    app_config.system_access.audit_log_path = tmp_path / "logs" / "system_actions.jsonl"
    app_config.ensure_runtime_dirs()

    manager = SystemAccessManager(app_config)
    await manager.initialize()

    read_category, _ = manager.runtime.classify_path_action(readonly / "note.txt", "read")
    write_category, _ = manager.runtime.classify_path_action(readonly / "note.txt", "write")
    protected_category, _ = manager.runtime.classify_path_action(protected / "index.txt", "read")

    assert read_category == "auto_allow"
    assert write_category == "deny"
    assert protected_category == "deny"


@pytest.mark.asyncio
async def test_search_host_files_can_search_all_allowed_roots(app_config, tmp_path: Path) -> None:
    c_documents = tmp_path / "drive-c" / "Users" / "Ritesh" / "Documents"
    r_drive = tmp_path / "drive-r"
    c_documents.mkdir(parents=True, exist_ok=True)
    (r_drive / "college" / "5sem").mkdir(parents=True, exist_ok=True)

    app_config.system_access.enabled = True
    app_config.system_access.home_root = c_documents.parent
    app_config.system_access.path_rules = [
        {"path": str(c_documents)},
        {"path": str(r_drive)},
    ]
    app_config.system_access.protected_roots = []
    app_config.system_access.backup_root = tmp_path / "backups"
    app_config.system_access.audit_log_path = tmp_path / "logs" / "system_actions.jsonl"
    app_config.ensure_runtime_dirs()

    manager = SystemAccessManager(app_config)
    await manager.initialize()

    result = await manager.search_host_files(
        root="@allowed",
        pattern="*",
        name_query="5sem",
        directories_only=True,
        limit=10,
        text="",
        session_id="sess-5",
        user_id="default",
    )

    assert any("5sem" == item["name"] for item in result["matches"])
    assert result["root"] == "@allowed"
    assert len(result["searched_roots"]) == 2


@pytest.mark.asyncio
async def test_host_command_blocks_paths_outside_path_policy(app_config, tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    blocked = tmp_path / "blocked"
    allowed.mkdir(parents=True, exist_ok=True)
    blocked.mkdir(parents=True, exist_ok=True)

    app_config.system_access.enabled = True
    app_config.system_access.home_root = allowed
    app_config.system_access.path_rules = [{"path": str(allowed)}]
    app_config.system_access.protected_roots = []
    app_config.system_access.backup_root = tmp_path / "backups"
    app_config.system_access.audit_log_path = tmp_path / "logs" / "system_actions.jsonl"
    app_config.ensure_runtime_dirs()

    manager = SystemAccessManager(app_config)
    await manager.initialize()

    result = await manager.run_host_command(
        command=f"Get-ChildItem '{blocked}'",
        session_key="main",
        session_id="sess-6",
        user_id="default",
    )

    assert result["status"] == "blocked:outside_policy"
    assert result["exit_code"] == 1
