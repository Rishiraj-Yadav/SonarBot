"""Desktop screenshot and OCR tools."""

from __future__ import annotations

from typing import Any

from assistant.tools.desktop_vision_runtime import DesktopVisionRuntime
from assistant.tools.registry import ToolDefinition


def build_desktop_vision_tools(config) -> tuple[list[ToolDefinition], DesktopVisionRuntime]:
    runtime = DesktopVisionRuntime(config)

    async def desktop_active_window(_payload: dict[str, Any]) -> dict[str, Any]:
        return runtime.active_window_info()

    async def desktop_screenshot(_payload: dict[str, Any]) -> dict[str, Any]:
        return await runtime.capture_desktop()

    async def desktop_window_screenshot(_payload: dict[str, Any]) -> dict[str, Any]:
        return await runtime.capture_active_window()

    async def desktop_ocr(payload: dict[str, Any]) -> dict[str, Any]:
        path = payload.get("path")
        return await runtime.ocr_image(str(path) if path else None)

    async def desktop_read_screen(payload: dict[str, Any]) -> dict[str, Any]:
        target = str(payload.get("target", "desktop"))
        return await runtime.read_screen(target=target)

    tools = [
        ToolDefinition(
            name="desktop_active_window",
            description="Return metadata about the currently active Windows desktop window.",
            parameters={"type": "object", "properties": {}},
            handler=desktop_active_window,
        ),
        ToolDefinition(
            name="desktop_screenshot",
            description="Capture a full desktop screenshot and save it into the workspace.",
            parameters={"type": "object", "properties": {}},
            handler=desktop_screenshot,
        ),
        ToolDefinition(
            name="desktop_window_screenshot",
            description="Capture a screenshot of the currently active window and save it into the workspace.",
            parameters={"type": "object", "properties": {}},
            handler=desktop_window_screenshot,
        ),
        ToolDefinition(
            name="desktop_ocr",
            description="Extract visible text from a desktop screenshot path or the most recent desktop capture.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
            },
            handler=desktop_ocr,
        ),
        ToolDefinition(
            name="desktop_read_screen",
            description="Capture the desktop or active window and OCR the visible text in one step.",
            parameters={
                "type": "object",
                "properties": {"target": {"type": "string", "enum": ["desktop", "window"]}},
            },
            handler=desktop_read_screen,
        ),
    ]
    return tools, runtime
