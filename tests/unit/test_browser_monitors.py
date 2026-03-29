from __future__ import annotations

from pathlib import Path

import pytest

from assistant.browser_workflows.browser_monitors import BrowserMonitorService


class FakeBrowserRuntime:
    def __init__(self, workspace_dir: Path) -> None:
        self.workspace_dir = workspace_dir
        self.counter = 0
        self.preview = "Initial preview"

    async def summarize_url_temporarily(self, url: str, *, headless: bool = True, screenshot_name: str | None = None):
        self.counter += 1
        screenshots_dir = self.workspace_dir / "browser"
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = screenshots_dir / (screenshot_name or f"capture-{self.counter}.png")
        screenshot_path.write_bytes(f"shot-{self.counter}-{self.preview}".encode("utf-8"))
        return {
            "url": url,
            "summary": {"text": self.preview},
            "screenshot_path": str(screenshot_path),
        }


class FakeDispatcher:
    def __init__(self) -> None:
        self.notifications = []

    async def dispatch(self, notification):
        self.notifications.append(notification)
        notification.status = "delivered"
        return notification


@pytest.mark.asyncio
async def test_browser_monitor_service_create_list_and_delete_watch(app_config) -> None:
    runtime = FakeBrowserRuntime(app_config.agent.workspace_dir)
    dispatcher = FakeDispatcher()
    service = BrowserMonitorService(app_config, runtime, dispatcher)

    created = await service.create_watch("default", "https://example.com", "notify me when it changes")
    listed = await service.list_watches("default")
    deleted = await service.delete_watch("default", created["watch_id"])
    listed_after_delete = await service.list_watches("default")

    assert created["url"] == "https://example.com"
    assert listed[0]["watch_id"] == created["watch_id"]
    assert deleted is True
    assert listed_after_delete == []


@pytest.mark.asyncio
async def test_browser_monitor_service_dispatches_notification_when_capture_changes(app_config) -> None:
    runtime = FakeBrowserRuntime(app_config.agent.workspace_dir)
    dispatcher = FakeDispatcher()
    service = BrowserMonitorService(app_config, runtime, dispatcher)

    created = await service.create_watch("default", "https://example.com", "watch for changes")
    runtime.preview = "Updated preview text"

    await service.run_checks()

    assert len(dispatcher.notifications) == 1
    assert dispatcher.notifications[0].metadata["watch_id"] == created["watch_id"]
    stored = await service.list_watches("default")
    assert stored[0]["baseline_preview"] == "Updated preview text"
