"""Desktop keyboard, mouse, and clipboard tools."""

from __future__ import annotations

from typing import Any

from assistant.tools.desktop_input_runtime import DesktopInputRuntime
from assistant.tools.registry import ToolDefinition


def build_desktop_input_tools(config, system_access_manager=None) -> tuple[list[ToolDefinition], DesktopInputRuntime]:
    runtime = DesktopInputRuntime(config)

    async def _run_action(
        *,
        tool_name: str,
        action_kind: str,
        target_summary: str,
        category: str,
        payload: dict[str, Any],
        audit_details: dict[str, Any],
        executor,
    ) -> dict[str, Any]:
        if system_access_manager is None:
            if category in {"ask_once", "always_ask"}:
                raise RuntimeError("Desktop input approvals require system access to be enabled.")
            result = await executor()
            return {**result, "status": "completed", "approval_category": category, "approval_mode": "auto"}
        return await system_access_manager.execute_desktop_input_action(
            tool=tool_name,
            action_kind=action_kind,
            target_summary=target_summary,
            approval_category=category,
            session_key=str(payload.get("session_key", "main")),
            session_id=str(payload.get("session_id", "host-session")),
            user_id=str(payload.get("user_id", "default")),
            connection_id=str(payload.get("connection_id", "")),
            channel_name=str(payload.get("channel_name", "")),
            approval_payload=audit_details,
            audit_details=audit_details,
            executor=executor,
        )

    def _redact_sensitive_input(payload: dict[str, Any]) -> dict[str, Any]:
        redacted = {key: value for key, value in payload.items() if key not in {"text", "content"}}
        if "text" in payload:
            redacted["text_chars"] = len(str(payload["text"]))
        if "content" in payload:
            redacted["content_chars"] = len(str(payload["content"]))
        return redacted

    def _extract_text_input(payload: dict[str, Any]) -> str:
        preferred_keys = ("text", "content", "value", "input", "typed_text", "text_to_type")
        for key in preferred_keys:
            if key in payload:
                return str(payload[key])

        ignored_keys = {
            "session_key",
            "session_id",
            "user_id",
            "connection_id",
            "channel_name",
            "expected_window_title",
            "expected_process_name",
        }
        string_like_keys = [
            key
            for key, value in payload.items()
            if key not in ignored_keys and isinstance(value, (str, int, float))
        ]
        if len(string_like_keys) == 1:
            return str(payload[string_like_keys[0]])
        raise KeyError("text")

    async def desktop_mouse_position(_payload: dict[str, Any]) -> dict[str, Any]:
        return runtime.mouse_position()

    async def desktop_mouse_move(payload: dict[str, Any]) -> dict[str, Any]:
        x = int(payload["x"])
        y = int(payload["y"])
        return await _run_action(
            tool_name="desktop_mouse_move",
            action_kind="desktop_move",
            target_summary=f"Move mouse to ({x}, {y}) [{payload.get('coordinate_space', 'screen')}]",
            category="auto_allow",
            payload=payload,
            audit_details={
                "x": x,
                "y": y,
                "coordinate_space": str(payload.get("coordinate_space", "screen")),
            },
            executor=lambda: _maybe_async(
                runtime.move_mouse(
                    x=x,
                    y=y,
                    coordinate_space=str(payload.get("coordinate_space", "screen")),
                    expected_window_title=str(payload.get("expected_window_title", "")),
                    expected_process_name=str(payload.get("expected_process_name", "")),
                )
            ),
        )

    async def desktop_mouse_click(payload: dict[str, Any]) -> dict[str, Any]:
        x = int(payload["x"])
        y = int(payload["y"])
        button = str(payload.get("button", "left")).lower()
        count = int(payload.get("count", 1))
        category = "always_ask" if bool(getattr(config.desktop_input, "confirm_clicks", True)) else "auto_allow"
        return await _run_action(
            tool_name="desktop_mouse_click",
            action_kind="desktop_click",
            target_summary=f"{button} click x{count} at ({x}, {y}) [{payload.get('coordinate_space', 'screen')}]",
            category=category,
            payload=payload,
            audit_details={
                "button": button,
                "count": count,
                "x": x,
                "y": y,
                "coordinate_space": str(payload.get("coordinate_space", "screen")),
            },
            executor=lambda: _maybe_async(
                runtime.click_mouse(
                    x=x,
                    y=y,
                    coordinate_space=str(payload.get("coordinate_space", "screen")),
                    button=button,
                    count=count,
                    expected_window_title=str(payload.get("expected_window_title", "")),
                    expected_process_name=str(payload.get("expected_process_name", "")),
                )
            ),
        )

    async def desktop_mouse_scroll(payload: dict[str, Any]) -> dict[str, Any]:
        direction = str(payload.get("direction", "down"))
        amount = int(payload.get("amount", 1))
        return await _run_action(
            tool_name="desktop_mouse_scroll",
            action_kind="desktop_scroll",
            target_summary=f"Scroll {direction} {amount}",
            category="auto_allow",
            payload=payload,
            audit_details={"direction": direction, "amount": amount},
            executor=lambda: _maybe_async(
                runtime.scroll_mouse(
                    direction=direction,
                    amount=amount,
                    expected_window_title=str(payload.get("expected_window_title", "")),
                    expected_process_name=str(payload.get("expected_process_name", "")),
                )
            ),
        )

    async def desktop_keyboard_type(payload: dict[str, Any]) -> dict[str, Any]:
        text = _extract_text_input(payload)
        category = "always_ask" if bool(getattr(config.desktop_input, "confirm_typing", True)) else "auto_allow"
        return await _run_action(
            tool_name="desktop_keyboard_type",
            action_kind="desktop_type",
            target_summary=f"Type {len(text)} characters",
            category=category,
            payload=payload,
            audit_details={"characters_typed": len(text)},
            executor=lambda: _maybe_async(
                runtime.type_text(
                    text=text,
                    expected_window_title=str(payload.get("expected_window_title", "")),
                    expected_process_name=str(payload.get("expected_process_name", "")),
                )
            ),
        )

    async def desktop_keyboard_hotkey(payload: dict[str, Any]) -> dict[str, Any]:
        hotkey = str(payload["hotkey"])
        normalized_hotkey = runtime.normalize_hotkey(hotkey)
        risky = bool(getattr(config.desktop_input, "confirm_risky_hotkeys", True)) and not runtime.is_safe_hotkey(normalized_hotkey)
        category = "always_ask" if risky else "auto_allow"
        return await _run_action(
            tool_name="desktop_keyboard_hotkey",
            action_kind="desktop_hotkey",
            target_summary=f"Press {normalized_hotkey}",
            category=category,
            payload=payload,
            audit_details={"hotkey": normalized_hotkey},
            executor=lambda: _maybe_async(
                runtime.press_hotkey(
                    hotkey=normalized_hotkey,
                    expected_window_title=str(payload.get("expected_window_title", "")),
                    expected_process_name=str(payload.get("expected_process_name", "")),
                )
            ),
        )

    async def desktop_clipboard_read(_payload: dict[str, Any]) -> dict[str, Any]:
        result = runtime.read_clipboard()
        result["status"] = "completed"
        return result

    async def desktop_clipboard_write(payload: dict[str, Any]) -> dict[str, Any]:
        text = _extract_text_input(payload)
        category = "always_ask" if bool(getattr(config.desktop_input, "confirm_clipboard_write", True)) else "auto_allow"
        return await _run_action(
            tool_name="desktop_clipboard_write",
            action_kind="desktop_clipboard_write",
            target_summary=f"Set clipboard text ({len(text)} characters)",
            category=category,
            payload=payload,
            audit_details={"char_count": len(text)},
            executor=lambda: _maybe_async(runtime.write_clipboard(text=text)),
        )

    tools = [
        ToolDefinition(
            name="desktop_mouse_position",
            description="Return the current cursor position and the active window metadata.",
            parameters={"type": "object", "properties": {}},
            handler=desktop_mouse_position,
        ),
        ToolDefinition(
            name="desktop_mouse_move",
            description="Move the mouse cursor to explicit screen or active-window coordinates.",
            parameters={
                "type": "object",
                "properties": {
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "coordinate_space": {"type": "string", "enum": ["screen", "active_window"], "default": "screen"},
                    "expected_window_title": {"type": "string"},
                    "expected_process_name": {"type": "string"},
                },
                "required": ["x", "y"],
            },
            handler=desktop_mouse_move,
        ),
        ToolDefinition(
            name="desktop_mouse_click",
            description="Click at explicit screen or active-window coordinates.",
            parameters={
                "type": "object",
                "properties": {
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "button": {"type": "string", "enum": ["left", "right"], "default": "left"},
                    "count": {
                        "type": "integer",
                        "default": 1,
                        "description": "Click count: use 1 for a single click or 2 for a double click.",
                    },
                    "coordinate_space": {"type": "string", "enum": ["screen", "active_window"], "default": "screen"},
                    "expected_window_title": {"type": "string"},
                    "expected_process_name": {"type": "string"},
                },
                "required": ["x", "y"],
            },
            handler=desktop_mouse_click,
        ),
        ToolDefinition(
            name="desktop_mouse_scroll",
            description="Scroll the mouse wheel up or down in the current active window.",
            parameters={
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "enum": ["up", "down"]},
                    "amount": {"type": "integer", "default": 1},
                    "expected_window_title": {"type": "string"},
                    "expected_process_name": {"type": "string"},
                },
                "required": ["direction"],
            },
            handler=desktop_mouse_scroll,
        ),
        ToolDefinition(
            name="desktop_keyboard_type",
            description="Type plain text into the active Windows application.",
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "expected_window_title": {"type": "string"},
                    "expected_process_name": {"type": "string"},
                },
                "required": ["text"],
            },
            handler=desktop_keyboard_type,
            persistence_policy="redacted",
            redactor=lambda _tool_input, tool_result: (
                system_access_manager.redact_tool_result("desktop_keyboard_type", {}, tool_result)
                if system_access_manager is not None
                else {
                    "status": tool_result.get("status"),
                    "characters_typed": tool_result.get("characters_typed"),
                    "approval_category": tool_result.get("approval_category"),
                    "approval_mode": tool_result.get("approval_mode"),
                }
            ),
            input_redactor=_redact_sensitive_input,
        ),
        ToolDefinition(
            name="desktop_keyboard_hotkey",
            description="Press a hotkey chord in the active Windows application.",
            parameters={
                "type": "object",
                "properties": {
                    "hotkey": {"type": "string"},
                    "expected_window_title": {"type": "string"},
                    "expected_process_name": {"type": "string"},
                },
                "required": ["hotkey"],
            },
            handler=desktop_keyboard_hotkey,
            persistence_policy="redacted",
            redactor=lambda _tool_input, tool_result: (
                system_access_manager.redact_tool_result("desktop_keyboard_hotkey", {}, tool_result)
                if system_access_manager is not None
                else {
                    "status": tool_result.get("status"),
                    "hotkey": tool_result.get("hotkey"),
                    "approval_category": tool_result.get("approval_category"),
                    "approval_mode": tool_result.get("approval_mode"),
                }
            ),
        ),
        ToolDefinition(
            name="desktop_clipboard_read",
            description="Read the current Unicode text content of the Windows clipboard.",
            parameters={"type": "object", "properties": {}},
            handler=desktop_clipboard_read,
            persistence_policy="redacted",
            redactor=lambda _tool_input, tool_result: (
                system_access_manager.redact_tool_result("desktop_clipboard_read", {}, tool_result)
                if system_access_manager is not None
                else {
                    "status": tool_result.get("status"),
                    "char_count": tool_result.get("char_count"),
                    "line_count": tool_result.get("line_count"),
                }
            ),
        ),
        ToolDefinition(
            name="desktop_clipboard_write",
            description="Replace the Windows clipboard text with the provided Unicode string.",
            parameters={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
            handler=desktop_clipboard_write,
            persistence_policy="redacted",
            redactor=lambda _tool_input, tool_result: (
                system_access_manager.redact_tool_result("desktop_clipboard_write", {}, tool_result)
                if system_access_manager is not None
                else {
                    "status": tool_result.get("status"),
                    "char_count": tool_result.get("char_count"),
                    "approval_category": tool_result.get("approval_category"),
                    "approval_mode": tool_result.get("approval_mode"),
                }
            ),
            input_redactor=_redact_sensitive_input,
        ),
    ]
    return tools, runtime


async def _maybe_async(result: dict[str, Any]) -> dict[str, Any]:
    return result
