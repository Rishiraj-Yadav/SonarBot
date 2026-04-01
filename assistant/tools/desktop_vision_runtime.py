"""Windows desktop screenshots, OCR, and active-window awareness."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from assistant.tools.image_ocr import ocr_image_with_gemini
from assistant.tools.windows_desktop import (
    get_foreground_window_handle,
    get_foreground_window_snapshot,
    get_window_rect,
    load_desktop_libraries,
)


@dataclass
class DesktopVisionRuntime:
    config: Any
    screenshots_dir: Path = field(init=False)
    _last_capture_path: Path | None = field(init=False, default=None)
    _last_capture_target: str | None = field(init=False, default=None)
    user32: Any | None = field(init=False, default=None)
    kernel32: Any | None = field(init=False, default=None)
    _availability_error: str = field(init=False, default="")

    def __post_init__(self) -> None:
        self.screenshots_dir = self.config.agent.workspace_dir / self.config.desktop_vision.screenshots_subdir
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            self.user32, self.kernel32, self._availability_error = load_desktop_libraries()

    def ensure_available(self) -> None:
        if not bool(getattr(self.config.desktop_vision, "enabled", False)):
            raise RuntimeError("Desktop vision is not enabled.")
        if sys.platform != "win32":
            raise RuntimeError("Desktop vision is only available on Windows hosts.")
        if self.user32 is None or self.kernel32 is None:
            detail = f" ({self._availability_error})" if self._availability_error else ""
            raise RuntimeError(f"Desktop vision is unavailable on this Windows host{detail}.")

    def active_window_info(self) -> dict[str, Any]:
        self.ensure_available()
        window = self._get_active_window_snapshot()
        return {"active_window": window, "captured_at": datetime.now(timezone.utc).isoformat()}

    async def capture_desktop(self) -> dict[str, Any]:
        self.ensure_available()
        active_window = self._get_active_window_snapshot()
        image = await asyncio.to_thread(self._capture_image, None)
        path, width, height = await asyncio.to_thread(self._save_capture, image, "desktop")
        return {
            "path": str(path),
            "scope": "desktop",
            "width": width,
            "height": height,
            "active_window": active_window,
            "captured_at": datetime.now(timezone.utc).isoformat(),
        }

    async def capture_active_window(self) -> dict[str, Any]:
        self.ensure_available()
        active_window = self._get_active_window_snapshot()
        left, top, right, bottom = self._get_active_window_rect()
        image = await asyncio.to_thread(self._capture_image, (left, top, right, bottom))
        path, width, height = await asyncio.to_thread(self._save_capture, image, "window")
        return {
            "path": str(path),
            "scope": "window",
            "width": width,
            "height": height,
            "active_window": active_window,
            "captured_at": datetime.now(timezone.utc).isoformat(),
        }

    async def ocr_image(self, path: str | None = None) -> dict[str, Any]:
        self.ensure_available()
        if not bool(getattr(self.config.desktop_vision, "ocr_enabled", True)):
            raise RuntimeError("Desktop OCR is disabled.")
        target_path = Path(path).expanduser().resolve() if path else self._last_capture_path
        if target_path is None:
            raise RuntimeError("No desktop screenshot is available to read yet.")
        if not target_path.exists():
            raise RuntimeError(f"Desktop screenshot '{target_path}' does not exist.")
        content = await ocr_image_with_gemini(
            self.config,
            target_path,
            prompt="Extract the visible text from this desktop screenshot. Return plain text only.",
        )
        max_chars = max(1000, int(getattr(self.config.desktop_vision, "max_ocr_characters", 12000)))
        normalized = content.strip()
        return {
            "path": str(target_path),
            "content": normalized[:max_chars],
            "truncated": len(normalized) > max_chars,
        }

    async def read_screen(self, *, target: str = "desktop") -> dict[str, Any]:
        self.ensure_available()
        normalized_target = target.strip().lower()
        if normalized_target not in {"desktop", "window"}:
            raise RuntimeError("desktop_read_screen target must be 'desktop' or 'window'.")
        capture = await (self.capture_active_window() if normalized_target == "window" else self.capture_desktop())
        ocr = await self.ocr_image(capture["path"])
        return {
            "target": normalized_target,
            "path": capture["path"],
            "active_window": capture["active_window"],
            "content": ocr["content"],
            "truncated": ocr["truncated"],
            "captured_at": capture["captured_at"],
        }

    def _capture_image(self, bbox: tuple[int, int, int, int] | None):
        try:
            from PIL import ImageGrab  # type: ignore
        except Exception as exc:  # pragma: no cover - dependency error path
            raise RuntimeError("Pillow is not installed. Run `uv sync --extra dev`.") from exc
        return ImageGrab.grab(bbox=bbox, all_screens=bbox is None)

    def _save_capture(self, image: Any, prefix: str) -> tuple[Path, int, int]:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        target = self.screenshots_dir / f"{prefix}-{timestamp}.{self.config.desktop_vision.capture_format}"
        image.save(target, self.config.desktop_vision.capture_format.upper())
        self._last_capture_path = target
        self._last_capture_target = prefix
        width, height = getattr(image, "size", (0, 0))
        return target, int(width), int(height)

    def _get_active_window_snapshot(self) -> dict[str, Any]:
        assert self.user32 is not None
        assert self.kernel32 is not None
        return get_foreground_window_snapshot(self.user32, self.kernel32)

    def _get_active_window_rect(self) -> tuple[int, int, int, int]:
        assert self.user32 is not None
        return get_window_rect(self.user32, get_foreground_window_handle(self.user32))
