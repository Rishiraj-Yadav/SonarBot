"""Structured executor for desktop routines."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any


class DesktopRoutineExecutor:
    def __init__(self, tool_registry, config) -> None:
        self.tool_registry = tool_registry
        self.config = config
        self.safe_hotkeys = {str(item).strip().lower() for item in getattr(config.desktop_input, "safe_hotkeys", [])}

    def risky_step_count(self, steps: list[dict[str, Any]]) -> int:
        return sum(1 for step in steps if self.is_step_risky(step))

    def is_step_risky(self, step: dict[str, Any]) -> bool:
        step_type = str(step.get("type", "")).strip().lower()
        if step_type in {
            "desktop_mouse_click",
            "desktop_keyboard_type",
            "desktop_clipboard_write",
            "write_host_file",
            "delete_host_file",
            "move_host_file",
        }:
            return True
        if step_type == "desktop_keyboard_hotkey":
            hotkey = self._normalize_hotkey(str(step.get("hotkey", "")))
            return hotkey not in self.safe_hotkeys
        return False

    def summarize_steps(self, steps: list[dict[str, Any]]) -> str:
        parts: list[str] = []
        for step in steps:
            step_type = str(step.get("type", "")).strip().lower()
            if step_type == "open_app":
                parts.append(f"open {step.get('target', 'app')}")
            elif step_type == "open_host_path":
                parts.append(f"open {step.get('path', 'path')}")
            elif step_type == "snap_window":
                parts.append(f"snap {step.get('target', 'window')} {step.get('position', 'left')}")
            elif step_type == "move_host_file":
                parts.append("move file")
            elif step_type == "copy_host_file":
                parts.append("copy file")
            elif step_type == "notify":
                parts.append("notify")
            elif step_type == "desktop_read_screen":
                parts.append("read screen")
            elif step_type == "desktop_screenshot":
                parts.append("capture screenshot")
            elif step_type == "desktop_keyboard_hotkey":
                parts.append(f"press {step.get('hotkey', 'hotkey')}")
            elif step_type == "desktop_keyboard_type":
                parts.append("type text")
            else:
                parts.append(step_type.replace("_", " "))
        if not parts:
            return "no steps"
        if len(parts) <= 4:
            return ", ".join(parts)
        return ", ".join(parts[:4]) + f", and {len(parts) - 4} more"

    async def execute(
        self,
        *,
        rule: dict[str, Any],
        event_payload: dict[str, Any],
        session_key: str,
        session_id: str,
        user_id: str,
        connection_id: str = "",
        channel_name: str = "",
    ) -> dict[str, Any]:
        steps = list(rule.get("steps", []))
        context = self._build_context(rule, event_payload)
        step_results: list[dict[str, Any]] = []
        messages: list[str] = []
        status = "completed"
        failure_error = ""

        for index, step in enumerate(steps, start=1):
            try:
                result = await self._execute_step(
                    step=step,
                    context=context,
                    session_key=session_key,
                    session_id=session_id,
                    user_id=user_id,
                    connection_id=connection_id,
                    channel_name=channel_name,
                )
                step_status = str(result.get("status", "completed"))
                step_record = {
                    "index": index,
                    "type": str(step.get("type", "")),
                    "status": step_status,
                    "summary": str(result.get("summary", "")),
                }
                if "path" in result:
                    step_record["path"] = result["path"]
                step_results.append(step_record)
                if result.get("summary"):
                    messages.append(str(result["summary"]))
                if step_status == "failed" and not bool(step.get("continue_on_error", False)):
                    status = "failed"
                    failure_error = str(result.get("error", "step failed"))
                    break
            except Exception as exc:
                step_results.append(
                    {
                        "index": index,
                        "type": str(step.get("type", "")),
                        "status": "failed",
                        "summary": str(exc),
                    }
                )
                if bool(step.get("continue_on_error", False)):
                    messages.append(f"{step.get('type', 'step')}: {exc}")
                    continue
                status = "failed"
                failure_error = str(exc)
                break

        if not messages:
            messages.append(f"Routine '{rule.get('name', 'desktop routine')}' finished with status {status}.")
        if status == "failed" and failure_error:
            messages.append(f"Stopped on failure: {failure_error}")
        return {
            "status": status,
            "message": "\n".join(messages).strip(),
            "steps": step_results,
            "summary": self.summarize_steps(steps),
            "risky_step_count": self.risky_step_count(steps),
        }

    async def _execute_step(
        self,
        *,
        step: dict[str, Any],
        context: dict[str, str],
        session_key: str,
        session_id: str,
        user_id: str,
        connection_id: str,
        channel_name: str,
    ) -> dict[str, Any]:
        step_type = str(step.get("type", "")).strip().lower()
        if not step_type:
            raise ValueError("Routine step type cannot be empty.")
        common_host = {
            "session_key": session_key,
            "session_id": session_id,
            "user_id": user_id,
            "connection_id": connection_id,
            "channel_name": channel_name,
        }

        if step_type == "open_app":
            target = self._render_required(step.get("target"), context, "open_app.target")
            result = await self.tool_registry.dispatch("apps_open", {"target": target})
            return {"status": result.get("status", "completed"), "summary": f"Opened {target}."}
        if step_type == "focus_window":
            target = self._render_required(step.get("target"), context, "focus_window.target")
            result = await self.tool_registry.dispatch("apps_focus", {"target": target})
            return {"status": result.get("status", "completed"), "summary": f"Focused {target}."}
        if step_type == "minimize_window":
            target = self._render_required(step.get("target"), context, "minimize_window.target")
            result = await self.tool_registry.dispatch("apps_minimize", {"target": target})
            return {"status": result.get("status", "completed"), "summary": f"Minimized {target}."}
        if step_type == "maximize_window":
            target = self._render_required(step.get("target"), context, "maximize_window.target")
            result = await self.tool_registry.dispatch("apps_maximize", {"target": target})
            return {"status": result.get("status", "completed"), "summary": f"Maximized {target}."}
        if step_type == "restore_window":
            target = self._render_required(step.get("target"), context, "restore_window.target")
            result = await self.tool_registry.dispatch("apps_restore", {"target": target})
            return {"status": result.get("status", "completed"), "summary": f"Restored {target}."}
        if step_type == "snap_window":
            target = self._render_required(step.get("target"), context, "snap_window.target")
            position = self._render_required(step.get("position"), context, "snap_window.position").lower()
            result = await self.tool_registry.dispatch("apps_snap", {"target": target, "position": position})
            return {"status": result.get("status", "completed"), "summary": f"Snapped {target} to the {position}."}
        if step_type == "open_host_path":
            path = self._render_required(step.get("path"), context, "open_host_path.path")
            command = f"Start-Process -FilePath '{self._ps_quote(path)}'"
            result = await self.tool_registry.dispatch("exec_shell", {"command": command, "host": True, **common_host})
            return {"status": result.get("status", "completed"), "summary": f"Opened {path}.", "path": path}
        if step_type == "list_host_dir":
            path = self._render_required(step.get("path"), context, "list_host_dir.path")
            result = await self.tool_registry.dispatch("list_host_dir", {"path": path, **common_host})
            entry_count = len(result.get("entries", []))
            return {"status": result.get("status", "completed"), "summary": f"Listed {entry_count} item(s) in {path}.", "path": path}
        if step_type == "read_host_file":
            path = self._render_required(step.get("path"), context, "read_host_file.path")
            result = await self.tool_registry.dispatch("read_host_file", {"path": path, **common_host})
            char_count = len(str(result.get("content", "")))
            return {"status": result.get("status", "completed"), "summary": f"Read {char_count} character(s) from {path}.", "path": path}
        if step_type == "write_host_file":
            path = self._render_required(step.get("path"), context, "write_host_file.path")
            content = self._render_required(step.get("content"), context, "write_host_file.content")
            result = await self.tool_registry.dispatch("write_host_file", {"path": path, "content": content, **common_host})
            return {"status": result.get("status", "completed"), "summary": f"Updated {path}.", "path": path}
        if step_type == "move_host_file":
            source = self._render_required(step.get("source", "{event_path}"), context, "move_host_file.source")
            destination = self._render_required(step.get("destination"), context, "move_host_file.destination")
            result = await self.tool_registry.dispatch("move_host_file", {"source": source, "destination": destination, **common_host})
            return {"status": result.get("status", "completed"), "summary": f"Moved {Path(source).name} to {destination}.", "path": destination}
        if step_type == "copy_host_file":
            source = self._render_required(step.get("source", "{event_path}"), context, "copy_host_file.source")
            destination = self._render_required(step.get("destination"), context, "copy_host_file.destination")
            result = await self.tool_registry.dispatch("copy_host_file", {"source": source, "destination": destination, **common_host})
            return {"status": result.get("status", "completed"), "summary": f"Copied {Path(source).name} to {destination}.", "path": destination}
        if step_type == "delete_host_file":
            path = self._render_required(step.get("path", "{event_path}"), context, "delete_host_file.path")
            result = await self.tool_registry.dispatch("delete_host_file", {"path": path, **common_host})
            return {"status": result.get("status", "completed"), "summary": f"Deleted {path}.", "path": path}
        if step_type == "desktop_screenshot":
            result = await self.tool_registry.dispatch("desktop_screenshot", {})
            path = str(result.get("path", ""))
            context["last_capture_path"] = path
            return {"status": result.get("status", "completed"), "summary": f"Captured a desktop screenshot at {path}.", "path": path}
        if step_type == "desktop_read_screen":
            target = self._render_optional(step.get("target", "desktop"), context) or "desktop"
            result = await self.tool_registry.dispatch("desktop_read_screen", {"target": target})
            path = str(result.get("path", ""))
            context["last_capture_path"] = path
            return {"status": result.get("status", "completed"), "summary": f"Read the {target} at {path}.", "path": path}
        if step_type == "desktop_keyboard_hotkey":
            hotkey = self._render_required(step.get("hotkey"), context, "desktop_keyboard_hotkey.hotkey")
            result = await self.tool_registry.dispatch(
                "desktop_keyboard_hotkey",
                {
                    "hotkey": hotkey,
                    **self._window_guards(step, context),
                },
            )
            return {"status": result.get("status", "completed"), "summary": f"Pressed {hotkey}."}
        if step_type == "desktop_keyboard_type":
            text = self._render_required(step.get("text"), context, "desktop_keyboard_type.text")
            result = await self.tool_registry.dispatch(
                "desktop_keyboard_type",
                {
                    "text": text,
                    **self._window_guards(step, context),
                },
            )
            return {"status": result.get("status", "completed"), "summary": f"Typed {len(text)} character(s)."}
        if step_type == "desktop_mouse_move":
            x, y = self._render_coordinates(step, context)
            result = await self.tool_registry.dispatch(
                "desktop_mouse_move",
                {
                    "x": x,
                    "y": y,
                    "coordinate_space": str(step.get("coordinate_space", "screen")),
                    **self._window_guards(step, context),
                },
            )
            return {"status": result.get("status", "completed"), "summary": f"Moved the mouse to ({x}, {y})."}
        if step_type == "desktop_mouse_click":
            x, y = self._render_coordinates(step, context)
            payload = {
                "x": x,
                "y": y,
                "coordinate_space": str(step.get("coordinate_space", "screen")),
                "button": str(step.get("button", "left")),
                "count": int(step.get("count", 1)),
                **self._window_guards(step, context),
            }
            result = await self.tool_registry.dispatch("desktop_mouse_click", payload)
            return {"status": result.get("status", "completed"), "summary": f"Clicked at ({x}, {y})."}
        if step_type == "desktop_clipboard_read":
            result = await self.tool_registry.dispatch("desktop_clipboard_read", {})
            content = str(result.get("content", ""))
            context["clipboard_text"] = content
            return {"status": result.get("status", "completed"), "summary": f"Read {len(content)} clipboard character(s)."}
        if step_type == "desktop_clipboard_write":
            text = self._render_required(step.get("text"), context, "desktop_clipboard_write.text")
            result = await self.tool_registry.dispatch("desktop_clipboard_write", {"text": text})
            return {"status": result.get("status", "completed"), "summary": f"Updated the clipboard ({len(text)} character(s))."}
        if step_type == "notify":
            text = self._render_required(step.get("text"), context, "notify.text")
            return {"status": "completed", "summary": text}
        raise ValueError(f"Unsupported desktop routine step '{step_type}'.")

    def _build_context(self, rule: dict[str, Any], event_payload: dict[str, Any]) -> dict[str, str]:
        event_path = str(event_payload.get("path", "")).strip()
        event_path_obj = Path(event_path) if event_path else None
        return {
            "routine_name": str(rule.get("name", "")).strip(),
            "routine_summary": str(rule.get("summary", "")).strip(),
            "watch_path": str(rule.get("watch_path", "")).strip(),
            "event_path": event_path,
            "event_name": event_path_obj.name if event_path_obj is not None else "",
            "event_stem": event_path_obj.stem if event_path_obj is not None else "",
            "event_suffix": event_path_obj.suffix if event_path_obj is not None else "",
            "now": datetime.now().isoformat(timespec="seconds"),
        }

    def _window_guards(self, step: dict[str, Any], context: dict[str, str]) -> dict[str, str]:
        expected_window_title = self._render_optional(step.get("expected_window_title"), context)
        expected_process_name = self._render_optional(step.get("expected_process_name"), context)
        guards: dict[str, str] = {}
        if expected_window_title:
            guards["expected_window_title"] = expected_window_title
        if expected_process_name:
            guards["expected_process_name"] = expected_process_name
        return guards

    def _render_coordinates(self, step: dict[str, Any], context: dict[str, str]) -> tuple[int, int]:
        x = self._render_required(step.get("x"), context, "coordinate.x")
        y = self._render_required(step.get("y"), context, "coordinate.y")
        return int(x), int(y)

    def _render_required(self, value: Any, context: dict[str, str], field_name: str) -> str:
        rendered = self._render_optional(value, context)
        if not rendered:
            raise ValueError(f"Missing value for {field_name}.")
        return rendered

    def _render_optional(self, value: Any, context: dict[str, str]) -> str:
        if value is None:
            return ""
        rendered = str(value)
        for key, item in context.items():
            rendered = rendered.replace(f"{{{key}}}", item)
        return rendered.strip()

    def _normalize_hotkey(self, value: str) -> str:
        parts = [segment.strip().lower() for segment in value.replace(" ", "").split("+") if segment.strip()]
        return "+".join(parts)

    def _ps_quote(self, value: str) -> str:
        return value.replace("'", "''")
