from __future__ import annotations

import asyncio
from pathlib import Path
import zipfile

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
async def test_read_host_document_extracts_pptx_text(app_config, tmp_path: Path) -> None:
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

    pptx_path = home_root / "Protein_Introduction_ 1 Why_It_Matters.pptx"
    with zipfile.ZipFile(pptx_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
  <Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>
  <Override PartName="/ppt/slides/slide2.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>
</Types>
""",
        )
        archive.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>
</Relationships>
""",
        )
        archive.writestr(
            "ppt/presentation.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
                xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <p:sldIdLst>
    <p:sldId id="256" r:id="rId1"/>
    <p:sldId id="257" r:id="rId2"/>
  </p:sldIdLst>
</p:presentation>
""",
        )
        archive.writestr(
            "ppt/_rels/presentation.xml.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide2.xml"/>
</Relationships>
""",
        )
        archive.writestr(
            "ppt/slides/slide1.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
       xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld>
    <p:spTree>
      <p:sp><p:txBody><a:p><a:r><a:t>Protein basics</a:t></a:r></a:p></p:txBody></p:sp>
    </p:spTree>
  </p:cSld>
</p:sld>
""",
        )
        archive.writestr(
            "ppt/slides/slide2.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
       xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld>
    <p:spTree>
      <p:sp><p:txBody><a:p><a:r><a:t>Why it matters</a:t></a:r></a:p></p:txBody></p:sp>
    </p:spTree>
  </p:cSld>
</p:sld>
""",
        )

    manager = SystemAccessManager(app_config)
    await manager.initialize()
    read_tool = next(tool for tool in build_host_file_tools(manager) if tool.name == "read_host_document")

    result = await read_tool.handler({"path": str(pptx_path), "session_id": "sess-doc", "user_id": "default"})
    persisted = read_tool.redactor({"path": str(pptx_path)}, result) if read_tool.redactor else result

    assert "Protein basics" in result["content"]
    assert "Why it matters" in result["content"]
    assert result["file_format"] == "pptx"
    assert "content" not in persisted


@pytest.mark.asyncio
async def test_resolve_host_path_prefers_onedrive_desktop_when_redirected(app_config, tmp_path: Path) -> None:
    home_root = tmp_path / "home"
    desktop_root = home_root / "OneDrive" / "Desktop"
    desktop_root.mkdir(parents=True, exist_ok=True)
    (desktop_root / "notes.txt").write_text("hello", encoding="utf-8")

    app_config.system_access.enabled = True
    app_config.system_access.home_root = home_root
    app_config.system_access.protected_roots = []
    app_config.system_access.backup_root = tmp_path / "backups"
    app_config.system_access.audit_log_path = tmp_path / "logs" / "system_actions.jsonl"
    app_config.ensure_runtime_dirs()

    manager = SystemAccessManager(app_config)
    await manager.initialize()

    resolved = manager.runtime.resolve_host_path("~/Desktop")
    assert resolved == desktop_root

    listing = await manager.list_host_dir(path="~/Desktop", session_id="sess-desktop", user_id="default")
    assert listing["path"] == str(desktop_root)
    assert [item["name"] for item in listing["entries"]] == ["notes.txt"]


@pytest.mark.asyncio
async def test_resolve_host_path_prefers_onedrive_downloads_and_documents_when_redirected(app_config, tmp_path: Path) -> None:
    home_root = tmp_path / "home"
    documents_root = home_root / "OneDrive" / "Documents"
    downloads_root = home_root / "OneDrive" / "Downloads"
    documents_root.mkdir(parents=True, exist_ok=True)
    downloads_root.mkdir(parents=True, exist_ok=True)
    (documents_root / "doc.txt").write_text("doc", encoding="utf-8")
    (downloads_root / "download.txt").write_text("download", encoding="utf-8")

    app_config.system_access.enabled = True
    app_config.system_access.home_root = home_root
    app_config.system_access.protected_roots = []
    app_config.system_access.backup_root = tmp_path / "backups"
    app_config.system_access.audit_log_path = tmp_path / "logs" / "system_actions.jsonl"
    app_config.ensure_runtime_dirs()

    manager = SystemAccessManager(app_config)
    await manager.initialize()

    assert manager.runtime.resolve_host_path("~/Documents") == documents_root
    assert manager.runtime.resolve_host_path("~/Downloads") == downloads_root

    documents_listing = await manager.list_host_dir(path="~/Documents", session_id="sess-documents", user_id="default")
    downloads_listing = await manager.list_host_dir(path="~/Downloads", session_id="sess-downloads", user_id="default")

    assert documents_listing["path"] == str(documents_root)
    assert downloads_listing["path"] == str(downloads_root)
    assert [item["name"] for item in documents_listing["entries"]] == ["doc.txt"]
    assert [item["name"] for item in downloads_listing["entries"]] == ["download.txt"]


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
