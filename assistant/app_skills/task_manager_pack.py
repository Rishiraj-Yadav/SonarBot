"""Task Manager app skill pack."""

from __future__ import annotations

import json
import subprocess
from typing import Any


class TaskManagerSkillPack:
    def __init__(self, config, tool_registry, system_control_pack) -> None:
        self.config = config
        self.tool_registry = tool_registry
        self.system_control_pack = system_control_pack

    def _ensure_enabled(self) -> None:
        if not bool(getattr(self.config.app_skills, "enabled", False)):
            raise RuntimeError("App skills are not enabled.")
        if not bool(getattr(self.config.app_skills, "task_manager_enabled", True)):
            raise RuntimeError("Task Manager skill pack is disabled.")
        if not self.tool_registry.has("apps_open"):
            raise RuntimeError("Desktop app control is not enabled.")

    async def open_task_manager(self) -> dict[str, Any]:
        self._ensure_enabled()
        open_result = await self.tool_registry.dispatch("apps_open", {"target": "taskmanager"})
        summary = self.summary()
        return {"open_result": open_result, "summary": summary, "status": "completed"}

    def summary(self) -> dict[str, Any]:
        self._ensure_enabled()
        snapshot = self.system_control_pack.system_snapshot()
        snapshot["top_processes"] = self._top_processes()
        return snapshot

    def _top_processes(self) -> list[dict[str, Any]]:
        command = (
            "Get-Process | Sort-Object CPU -Descending | "
            "Select-Object -First 5 "
            "@{Name='name';Expression={$_.ProcessName}}, "
            "@{Name='cpu_seconds';Expression={[math]::Round($_.CPU, 1)}}, "
            "@{Name='memory_mb';Expression={[math]::Round($_.WorkingSet64 / 1MB, 1)}} | "
            "ConvertTo-Json -Compress"
        )
        try:
            completed = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", command],
                capture_output=True,
                text=True,
                check=False,
                timeout=8,
            )
        except Exception:
            return []
        raw = (completed.stdout or "").strip()
        if not raw:
            return []
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if isinstance(payload, dict):
            payload = [payload]
        results: list[dict[str, Any]] = []
        if isinstance(payload, list):
            for item in payload:
                if not isinstance(item, dict):
                    continue
                results.append(
                    {
                        "name": str(item.get("name", "")),
                        "cpu_seconds": float(item.get("cpu_seconds", 0.0)),
                        "memory_mb": float(item.get("memory_mb", 0.0)),
                    }
                )
        return results
