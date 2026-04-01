"""State collection helpers for verified coworker tasks."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class DesktopCoworkerStateCollector:
    def __init__(self, config, tool_registry) -> None:
        self.config = config
        self.tool_registry = tool_registry

    async def capture(
        self,
        *,
        include_capture: bool = False,
        capture_target: str = "window",
        include_ocr: bool = False,
        include_clipboard: bool = False,
    ) -> dict[str, Any]:
        snapshot: dict[str, Any] = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "active_window": await self._active_window(),
        }
        if include_ocr and self.tool_registry.has("desktop_read_screen"):
            target = "window" if capture_target == "window" else "desktop"
            read_result = await self.tool_registry.dispatch("desktop_read_screen", {"target": target})
            snapshot["capture_path"] = str(read_result.get("path", ""))
            snapshot["screen_text"] = str(read_result.get("content", ""))
            if not snapshot.get("active_window") and isinstance(read_result.get("active_window"), dict):
                snapshot["active_window"] = dict(read_result["active_window"])
        elif include_capture:
            if capture_target == "window" and self.tool_registry.has("desktop_window_screenshot"):
                capture = await self.tool_registry.dispatch("desktop_window_screenshot", {})
                snapshot["capture_path"] = str(capture.get("path", ""))
                if not snapshot.get("active_window") and isinstance(capture.get("active_window"), dict):
                    snapshot["active_window"] = dict(capture["active_window"])
            elif self.tool_registry.has("desktop_screenshot"):
                capture = await self.tool_registry.dispatch("desktop_screenshot", {})
                snapshot["capture_path"] = str(capture.get("path", ""))
                if not snapshot.get("active_window") and isinstance(capture.get("active_window"), dict):
                    snapshot["active_window"] = dict(capture["active_window"])
        if include_clipboard and self.tool_registry.has("desktop_clipboard_read"):
            clipboard = await self.tool_registry.dispatch("desktop_clipboard_read", {})
            snapshot["clipboard_text"] = str(clipboard.get("content", ""))
        return snapshot

    async def _active_window(self) -> dict[str, Any]:
        if self.tool_registry.has("desktop_active_window"):
            result = await self.tool_registry.dispatch("desktop_active_window", {})
            window = result.get("active_window", {})
            return dict(window) if isinstance(window, dict) else {}
        if self.tool_registry.has("apps_list_windows"):
            result = await self.tool_registry.dispatch("apps_list_windows", {})
            for item in result.get("windows", []):
                if isinstance(item, dict) and item.get("is_foreground"):
                    return dict(item)
        return {}
