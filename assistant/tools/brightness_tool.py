"""Windows built-in display brightness via WMI (requires system access)."""

from __future__ import annotations

import sys
from typing import Any

from assistant.tools.registry import ToolDefinition


def build_windows_brightness_tool(system_access_manager) -> ToolDefinition:
    async def set_windows_brightness(payload: dict[str, Any]) -> dict[str, Any]:
        if sys.platform != "win32":
            return {
                "stdout": "",
                "stderr": "set_windows_brightness only works when the gateway runs on Windows.",
                "exit_code": 1,
                "host": False,
            }
        try:
            pct = int(payload["percent"])
        except (KeyError, TypeError, ValueError):
            return {
                "stdout": "",
                "stderr": "Invalid percent: provide an integer 0-100.",
                "exit_code": 1,
                "host": True,
            }
        try:
            return await system_access_manager.set_windows_monitor_brightness(
                pct,
                session_id=str(payload.get("session_id", "default")),
                user_id=str(payload.get("user_id", "default")),
                timeout=int(payload.get("timeout", 30)),
            )
        except RuntimeError as exc:
            return {
                "stdout": "",
                "stderr": str(exc),
                "exit_code": 1,
                "host": True,
            }

    return ToolDefinition(
        name="set_windows_brightness",
        description=(
            "Set the built-in laptop or integrated display brightness on Windows (0-100) using the "
            "WMI WmiMonitorBrightnessMethods API. Use this when the user asks to change screen brightness. "
            "Requires system_access enabled. Often does nothing for desktop PCs with only external monitors."
        ),
        parameters={
            "type": "object",
            "properties": {
                "percent": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100,
                    "description": "Target brightness percentage.",
                },
                "timeout": {"type": "integer", "minimum": 5, "maximum": 120, "default": 30},
            },
            "required": ["percent"],
        },
        handler=set_windows_brightness,
        persistence_policy="redacted",
        redactor=(
            (lambda tool_input, tool_result: system_access_manager.redact_tool_result("set_windows_brightness", tool_input, tool_result))
            if system_access_manager is not None
            else None
        ),
    )
