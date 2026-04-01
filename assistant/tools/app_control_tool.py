"""Windows app and window control tools."""

from __future__ import annotations

from typing import Any

from assistant.tools.app_control_runtime import AppControlRuntime
from assistant.tools.registry import ToolDefinition


def build_app_control_tools(config) -> tuple[list[ToolDefinition], AppControlRuntime]:
    runtime = AppControlRuntime(config)

    async def apps_list_windows(_payload: dict[str, Any]) -> dict[str, Any]:
        return runtime.list_windows()

    async def apps_open(payload: dict[str, Any]) -> dict[str, Any]:
        raw_args = payload.get("args", [])
        args = [str(item) for item in raw_args] if isinstance(raw_args, list) else []
        return runtime.open_app(str(payload["target"]), args=args)

    async def apps_focus(payload: dict[str, Any]) -> dict[str, Any]:
        return runtime.focus_window(str(payload["target"]))

    async def apps_minimize(payload: dict[str, Any]) -> dict[str, Any]:
        return runtime.minimize_window(str(payload["target"]))

    async def apps_maximize(payload: dict[str, Any]) -> dict[str, Any]:
        return runtime.maximize_window(str(payload["target"]))

    async def apps_restore(payload: dict[str, Any]) -> dict[str, Any]:
        return runtime.restore_window(str(payload["target"]))

    async def apps_snap(payload: dict[str, Any]) -> dict[str, Any]:
        return runtime.snap_window(str(payload["target"]), str(payload["position"]))

    tools = [
        ToolDefinition(
            name="apps_list_windows",
            description="List visible Windows app windows with titles, process names, and active/minimized state.",
            parameters={"type": "object", "properties": {}},
            handler=apps_list_windows,
        ),
        ToolDefinition(
            name="apps_open",
            description="Launch a configured Windows app alias or safe configured executable path.",
            parameters={
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "args": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["target"],
            },
            handler=apps_open,
        ),
        ToolDefinition(
            name="apps_focus",
            description="Focus a visible Windows app window by alias, process, exact title, or window id.",
            parameters={
                "type": "object",
                "properties": {"target": {"type": "string"}},
                "required": ["target"],
            },
            handler=apps_focus,
        ),
        ToolDefinition(
            name="apps_minimize",
            description="Minimize a visible Windows app window by alias, process, exact title, or window id.",
            parameters={
                "type": "object",
                "properties": {"target": {"type": "string"}},
                "required": ["target"],
            },
            handler=apps_minimize,
        ),
        ToolDefinition(
            name="apps_maximize",
            description="Maximize a visible Windows app window by alias, process, exact title, or window id.",
            parameters={
                "type": "object",
                "properties": {"target": {"type": "string"}},
                "required": ["target"],
            },
            handler=apps_maximize,
        ),
        ToolDefinition(
            name="apps_restore",
            description="Restore a minimized or maximized Windows app window by alias, process, exact title, or window id.",
            parameters={
                "type": "object",
                "properties": {"target": {"type": "string"}},
                "required": ["target"],
            },
            handler=apps_restore,
        ),
        ToolDefinition(
            name="apps_snap",
            description="Snap a visible Windows app window to the left or right half of the monitor work area.",
            parameters={
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "position": {"type": "string", "enum": ["left", "right"]},
                },
                "required": ["target", "position"],
            },
            handler=apps_snap,
        ),
    ]
    return tools, runtime
