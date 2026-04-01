from __future__ import annotations

from pathlib import Path

import pytest

from assistant.tools.desktop_vision_runtime import DesktopVisionRuntime


class _FakeImage:
    def __init__(self, *, size=(1280, 720)) -> None:
        self.size = size

    def save(self, path: Path, _format: str) -> None:
        Path(path).write_bytes(b"fake-image")


@pytest.mark.asyncio
async def test_desktop_vision_runtime_uses_last_capture_for_ocr(monkeypatch, app_config) -> None:
    app_config.desktop_vision.enabled = True
    runtime = DesktopVisionRuntime(app_config)
    target = runtime.screenshots_dir / "desktop-test.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"fake-image")
    runtime._last_capture_path = target

    async def fake_ocr(_config, image_path: Path, *, prompt: str) -> str:
        assert image_path == target
        assert "desktop screenshot" in prompt.lower()
        return "x" * 13050

    monkeypatch.setattr("assistant.tools.desktop_vision_runtime.ocr_image_with_gemini", fake_ocr)

    result = await runtime.ocr_image()

    assert result["path"] == str(target)
    assert result["truncated"] is True
    assert len(result["content"]) == app_config.desktop_vision.max_ocr_characters


@pytest.mark.asyncio
async def test_desktop_vision_runtime_capture_active_window_saves_into_workspace(monkeypatch, app_config) -> None:
    app_config.desktop_vision.enabled = True
    runtime = DesktopVisionRuntime(app_config)

    monkeypatch.setattr(
        runtime,
        "_get_active_window_snapshot",
        lambda: {
            "window_id": "101",
            "title": "Visual Studio Code",
            "process_name": "Code",
            "executable_path": "C:/Code.exe",
            "is_minimized": False,
            "is_visible": True,
        },
    )
    monkeypatch.setattr(runtime, "_get_active_window_rect", lambda: (0, 0, 640, 480))
    monkeypatch.setattr(runtime, "_capture_image", lambda _bbox: _FakeImage(size=(640, 480)))

    result = await runtime.capture_active_window()

    assert result["scope"] == "window"
    assert result["width"] == 640
    assert result["height"] == 480
    assert result["active_window"]["title"] == "Visual Studio Code"
    assert Path(result["path"]).exists()


def test_desktop_vision_runtime_rejects_disabled_feature(app_config) -> None:
    runtime = DesktopVisionRuntime(app_config)

    with pytest.raises(RuntimeError, match="Desktop vision is not enabled"):
        runtime.ensure_available()
