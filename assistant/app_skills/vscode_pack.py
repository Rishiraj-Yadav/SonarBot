"""VS Code and project-oriented skill pack."""

from __future__ import annotations

from pathlib import Path
from typing import Any


class VSCodeSkillPack:
    def __init__(self, config, tool_registry, system_access_manager, *, resolve_target_path) -> None:
        self.config = config
        self.tool_registry = tool_registry
        self.system_access_manager = system_access_manager
        self.resolve_target_path = resolve_target_path

    async def open_target(self, target_hint: str, *, prefer: str = "either") -> dict[str, Any]:
        self._ensure_enabled()
        if not self.tool_registry.has("apps_open"):
            raise RuntimeError("Desktop app control is not enabled.")
        resolved_path = await self.resolve_target_path(target_hint, prefer=prefer)
        result = await self.tool_registry.dispatch("apps_open", {"target": "vscode", "args": [resolved_path]})
        return {"path": resolved_path, "target_type": "directory" if Path(resolved_path).is_dir() else "file", **result}

    async def search(self, query: str, *, prefer: str = "either", limit: int = 12, session_id: str, user_id: str) -> dict[str, Any]:
        self._ensure_enabled()
        directories_only = prefer == "directory"
        files_only = prefer == "file"
        result = await self.tool_registry.dispatch(
            "search_host_files",
            {
                "root": "@allowed",
                "name_query": query,
                "directories_only": directories_only,
                "files_only": files_only,
                "limit": limit,
                "session_id": session_id,
                "user_id": user_id,
            },
        )
        return result

    def _ensure_enabled(self) -> None:
        if not bool(getattr(self.config.app_skills, "enabled", False)):
            raise RuntimeError("App skills are not enabled.")
        if not bool(getattr(self.config.app_skills, "vscode_enabled", True)):
            raise RuntimeError("VS Code skill pack is disabled.")
        if self.system_access_manager is None or not bool(getattr(self.config.system_access, "enabled", False)):
            raise RuntimeError("VS Code skill pack requires system access to be enabled.")
