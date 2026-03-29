"""Host-scoped file tools with approval-gated writes."""

from __future__ import annotations

from typing import Any

from assistant.tools.registry import ToolDefinition


def build_host_file_tools(system_access_manager) -> list[ToolDefinition]:
    async def read_host_file(payload: dict[str, Any]) -> dict[str, Any]:
        return await system_access_manager.read_host_file(
            path=str(payload["path"]),
            session_id=str(payload.get("session_id", "host-session")),
            user_id=str(payload.get("user_id", "default")),
        )

    async def read_host_document(payload: dict[str, Any]) -> dict[str, Any]:
        return await system_access_manager.read_host_document(
            path=str(payload["path"]),
            session_id=str(payload.get("session_id", "host-session")),
            user_id=str(payload.get("user_id", "default")),
        )

    async def write_host_file(payload: dict[str, Any]) -> dict[str, Any]:
        return await system_access_manager.write_host_file(
            path=str(payload["path"]),
            content=str(payload["content"]),
            session_key=str(payload.get("session_key", "main")),
            session_id=str(payload.get("session_id", "host-session")),
            user_id=str(payload.get("user_id", "default")),
            connection_id=str(payload.get("connection_id", "")),
            channel_name=str(payload.get("channel_name", "")),
        )

    async def delete_host_file(payload: dict[str, Any]) -> dict[str, Any]:
        return await system_access_manager.delete_host_file(
            path=str(payload["path"]),
            session_key=str(payload.get("session_key", "main")),
            session_id=str(payload.get("session_id", "host-session")),
            user_id=str(payload.get("user_id", "default")),
            connection_id=str(payload.get("connection_id", "")),
            channel_name=str(payload.get("channel_name", "")),
        )

    async def copy_host_file(payload: dict[str, Any]) -> dict[str, Any]:
        return await system_access_manager.copy_host_file(
            source=str(payload["source"]),
            destination=str(payload["destination"]),
            session_key=str(payload.get("session_key", "main")),
            session_id=str(payload.get("session_id", "host-session")),
            user_id=str(payload.get("user_id", "default")),
            connection_id=str(payload.get("connection_id", "")),
            channel_name=str(payload.get("channel_name", "")),
        )

    async def move_host_file(payload: dict[str, Any]) -> dict[str, Any]:
        return await system_access_manager.move_host_file(
            source=str(payload["source"]),
            destination=str(payload["destination"]),
            session_key=str(payload.get("session_key", "main")),
            session_id=str(payload.get("session_id", "host-session")),
            user_id=str(payload.get("user_id", "default")),
            connection_id=str(payload.get("connection_id", "")),
            channel_name=str(payload.get("channel_name", "")),
        )

    async def list_host_dir(payload: dict[str, Any]) -> dict[str, Any]:
        return await system_access_manager.list_host_dir(
            path=str(payload.get("path", "~")),
            session_id=str(payload.get("session_id", "host-session")),
            user_id=str(payload.get("user_id", "default")),
            limit=int(payload.get("limit", 200)),
        )

    async def search_host_files(payload: dict[str, Any]) -> dict[str, Any]:
        return await system_access_manager.search_host_files(
            root=str(payload.get("root", "@allowed")),
            pattern=str(payload.get("pattern", "*")),
            text=str(payload.get("text", "")),
            name_query=str(payload.get("name_query", "")),
            directories_only=bool(payload.get("directories_only", False)),
            files_only=bool(payload.get("files_only", False)),
            limit=int(payload.get("limit", 50)),
            session_id=str(payload.get("session_id", "host-session")),
            user_id=str(payload.get("user_id", "default")),
        )

    def _redact(tool_name: str):
        def redactor(tool_input: dict[str, Any], tool_result: dict[str, Any]) -> dict[str, Any]:
            return system_access_manager.redact_tool_result(tool_name, tool_input, tool_result)

        return redactor

    return [
        ToolDefinition(
            name="read_host_file",
            description="Read a UTF-8 text file from configured allowed host paths on the machine.",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            handler=read_host_file,
            persistence_policy="redacted",
            redactor=_redact("read_host_file"),
        ),
        ToolDefinition(
            name="read_host_document",
            description="Read and extract text from a host document such as .txt, .md, .pdf, .docx, or .pptx.",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            handler=read_host_document,
            persistence_policy="redacted",
            redactor=_redact("read_host_document"),
        ),
        ToolDefinition(
            name="write_host_file",
            description="Write a file inside configured allowed host paths on the machine. Text and code files are written as UTF-8 text, and .pdf/.doc/.docx are generated from the provided content.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
            handler=write_host_file,
            persistence_policy="redacted",
            redactor=_redact("write_host_file"),
        ),
        ToolDefinition(
            name="delete_host_file",
            description="Delete a file or directory inside configured allowed host paths on the machine.",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            handler=delete_host_file,
            persistence_policy="redacted",
            redactor=_redact("delete_host_file"),
        ),
        ToolDefinition(
            name="copy_host_file",
            description="Copy a file or directory within configured allowed host paths on the machine.",
            parameters={
                "type": "object",
                "properties": {"source": {"type": "string"}, "destination": {"type": "string"}},
                "required": ["source", "destination"],
            },
            handler=copy_host_file,
            persistence_policy="redacted",
            redactor=_redact("copy_host_file"),
        ),
        ToolDefinition(
            name="move_host_file",
            description="Move or rename a file or directory within configured allowed host paths on the machine.",
            parameters={
                "type": "object",
                "properties": {"source": {"type": "string"}, "destination": {"type": "string"}},
                "required": ["source", "destination"],
            },
            handler=move_host_file,
            persistence_policy="redacted",
            redactor=_redact("move_host_file"),
        ),
        ToolDefinition(
            name="list_host_dir",
            description="List files and directories within configured allowed host paths on the machine.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string", "default": "~"}, "limit": {"type": "integer", "default": 200}},
            },
            handler=list_host_dir,
            persistence_policy="redacted",
            redactor=_redact("list_host_dir"),
        ),
        ToolDefinition(
            name="search_host_files",
            description="Search for files inside allowed host roots using Python traversal instead of raw shell.",
            parameters={
                "type": "object",
                "properties": {
                    "root": {"type": "string", "default": "@allowed"},
                    "pattern": {"type": "string", "default": "*"},
                    "text": {"type": "string", "default": ""},
                    "name_query": {"type": "string", "default": ""},
                    "directories_only": {"type": "boolean", "default": False},
                    "files_only": {"type": "boolean", "default": False},
                    "limit": {"type": "integer", "default": 50},
                },
            },
            handler=search_host_files,
            persistence_policy="redacted",
            redactor=_redact("search_host_files"),
        ),
    ]
