"""Coordinator for Phase 5 app-specific skill packs."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from assistant.app_skills.browser_pack import BrowserSkillPack
from assistant.app_skills.document_pack import DocumentSkillPack
from assistant.app_skills.excel_pack import ExcelSkillPack
from assistant.app_skills.preset_pack import PresetSkillPack
from assistant.app_skills.system_control_pack import SystemControlPack
from assistant.app_skills.task_manager_pack import TaskManagerSkillPack
from assistant.app_skills.vscode_pack import VSCodeSkillPack


class AppSkillsManager:
    def __init__(self, config, tool_registry, system_access_manager=None) -> None:
        self.config = config
        self.tool_registry = tool_registry
        self.system_access_manager = system_access_manager
        self.browser = BrowserSkillPack(config, tool_registry)
        self.system = SystemControlPack(config, system_access_manager=system_access_manager)
        self.vscode = VSCodeSkillPack(
            config,
            tool_registry,
            system_access_manager,
            resolve_target_path=self.resolve_target_path,
        )
        self.documents = DocumentSkillPack(
            config,
            tool_registry,
            system_access_manager,
            resolve_target_path=self.resolve_target_path,
        )
        self.excel = ExcelSkillPack(
            config,
            system_access_manager,
            resolve_target_path=self.resolve_target_path,
        )
        self.task_manager = TaskManagerSkillPack(config, tool_registry, self.system)
        self.presets = PresetSkillPack(
            config,
            tool_registry,
            resolve_target_path=self.resolve_target_path,
            browser_pack=self.browser,
        )

    def ensure_enabled(self) -> None:
        if not bool(getattr(self.config.app_skills, "enabled", False)):
            raise RuntimeError("App skills are not enabled.")

    async def resolve_target_path(self, target_hint: str, *, prefer: str = "either") -> str:
        self.ensure_enabled()
        if self.system_access_manager is None or not bool(getattr(self.config.system_access, "enabled", False)):
            raise RuntimeError("Resolving host paths requires system access to be enabled.")
        hint = str(target_hint).strip().strip("\"'")
        if not hint:
            raise RuntimeError("Please provide a file or folder target.")
        if self._looks_like_explicit_path(hint):
            resolved = self.system_access_manager.runtime.resolve_host_path(hint)
            if prefer == "directory" and resolved.exists() and not resolved.is_dir():
                raise RuntimeError(f"'{hint}' is a file, not a folder.")
            if prefer == "file" and resolved.exists() and resolved.is_dir():
                raise RuntimeError(f"'{hint}' is a folder, not a file.")
            action = "read" if resolved.exists() or prefer == "directory" else "write"
            category, reason = self.system_access_manager.runtime.classify_path_action(resolved if action == "read" else resolved.parent, action)
            if category == "deny":
                raise RuntimeError(f"That path is outside the allowed host access policy ({reason}).")
            return str(resolved)

        directories_only = prefer == "directory"
        files_only = prefer == "file"
        result = await self.tool_registry.dispatch(
            "search_host_files",
            {
                "root": "@allowed",
                "name_query": hint,
                "directories_only": directories_only,
                "files_only": files_only,
                "limit": 12,
                "session_id": "app-skills",
                "user_id": self.config.users.default_user_id,
            },
        )
        matches = [item for item in result.get("matches", []) if isinstance(item, dict)]
        if not matches:
            raise RuntimeError(f"I couldn't find a matching {'folder' if directories_only else 'file'} for '{hint}'.")
        best = matches[0]
        return str(best.get("path", ""))

    async def open_browser_workspace(self, workspace_name: str, *, user_id: str) -> dict[str, Any]:
        self.ensure_enabled()
        return await self.browser.open_workspace(workspace_name, user_id=user_id)

    async def set_volume(
        self,
        *,
        percent: int,
        session_key: str,
        session_id: str,
        user_id: str,
        connection_id: str = "",
        channel_name: str = "",
    ) -> dict[str, Any]:
        self.ensure_enabled()
        if self.system_access_manager is None:
            result = self.system.set_volume(percent)
            return {**result, "approval_category": "auto_allow", "approval_mode": "auto"}
        return await self.system_access_manager.execute_desktop_input_action(
            tool="system_volume_set",
            action_kind="system_volume_set",
            target_summary=f"Set system volume to {percent}%",
            approval_category="always_ask",
            session_key=session_key,
            session_id=session_id,
            user_id=user_id,
            connection_id=connection_id,
            channel_name=channel_name,
            approval_payload={"volume_percent": int(percent)},
            audit_details={"volume_percent": int(percent)},
            executor=lambda: self._sync_result(self.system.set_volume(int(percent))),
        )

    async def set_brightness(
        self,
        *,
        percent: int,
        session_key: str,
        session_id: str,
        user_id: str,
        connection_id: str = "",
        channel_name: str = "",
    ) -> dict[str, Any]:
        self.ensure_enabled()
        if self.system_access_manager is None:
            result = self.system.set_brightness(percent)
            return {**result, "approval_category": "auto_allow", "approval_mode": "auto"}
        return await self.system_access_manager.execute_desktop_input_action(
            tool="system_brightness_set",
            action_kind="system_brightness_set",
            target_summary=f"Set brightness to {percent}%",
            approval_category="always_ask",
            session_key=session_key,
            session_id=session_id,
            user_id=user_id,
            connection_id=connection_id,
            channel_name=channel_name,
            approval_payload={"brightness_percent": int(percent)},
            audit_details={"brightness_percent": int(percent)},
            executor=lambda: self._sync_result(self.system.set_brightness(int(percent))),
        )

    async def set_bluetooth(
        self,
        *,
        mode: str,
        session_key: str,
        session_id: str,
        user_id: str,
        connection_id: str = "",
        channel_name: str = "",
    ) -> dict[str, Any]:
        self.ensure_enabled()
        normalized_mode = str(mode).strip().lower()
        if self.system_access_manager is None:
            result = self.system.set_bluetooth(normalized_mode)
            return {**result, "approval_category": "auto_allow", "approval_mode": "auto"}
        return await self.system_access_manager.execute_desktop_input_action(
            tool="system_bluetooth_set",
            action_kind="system_bluetooth_set",
            target_summary=f"Turn Bluetooth {normalized_mode}",
            approval_category="always_ask",
            session_key=session_key,
            session_id=session_id,
            user_id=user_id,
            connection_id=connection_id,
            channel_name=channel_name,
            approval_payload={"mode": normalized_mode},
            audit_details={"mode": normalized_mode},
            executor=lambda: self._sync_result(self.system.set_bluetooth(normalized_mode)),
        )

    @staticmethod
    async def _sync_result(result: dict[str, Any]) -> dict[str, Any]:
        return result

    def _looks_like_explicit_path(self, value: str) -> bool:
        if value.startswith("~"):
            return True
        if re.match(r"^[A-Za-z]:[\\/]", value):
            return True
        if "\\" in value or "/" in value:
            return True
        suffix = Path(value).suffix.lower()
        return suffix in {".txt", ".md", ".docx", ".doc", ".xlsx", ".csv", ".json", ".py"}
