"""Tool surface for Phase 5 app-specific skills."""

from __future__ import annotations

from typing import Any

from assistant.app_skills import AppSkillsManager
from assistant.tools.registry import ToolDefinition


def build_app_skills_tools(config, tool_registry, system_access_manager=None) -> tuple[list[ToolDefinition], AppSkillsManager]:
    manager = AppSkillsManager(config, tool_registry, system_access_manager=system_access_manager)

    def _redact_textual_input(payload: dict[str, Any]) -> dict[str, Any]:
        redacted = {key: value for key, value in payload.items() if key not in {"content", "find_text", "replace_text", "values"}}
        if "content" in payload:
            redacted["content_chars"] = len(str(payload["content"]))
        if "find_text" in payload:
            redacted["find_chars"] = len(str(payload["find_text"]))
        if "replace_text" in payload:
            redacted["replace_chars"] = len(str(payload["replace_text"]))
        if "values" in payload:
            redacted["value_count"] = len(payload.get("values", []))
        return redacted

    async def vscode_open_target(payload: dict[str, Any]) -> dict[str, Any]:
        return await manager.vscode.open_target(str(payload["target"]), prefer=str(payload.get("prefer", "either")))

    async def vscode_search(payload: dict[str, Any]) -> dict[str, Any]:
        return await manager.vscode.search(
            str(payload["query"]),
            prefer=str(payload.get("prefer", "either")),
            limit=int(payload.get("limit", 12)),
            session_id=str(payload.get("session_id", "app-skills")),
            user_id=str(payload.get("user_id", "default")),
        )

    async def document_create(payload: dict[str, Any]) -> dict[str, Any]:
        return await manager.documents.create_document(
            path_hint=str(payload["path"]),
            content=str(payload.get("content", "")),
            session_key=str(payload.get("session_key", "main")),
            session_id=str(payload.get("session_id", "app-skills")),
            user_id=str(payload.get("user_id", "default")),
            connection_id=str(payload.get("connection_id", "")),
            channel_name=str(payload.get("channel_name", "")),
        )

    async def document_read(payload: dict[str, Any]) -> dict[str, Any]:
        return await manager.documents.read_document(
            path_hint=str(payload["path"]),
            session_id=str(payload.get("session_id", "app-skills")),
            user_id=str(payload.get("user_id", "default")),
        )

    async def document_replace_text(payload: dict[str, Any]) -> dict[str, Any]:
        return await manager.documents.replace_text(
            path_hint=str(payload["path"]),
            find_text=str(payload["find_text"]),
            replace_text=str(payload["replace_text"]),
            session_key=str(payload.get("session_key", "main")),
            session_id=str(payload.get("session_id", "app-skills")),
            user_id=str(payload.get("user_id", "default")),
            connection_id=str(payload.get("connection_id", "")),
            channel_name=str(payload.get("channel_name", "")),
        )

    async def excel_create_workbook(payload: dict[str, Any]) -> dict[str, Any]:
        return await manager.excel.create_workbook(
            path_hint=str(payload["path"]),
            headers=[str(item) for item in payload.get("headers", [])],
            sheet_name=str(payload.get("sheet_name", "Sheet1")),
            session_key=str(payload.get("session_key", "main")),
            session_id=str(payload.get("session_id", "app-skills")),
            user_id=str(payload.get("user_id", "default")),
            connection_id=str(payload.get("connection_id", "")),
            channel_name=str(payload.get("channel_name", "")),
        )

    async def excel_append_row(payload: dict[str, Any]) -> dict[str, Any]:
        return await manager.excel.append_row(
            path_hint=str(payload["path"]),
            values=[str(item) for item in payload.get("values", [])],
            session_key=str(payload.get("session_key", "main")),
            session_id=str(payload.get("session_id", "app-skills")),
            user_id=str(payload.get("user_id", "default")),
            connection_id=str(payload.get("connection_id", "")),
            channel_name=str(payload.get("channel_name", "")),
        )

    async def excel_preview(payload: dict[str, Any]) -> dict[str, Any]:
        return await manager.excel.preview(
            path_hint=str(payload["path"]),
            session_id=str(payload.get("session_id", "app-skills")),
            user_id=str(payload.get("user_id", "default")),
            limit=int(payload.get("limit", 8)),
        )

    async def browser_workspace_open(payload: dict[str, Any]) -> dict[str, Any]:
        return await manager.open_browser_workspace(str(payload["workspace"]), user_id=str(payload.get("user_id", "default")))

    async def system_open_settings(payload: dict[str, Any]) -> dict[str, Any]:
        return manager.system.open_settings(str(payload["page"]))

    async def system_volume_status(_payload: dict[str, Any]) -> dict[str, Any]:
        return manager.system.volume_status()

    async def system_volume_set(payload: dict[str, Any]) -> dict[str, Any]:
        return await manager.set_volume(
            percent=int(payload["percent"]),
            session_key=str(payload.get("session_key", "main")),
            session_id=str(payload.get("session_id", "app-skills")),
            user_id=str(payload.get("user_id", "default")),
            connection_id=str(payload.get("connection_id", "")),
            channel_name=str(payload.get("channel_name", "")),
        )

    async def system_brightness_status(_payload: dict[str, Any]) -> dict[str, Any]:
        return manager.system.brightness_status()

    async def system_brightness_set(payload: dict[str, Any]) -> dict[str, Any]:
        return await manager.set_brightness(
            percent=int(payload["percent"]),
            session_key=str(payload.get("session_key", "main")),
            session_id=str(payload.get("session_id", "app-skills")),
            user_id=str(payload.get("user_id", "default")),
            connection_id=str(payload.get("connection_id", "")),
            channel_name=str(payload.get("channel_name", "")),
        )

    async def system_bluetooth_status(_payload: dict[str, Any]) -> dict[str, Any]:
        return manager.system.bluetooth_status()

    async def system_snapshot(_payload: dict[str, Any]) -> dict[str, Any]:
        return manager.system.system_snapshot()

    async def task_manager_open(_payload: dict[str, Any]) -> dict[str, Any]:
        return await manager.task_manager.open_task_manager()

    async def task_manager_summary(_payload: dict[str, Any]) -> dict[str, Any]:
        return manager.task_manager.summary()

    async def preset_list(_payload: dict[str, Any]) -> dict[str, Any]:
        return {"presets": manager.presets.list_presets()}

    async def preset_run(payload: dict[str, Any]) -> dict[str, Any]:
        return await manager.presets.run_preset(str(payload["name"]), user_id=str(payload.get("user_id", "default")))

    tools = [
        ToolDefinition(
            name="vscode_open_target",
            description="Open a file or project folder in VS Code using allowed host paths and the configured VS Code app alias.",
            parameters={"type": "object", "properties": {"target": {"type": "string"}, "prefer": {"type": "string"}}, "required": ["target"]},
            handler=vscode_open_target,
        ),
        ToolDefinition(
            name="vscode_search",
            description="Search allowed host paths for a file or folder to open in VS Code.",
            parameters={"type": "object", "properties": {"query": {"type": "string"}, "prefer": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]},
            handler=vscode_search,
        ),
        ToolDefinition(
            name="document_create",
            description="Create or overwrite a text, markdown, or docx document in allowed host paths.",
            parameters={"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path"]},
            handler=document_create,
            persistence_policy="redacted",
            redactor=lambda _tool_input, tool_result: {
                "path": tool_result.get("path"),
                "status": tool_result.get("status"),
                "approval_category": tool_result.get("approval_category"),
                "approval_mode": tool_result.get("approval_mode"),
            },
            input_redactor=_redact_textual_input,
        ),
        ToolDefinition(
            name="document_read",
            description="Read a text, markdown, or docx document from allowed host paths.",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            handler=document_read,
            persistence_policy="redacted",
            redactor=lambda _tool_input, tool_result: {
                "path": tool_result.get("path"),
                "bytes_read": tool_result.get("bytes_read"),
                "line_count": tool_result.get("line_count"),
            },
        ),
        ToolDefinition(
            name="document_replace_text",
            description="Replace plain text in a text, markdown, or docx document and write the updated result back through the host approval flow.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "find_text": {"type": "string"},
                    "replace_text": {"type": "string"},
                },
                "required": ["path", "find_text", "replace_text"],
            },
            handler=document_replace_text,
            persistence_policy="redacted",
            redactor=lambda _tool_input, tool_result: {
                "path": tool_result.get("path"),
                "status": tool_result.get("status"),
                "replacements": tool_result.get("replacements"),
                "approval_category": tool_result.get("approval_category"),
                "approval_mode": tool_result.get("approval_mode"),
            },
            input_redactor=_redact_textual_input,
        ),
        ToolDefinition(
            name="excel_create_workbook",
            description="Create a simple XLSX workbook in an allowed host path.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "headers": {"type": "array", "items": {"type": "string"}},
                    "sheet_name": {"type": "string"},
                },
                "required": ["path"],
            },
            handler=excel_create_workbook,
            persistence_policy="redacted",
            redactor=lambda _tool_input, tool_result: {
                "path": tool_result.get("path"),
                "status": tool_result.get("status"),
                "sheet_name": tool_result.get("sheet_name"),
                "approval_category": tool_result.get("approval_category"),
                "approval_mode": tool_result.get("approval_mode"),
            },
            input_redactor=_redact_textual_input,
        ),
        ToolDefinition(
            name="excel_append_row",
            description="Append a row of values to a simple XLSX workbook in an allowed host path.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}, "values": {"type": "array", "items": {"type": "string"}}},
                "required": ["path", "values"],
            },
            handler=excel_append_row,
            persistence_policy="redacted",
            redactor=lambda _tool_input, tool_result: {
                "path": tool_result.get("path"),
                "status": tool_result.get("status"),
                "approval_category": tool_result.get("approval_category"),
                "approval_mode": tool_result.get("approval_mode"),
                "row_count": tool_result.get("preview", {}).get("row_count") if isinstance(tool_result.get("preview"), dict) else None,
            },
            input_redactor=_redact_textual_input,
        ),
        ToolDefinition(
            name="excel_preview",
            description="Preview the first rows of an XLSX workbook from an allowed host path.",
            parameters={"type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["path"]},
            handler=excel_preview,
        ),
        ToolDefinition(
            name="browser_workspace_open",
            description="Open a configured study, work, or meeting browser workspace using existing browser tools through a separate app-skill adapter.",
            parameters={"type": "object", "properties": {"workspace": {"type": "string"}}, "required": ["workspace"]},
            handler=browser_workspace_open,
        ),
        ToolDefinition(
            name="system_open_settings",
            description="Open a Windows Settings page such as sound, display, bluetooth, or network.",
            parameters={"type": "object", "properties": {"page": {"type": "string"}}, "required": ["page"]},
            handler=system_open_settings,
        ),
        ToolDefinition(
            name="system_volume_status",
            description="Read the current Windows system volume level.",
            parameters={"type": "object", "properties": {}},
            handler=system_volume_status,
        ),
        ToolDefinition(
            name="system_volume_set",
            description="Set the current Windows system volume level.",
            parameters={"type": "object", "properties": {"percent": {"type": "integer"}}, "required": ["percent"]},
            handler=system_volume_set,
        ),
        ToolDefinition(
            name="system_brightness_status",
            description="Read the current display brightness level when supported by the device.",
            parameters={"type": "object", "properties": {}},
            handler=system_brightness_status,
        ),
        ToolDefinition(
            name="system_brightness_set",
            description="Set the display brightness level when supported by the device.",
            parameters={"type": "object", "properties": {"percent": {"type": "integer"}}, "required": ["percent"]},
            handler=system_brightness_set,
        ),
        ToolDefinition(
            name="system_bluetooth_status",
            description="Return a simple Bluetooth availability summary.",
            parameters={"type": "object", "properties": {}},
            handler=system_bluetooth_status,
        ),
        ToolDefinition(
            name="system_snapshot",
            description="Return a brief Windows system summary including CPU, memory, disk, bluetooth, and volume.",
            parameters={"type": "object", "properties": {}},
            handler=system_snapshot,
        ),
        ToolDefinition(
            name="task_manager_open",
            description="Open Windows Task Manager and return a brief utilization summary.",
            parameters={"type": "object", "properties": {}},
            handler=task_manager_open,
        ),
        ToolDefinition(
            name="task_manager_summary",
            description="Return a brief system utilization summary similar to Task Manager.",
            parameters={"type": "object", "properties": {}},
            handler=task_manager_summary,
        ),
        ToolDefinition(
            name="preset_list",
            description="List the built-in study, work, and meeting app-skill presets.",
            parameters={"type": "object", "properties": {}},
            handler=preset_list,
        ),
        ToolDefinition(
            name="preset_run",
            description="Run a built-in preset such as study-mode, work-mode, or meeting-mode.",
            parameters={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
            handler=preset_run,
        ),
    ]
    return tools, manager
