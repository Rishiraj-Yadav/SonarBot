"""Excel skill pack backed by minimal XLSX helpers and host approvals."""

from __future__ import annotations

from typing import Any

from assistant.app_skills.simple_xlsx import build_workbook_bytes, preview_workbook_bytes, load_workbook_bytes


class ExcelSkillPack:
    def __init__(self, config, system_access_manager, *, resolve_target_path) -> None:
        self.config = config
        self.system_access_manager = system_access_manager
        self.resolve_target_path = resolve_target_path

    def _ensure_enabled(self) -> None:
        if not bool(getattr(self.config.app_skills, "enabled", False)):
            raise RuntimeError("App skills are not enabled.")
        if not bool(getattr(self.config.app_skills, "excel_enabled", True)):
            raise RuntimeError("Excel skill pack is disabled.")
        if self.system_access_manager is None or not bool(getattr(self.config.system_access, "enabled", False)):
            raise RuntimeError("Excel skill pack requires system access to be enabled.")

    async def create_workbook(
        self,
        *,
        path_hint: str,
        headers: list[str] | None,
        session_key: str,
        session_id: str,
        user_id: str,
        connection_id: str = "",
        channel_name: str = "",
        sheet_name: str = "Sheet1",
    ) -> dict[str, Any]:
        self._ensure_enabled()
        resolved_path = await self.resolve_target_path(path_hint, prefer="file")
        rows: list[list[object]] = [headers] if headers else []
        data = build_workbook_bytes(sheet_name=sheet_name, rows=rows)
        result = await self.system_access_manager.write_host_binary_file(
            path=resolved_path,
            data=data,
            session_key=session_key,
            session_id=session_id,
            user_id=user_id,
            connection_id=connection_id,
            channel_name=channel_name,
            tool_name="excel_create_workbook",
            action_kind="excel_create_workbook",
        )
        preview = preview_workbook_bytes(data, limit=4)
        return {"path": resolved_path, "sheet_name": sheet_name, "preview": preview, **result}

    async def append_row(
        self,
        *,
        path_hint: str,
        values: list[str],
        session_key: str,
        session_id: str,
        user_id: str,
        connection_id: str = "",
        channel_name: str = "",
    ) -> dict[str, Any]:
        self._ensure_enabled()
        resolved_path = await self.resolve_target_path(path_hint, prefer="file")
        current = await self.system_access_manager.read_host_binary_file(path=resolved_path, session_id=session_id, user_id=user_id)
        workbook = load_workbook_bytes(bytes(current.get("content_bytes", b"")))
        workbook.rows.append([str(item) for item in values])
        data = build_workbook_bytes(sheet_name=workbook.sheet_name, rows=workbook.rows)
        result = await self.system_access_manager.write_host_binary_file(
            path=resolved_path,
            data=data,
            session_key=session_key,
            session_id=session_id,
            user_id=user_id,
            connection_id=connection_id,
            channel_name=channel_name,
            tool_name="excel_append_row",
            action_kind="excel_append_row",
        )
        preview = preview_workbook_bytes(data, limit=6)
        return {"path": resolved_path, "preview": preview, "appended_values": list(values), **result}

    async def preview(
        self,
        *,
        path_hint: str,
        session_id: str,
        user_id: str,
        limit: int = 8,
    ) -> dict[str, Any]:
        self._ensure_enabled()
        resolved_path = await self.resolve_target_path(path_hint, prefer="file")
        current = await self.system_access_manager.read_host_binary_file(path=resolved_path, session_id=session_id, user_id=user_id)
        preview = preview_workbook_bytes(bytes(current.get("content_bytes", b"")), limit=limit)
        return {"path": resolved_path, **preview, "bytes_read": current.get("bytes_read", 0)}
