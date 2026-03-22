"""Workspace-scoped file tools."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from assistant.tools.registry import ToolDefinition


def _resolve_workspace_path(workspace_dir: Path, raw_path: str) -> Path:
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = workspace_dir / candidate
    candidate = candidate.expanduser().resolve()
    workspace_root = workspace_dir.expanduser().resolve()
    candidate.relative_to(workspace_root)
    return candidate


def build_file_tools(workspace_dir: Path) -> list[ToolDefinition]:
    async def read_file(payload: dict[str, Any]) -> dict[str, Any]:
        path = _resolve_workspace_path(workspace_dir, str(payload["path"]))
        content = await asyncio.to_thread(path.read_text, encoding="utf-8")
        return {"path": str(path), "content": content}

    async def write_file(payload: dict[str, Any]) -> dict[str, Any]:
        path = _resolve_workspace_path(workspace_dir, str(payload["path"]))
        content = str(payload["content"])

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        await asyncio.to_thread(_write)
        return {"path": str(path), "bytes_written": len(content.encode("utf-8"))}

    return [
        ToolDefinition(
            name="read_file",
            description="Read a UTF-8 text file from the workspace.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            handler=read_file,
        ),
        ToolDefinition(
            name="write_file",
            description="Write a UTF-8 text file inside the workspace.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
            handler=write_file,
        ),
    ]
