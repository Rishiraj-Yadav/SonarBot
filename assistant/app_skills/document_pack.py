"""Document skill pack built on existing host file tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any


class DocumentSkillPack:
    def __init__(self, config, tool_registry, system_access_manager, *, resolve_target_path) -> None:
        self.config = config
        self.tool_registry = tool_registry
        self.system_access_manager = system_access_manager
        self.resolve_target_path = resolve_target_path

    async def create_document(
        self,
        *,
        path_hint: str,
        content: str,
        session_key: str,
        session_id: str,
        user_id: str,
        connection_id: str = "",
        channel_name: str = "",
    ) -> dict[str, Any]:
        self._ensure_enabled()
        resolved_path = await self.resolve_target_path(path_hint, prefer="file")
        payload = {
            "path": resolved_path,
            "content": content,
            "session_key": session_key,
            "session_id": session_id,
            "user_id": user_id,
            "connection_id": connection_id,
            "channel_name": channel_name,
        }
        result = await self.tool_registry.dispatch("write_host_file", payload)
        return {"path": resolved_path, **result}

    async def read_document(
        self,
        *,
        path_hint: str,
        session_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        self._ensure_enabled()
        resolved_path = await self.resolve_target_path(path_hint, prefer="file")
        result = await self.tool_registry.dispatch(
            "read_host_file",
            {"path": resolved_path, "session_id": session_id, "user_id": user_id},
        )
        return {"path": resolved_path, **result}

    async def replace_text(
        self,
        *,
        path_hint: str,
        find_text: str,
        replace_text: str,
        session_key: str,
        session_id: str,
        user_id: str,
        connection_id: str = "",
        channel_name: str = "",
    ) -> dict[str, Any]:
        self._ensure_enabled()
        if not find_text:
            raise RuntimeError("Find text cannot be empty.")
        read_result = await self.read_document(path_hint=path_hint, session_id=session_id, user_id=user_id)
        current_content = str(read_result.get("content", ""))
        replacements = current_content.count(find_text)
        if replacements <= 0:
            return {
                "path": read_result["path"],
                "status": "no_change",
                "replacements": 0,
                "content": current_content,
            }
        updated_content = current_content.replace(find_text, replace_text)
        write_result = await self.tool_registry.dispatch(
            "write_host_file",
            {
                "path": read_result["path"],
                "content": updated_content,
                "session_key": session_key,
                "session_id": session_id,
                "user_id": user_id,
                "connection_id": connection_id,
                "channel_name": channel_name,
            },
        )
        return {
            "path": read_result["path"],
            "status": str(write_result.get("status", "completed")),
            "replacements": replacements,
            "file_format": Path(str(read_result["path"])).suffix.lower().lstrip(".") or "text",
            "approval_category": write_result.get("approval_category"),
            "approval_mode": write_result.get("approval_mode"),
        }

    def _ensure_enabled(self) -> None:
        if not bool(getattr(self.config.app_skills, "enabled", False)):
            raise RuntimeError("App skills are not enabled.")
        if not bool(getattr(self.config.app_skills, "documents_enabled", True)):
            raise RuntimeError("Document skill pack is disabled.")
        if self.system_access_manager is None or not bool(getattr(self.config.system_access, "enabled", False)):
            raise RuntimeError("Document skill pack requires system access to be enabled.")
