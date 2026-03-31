"""Structured desktop automation action executor."""

from __future__ import annotations

import fnmatch
from datetime import datetime
from pathlib import Path
from typing import Any


class DesktopAutomationExecutor:
    def __init__(self, system_access_manager) -> None:
        self.system_access_manager = system_access_manager

    async def execute(
        self,
        *,
        rule: dict[str, Any],
        event_payload: dict[str, Any],
        session_key: str,
        session_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        source_path = Path(str(event_payload.get("path", "")))
        action_type = str(rule.get("action_type", "notify"))
        if action_type == "notify":
            return {"status": "completed", "message": self._notify_message(rule, source_path)}
        if action_type == "move":
            destination = self._destination_for_source(rule, source_path)
            result = await self.system_access_manager.move_host_file(
                source=str(source_path),
                destination=str(destination),
                session_key=session_key,
                session_id=session_id,
                user_id=user_id,
            )
            return {"status": result.get("status", "completed"), "message": f"Moved {source_path.name} to {destination.parent}."}
        if action_type == "copy":
            destination = self._destination_for_source(rule, source_path)
            result = await self.system_access_manager.copy_host_file(
                source=str(source_path),
                destination=str(destination),
                session_key=session_key,
                session_id=session_id,
                user_id=user_id,
            )
            return {"status": result.get("status", "completed"), "message": f"Copied {source_path.name} to {destination.parent}."}
        if action_type == "rename":
            destination = self._renamed_destination(rule, source_path)
            result = await self.system_access_manager.move_host_file(
                source=str(source_path),
                destination=str(destination),
                session_key=session_key,
                session_id=session_id,
                user_id=user_id,
            )
            return {"status": result.get("status", "completed"), "message": f"Renamed {source_path.name} to {destination.name}."}
        if action_type == "delete":
            result = await self.system_access_manager.delete_host_file(
                path=str(source_path),
                session_key=session_key,
                session_id=session_id,
                user_id=user_id,
            )
            return {"status": result.get("status", "completed"), "message": f"Deleted {source_path.name}."}
        if action_type == "archive":
            archive_dir = self._archive_destination(rule, source_path)
            result = await self.system_access_manager.move_host_file(
                source=str(source_path),
                destination=str(archive_dir / source_path.name),
                session_key=session_key,
                session_id=session_id,
                user_id=user_id,
            )
            return {"status": result.get("status", "completed"), "message": f"Archived {source_path.name} into {archive_dir}."}
        if action_type == "organize":
            destination = self._organized_destination(source_path)
            result = await self.system_access_manager.move_host_file(
                source=str(source_path),
                destination=str(destination / source_path.name),
                session_key=session_key,
                session_id=session_id,
                user_id=user_id,
            )
            return {"status": result.get("status", "completed"), "message": f"Organized {source_path.name} into {destination.name}."}
        if action_type == "write":
            target_path = self._write_target(rule, source_path)
            content = self._content_for_rule(rule, source_path, event_payload)
            result = await self.system_access_manager.write_host_file(
                path=str(target_path),
                content=content,
                session_key=session_key,
                session_id=session_id,
                user_id=user_id,
            )
            return {"status": result.get("status", "completed"), "message": f"Updated {target_path.name}."}
        raise ValueError(f"Unsupported desktop automation action '{action_type}'.")

    def matches_event(self, rule: dict[str, Any], *, event_type: str, path: str) -> bool:
        event_types = [str(item).lower() for item in rule.get("event_types", [])]
        if event_types and event_type.lower() not in event_types:
            return False
        candidate = Path(path)
        suffix = candidate.suffix.lower().lstrip(".")
        allowed_extensions = [str(item).lower().lstrip(".") for item in rule.get("file_extensions", []) if item]
        if allowed_extensions and suffix not in allowed_extensions:
            return False
        pattern = str(rule.get("filename_pattern", "*") or "*")
        return fnmatch.fnmatch(candidate.name.lower(), pattern.lower())

    def _destination_for_source(self, rule: dict[str, Any], source_path: Path) -> Path:
        base = Path(str(rule.get("destination_path", "")))
        return base / source_path.name

    def _renamed_destination(self, rule: dict[str, Any], source_path: Path) -> Path:
        template = str(rule.get("target_name_template", "")).strip()
        if not template:
            template = f"{source_path.stem}_renamed{source_path.suffix}"
        return source_path.with_name(self._render_template(template, source_path))

    def _archive_destination(self, rule: dict[str, Any], source_path: Path) -> Path:
        base = Path(str(rule.get("destination_path", "") or source_path.parent))
        return base / datetime.now().strftime("%Y-%m-%d")

    def _organized_destination(self, source_path: Path) -> Path:
        suffix = source_path.suffix.lower()
        mapping = {
            ".pdf": "PDFs",
            ".doc": "Docs",
            ".docx": "Docs",
            ".txt": "Text",
            ".jpg": "Images",
            ".jpeg": "Images",
            ".png": "Images",
            ".gif": "Images",
            ".zip": "Archives",
            ".rar": "Archives",
        }
        folder = mapping.get(suffix, "Other Files")
        return source_path.parent / folder

    def _write_target(self, rule: dict[str, Any], source_path: Path) -> Path:
        destination = str(rule.get("destination_path", "")).strip()
        if destination:
            return Path(destination)
        return source_path

    def _content_for_rule(self, rule: dict[str, Any], source_path: Path, event_payload: dict[str, Any]) -> str:
        template = str(rule.get("content_template", "")).strip()
        if template:
            return self._render_template(template, source_path)
        return str(event_payload.get("content", f"Updated by desktop automation for {source_path.name}"))

    def _notify_message(self, rule: dict[str, Any], source_path: Path) -> str:
        return f"Desktop automation noticed {source_path.name} in {source_path.parent}."

    def _render_template(self, template: str, source_path: Path) -> str:
        return (
            template.replace("{name}", source_path.name)
            .replace("{stem}", source_path.stem)
            .replace("{suffix}", source_path.suffix)
            .replace("{parent}", str(source_path.parent))
        )
