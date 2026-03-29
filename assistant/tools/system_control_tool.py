"""Native Windows system-control tools built on the host access layer."""

from __future__ import annotations

import sys
from typing import Any

from assistant.tools.registry import ToolDefinition


def build_windows_system_control_tools(system_access_manager) -> list[ToolDefinition]:
    async def get_windows_state(payload: dict[str, Any]) -> dict[str, Any]:
        if sys.platform != "win32":
            return {"stderr": "Windows system control only works on Windows.", "exit_code": 1, "host": False}
        return await system_access_manager.get_system_state(
            session_id=str(payload.get("session_id", "default")),
            user_id=str(payload.get("user_id", "default")),
            process_limit=int(payload.get("process_limit", 12)),
            window_limit=int(payload.get("window_limit", 12)),
        )

    async def list_windows_processes(payload: dict[str, Any]) -> dict[str, Any]:
        if sys.platform != "win32":
            return {"stderr": "Windows system control only works on Windows.", "exit_code": 1, "host": False}
        return await system_access_manager.list_processes(
            session_id=str(payload.get("session_id", "default")),
            user_id=str(payload.get("user_id", "default")),
            limit=int(payload.get("limit", 12)),
        )

    async def list_windows(payload: dict[str, Any]) -> dict[str, Any]:
        if sys.platform != "win32":
            return {"stderr": "Windows system control only works on Windows.", "exit_code": 1, "host": False}
        return await system_access_manager.list_windows(
            session_id=str(payload.get("session_id", "default")),
            user_id=str(payload.get("user_id", "default")),
            limit=int(payload.get("limit", 12)),
        )

    async def get_windows_clipboard(payload: dict[str, Any]) -> dict[str, Any]:
        if sys.platform != "win32":
            return {"stderr": "Windows system control only works on Windows.", "exit_code": 1, "host": False}
        return await system_access_manager.get_clipboard(
            session_id=str(payload.get("session_id", "default")),
            user_id=str(payload.get("user_id", "default")),
        )

    async def set_windows_clipboard(payload: dict[str, Any]) -> dict[str, Any]:
        if sys.platform != "win32":
            return {"stderr": "Windows system control only works on Windows.", "exit_code": 1, "host": False}
        return await system_access_manager.set_clipboard(
            text=str(payload.get("text", "")),
            session_id=str(payload.get("session_id", "default")),
            user_id=str(payload.get("user_id", "default")),
            session_key=str(payload.get("session_key", payload.get("session_id", "default"))),
        )

    async def focus_windows_process(payload: dict[str, Any]) -> dict[str, Any]:
        if sys.platform != "win32":
            return {"stderr": "Windows system control only works on Windows.", "exit_code": 1, "host": False}
        return await system_access_manager.focus_process_window(
            pid=int(payload["pid"]),
            session_id=str(payload.get("session_id", "default")),
            user_id=str(payload.get("user_id", "default")),
            session_key=str(payload.get("session_key", payload.get("session_id", "default"))),
        )

    async def terminate_windows_process(payload: dict[str, Any]) -> dict[str, Any]:
        if sys.platform != "win32":
            return {"stderr": "Windows system control only works on Windows.", "exit_code": 1, "host": False}
        return await system_access_manager.terminate_process(
            pid=int(payload["pid"]),
            force=bool(payload.get("force", True)),
            session_id=str(payload.get("session_id", "default")),
            user_id=str(payload.get("user_id", "default")),
            session_key=str(payload.get("session_key", payload.get("session_id", "default"))),
        )

    async def move_windows_window(payload: dict[str, Any]) -> dict[str, Any]:
        if sys.platform != "win32":
            return {"stderr": "Windows system control only works on Windows.", "exit_code": 1, "host": False}
        return await system_access_manager.move_window(
            pid=int(payload["pid"]),
            x=int(payload["x"]),
            y=int(payload["y"]),
            width=int(payload["width"]) if payload.get("width") is not None else None,
            height=int(payload["height"]) if payload.get("height") is not None else None,
            session_id=str(payload.get("session_id", "default")),
            user_id=str(payload.get("user_id", "default")),
            session_key=str(payload.get("session_key", payload.get("session_id", "default"))),
        )

    async def set_windows_window_state(payload: dict[str, Any]) -> dict[str, Any]:
        if sys.platform != "win32":
            return {"stderr": "Windows system control only works on Windows.", "exit_code": 1, "host": False}
        return await system_access_manager.set_window_state(
            pid=int(payload["pid"]),
            state=str(payload["state"]),
            session_id=str(payload.get("session_id", "default")),
            user_id=str(payload.get("user_id", "default")),
            session_key=str(payload.get("session_key", payload.get("session_id", "default"))),
        )

    async def send_windows_keys(payload: dict[str, Any]) -> dict[str, Any]:
        if sys.platform != "win32":
            return {"stderr": "Windows system control only works on Windows.", "exit_code": 1, "host": False}
        return await system_access_manager.send_keys(
            keys=str(payload["keys"]),
            session_id=str(payload.get("session_id", "default")),
            user_id=str(payload.get("user_id", "default")),
            session_key=str(payload.get("session_key", payload.get("session_id", "default"))),
        )

    async def type_windows_text(payload: dict[str, Any]) -> dict[str, Any]:
        if sys.platform != "win32":
            return {"stderr": "Windows system control only works on Windows.", "exit_code": 1, "host": False}
        return await system_access_manager.type_text(
            text=str(payload["text"]),
            session_id=str(payload.get("session_id", "default")),
            user_id=str(payload.get("user_id", "default")),
            session_key=str(payload.get("session_key", payload.get("session_id", "default"))),
        )

    async def click_windows_point(payload: dict[str, Any]) -> dict[str, Any]:
        if sys.platform != "win32":
            return {"stderr": "Windows system control only works on Windows.", "exit_code": 1, "host": False}
        return await system_access_manager.click_mouse(
            x=int(payload["x"]),
            y=int(payload["y"]),
            button=str(payload.get("button", "left")),
            clicks=int(payload.get("clicks", 1)),
            session_id=str(payload.get("session_id", "default")),
            user_id=str(payload.get("user_id", "default")),
            session_key=str(payload.get("session_key", payload.get("session_id", "default"))),
        )

    async def move_windows_pointer(payload: dict[str, Any]) -> dict[str, Any]:
        if sys.platform != "win32":
            return {"stderr": "Windows system control only works on Windows.", "exit_code": 1, "host": False}
        return await system_access_manager.move_mouse(
            x=int(payload["x"]),
            y=int(payload["y"]),
            session_id=str(payload.get("session_id", "default")),
            user_id=str(payload.get("user_id", "default")),
            session_key=str(payload.get("session_key", payload.get("session_id", "default"))),
        )

    async def scroll_windows_pointer(payload: dict[str, Any]) -> dict[str, Any]:
        if sys.platform != "win32":
            return {"stderr": "Windows system control only works on Windows.", "exit_code": 1, "host": False}
        return await system_access_manager.scroll_mouse(
            delta=int(payload["delta"]),
            session_id=str(payload.get("session_id", "default")),
            user_id=str(payload.get("user_id", "default")),
            session_key=str(payload.get("session_key", payload.get("session_id", "default"))),
        )

    async def set_windows_volume(payload: dict[str, Any]) -> dict[str, Any]:
        if sys.platform != "win32":
            return {"stderr": "Windows system control only works on Windows.", "exit_code": 1, "host": False}
        return await system_access_manager.set_volume(
            direction=str(payload["direction"]),
            session_id=str(payload.get("session_id", "default")),
            user_id=str(payload.get("user_id", "default")),
            session_key=str(payload.get("session_key", payload.get("session_id", "default"))),
        )

    def _redact(tool_name: str):
        def redactor(tool_input: dict[str, Any], tool_result: dict[str, Any]) -> dict[str, Any]:
            return system_access_manager.redact_tool_result(tool_name, tool_input, tool_result)

        return redactor

    return [
        ToolDefinition(
            name="get_windows_state",
            description="Inspect the current Windows foreground window, clipboard, processes, and visible windows.",
            parameters={
                "type": "object",
                "properties": {
                    "process_limit": {"type": "integer", "default": 12},
                    "window_limit": {"type": "integer", "default": 12},
                },
            },
            handler=get_windows_state,
            persistence_policy="redacted",
            redactor=_redact("get_windows_state"),
        ),
        ToolDefinition(
            name="list_windows_processes",
            description="List running Windows processes with ids, CPU, memory, and window titles.",
            parameters={
                "type": "object",
                "properties": {"limit": {"type": "integer", "default": 12}},
            },
            handler=list_windows_processes,
            persistence_policy="redacted",
            redactor=_redact("list_windows_processes"),
        ),
        ToolDefinition(
            name="list_windows",
            description="List visible application windows on Windows.",
            parameters={
                "type": "object",
                "properties": {"limit": {"type": "integer", "default": 12}},
            },
            handler=list_windows,
            persistence_policy="redacted",
            redactor=_redact("list_windows"),
        ),
        ToolDefinition(
            name="get_windows_clipboard",
            description="Read the current Windows clipboard text.",
            parameters={"type": "object", "properties": {}},
            handler=get_windows_clipboard,
            persistence_policy="redacted",
            redactor=_redact("get_windows_clipboard"),
        ),
        ToolDefinition(
            name="set_windows_clipboard",
            description="Set the Windows clipboard text.",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            handler=set_windows_clipboard,
            persistence_policy="redacted",
            redactor=_redact("set_windows_clipboard"),
        ),
        ToolDefinition(
            name="focus_windows_process",
            description="Bring a Windows process window to the foreground by process id.",
            parameters={
                "type": "object",
                "properties": {"pid": {"type": "integer"}},
                "required": ["pid"],
            },
            handler=focus_windows_process,
            persistence_policy="redacted",
            redactor=_redact("focus_windows_process"),
        ),
        ToolDefinition(
            name="terminate_windows_process",
            description="Stop a Windows process by process id.",
            parameters={
                "type": "object",
                "properties": {
                    "pid": {"type": "integer"},
                    "force": {"type": "boolean", "default": True},
                },
                "required": ["pid"],
            },
            handler=terminate_windows_process,
            persistence_policy="redacted",
            redactor=_redact("terminate_windows_process"),
        ),
        ToolDefinition(
            name="move_windows_window",
            description="Move or resize a Windows process window by process id.",
            parameters={
                "type": "object",
                "properties": {
                    "pid": {"type": "integer"},
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "width": {"type": "integer"},
                    "height": {"type": "integer"},
                },
                "required": ["pid", "x", "y"],
            },
            handler=move_windows_window,
            persistence_policy="redacted",
            redactor=_redact("move_windows_window"),
        ),
        ToolDefinition(
            name="set_windows_window_state",
            description="Minimize, maximize, or restore a Windows process window by process id.",
            parameters={
                "type": "object",
                "properties": {
                    "pid": {"type": "integer"},
                    "state": {"type": "string", "enum": ["minimize", "maximize", "restore"]},
                },
                "required": ["pid", "state"],
            },
            handler=set_windows_window_state,
            persistence_policy="redacted",
            redactor=_redact("set_windows_window_state"),
        ),
        ToolDefinition(
            name="send_windows_keys",
            description="Send Windows keyboard shortcut syntax to the active window.",
            parameters={
                "type": "object",
                "properties": {"keys": {"type": "string"}},
                "required": ["keys"],
            },
            handler=send_windows_keys,
            persistence_policy="redacted",
            redactor=_redact("send_windows_keys"),
        ),
        ToolDefinition(
            name="type_windows_text",
            description="Type literal text into the active window using clipboard paste.",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            handler=type_windows_text,
            persistence_policy="redacted",
            redactor=_redact("type_windows_text"),
        ),
        ToolDefinition(
            name="click_windows_point",
            description="Click a screen point on Windows.",
            parameters={
                "type": "object",
                "properties": {
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "button": {"type": "string", "default": "left"},
                    "clicks": {"type": "integer", "default": 1},
                },
                "required": ["x", "y"],
            },
            handler=click_windows_point,
            persistence_policy="redacted",
            redactor=_redact("click_windows_point"),
        ),
        ToolDefinition(
            name="move_windows_pointer",
            description="Move the mouse pointer on Windows.",
            parameters={
                "type": "object",
                "properties": {
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                },
                "required": ["x", "y"],
            },
            handler=move_windows_pointer,
            persistence_policy="redacted",
            redactor=_redact("move_windows_pointer"),
        ),
        ToolDefinition(
            name="scroll_windows_pointer",
            description="Scroll the mouse wheel on Windows.",
            parameters={
                "type": "object",
                "properties": {"delta": {"type": "integer"}},
                "required": ["delta"],
            },
            handler=scroll_windows_pointer,
            persistence_policy="redacted",
            redactor=_redact("scroll_windows_pointer"),
        ),
        ToolDefinition(
            name="set_windows_volume",
            description="Raise, lower, or mute the Windows volume using media keys.",
            parameters={
                "type": "object",
                "properties": {"direction": {"type": "string", "enum": ["up", "down", "mute"]}},
                "required": ["direction"],
            },
            handler=set_windows_volume,
            persistence_policy="redacted",
            redactor=_redact("set_windows_volume"),
        ),
    ]
