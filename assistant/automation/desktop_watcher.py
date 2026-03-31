"""Polling-based desktop watcher for file automation."""

from __future__ import annotations

import asyncio
import fnmatch
from pathlib import Path
from typing import Any


class DesktopAutomationWatcher:
    def __init__(self, config, automation_engine) -> None:
        self.config = config
        self.automation_engine = automation_engine
        self._task: asyncio.Task[None] | None = None
        self._snapshot: dict[tuple[str, str], tuple[float, int]] = {}

    async def start(self) -> None:
        if not self.config.automation.desktop.enabled or not self.config.automation.desktop.watch_enabled:
            return
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def refresh_rules(self) -> None:
        self._snapshot = {}

    async def _run(self) -> None:
        interval = max(1, int(self.config.automation.desktop.poll_interval_seconds))
        while True:
            try:
                await self._scan_once()
            except Exception:
                pass
            await asyncio.sleep(interval)

    async def _scan_once(self) -> None:
        rules = await self.automation_engine.list_all_desktop_rules()
        watch_rules = [rule for rule in rules if str(rule.get("trigger_type")) == "file_watch" and not bool(rule.get("paused"))]
        new_snapshot: dict[tuple[str, str], tuple[float, int]] = {}
        for rule in watch_rules:
            watch_path = Path(str(rule.get("watch_path", "")))
            if not watch_path.exists() or not watch_path.is_dir():
                continue
            for child in watch_path.iterdir():
                if child.is_dir():
                    continue
                if self._should_ignore(child):
                    continue
                stat = child.stat()
                key = (str(rule.get("rule_id")), str(child))
                marker = (stat.st_mtime, stat.st_size)
                previous = self._snapshot.get(key)
                new_snapshot[key] = marker
                if previous is None:
                    await self.automation_engine.handle_desktop_watch_event(
                        rule_id=str(rule["rule_id"]),
                        user_id=str(rule["user_id"]),
                        event_type="file_created",
                        path=str(child),
                    )
                elif previous != marker:
                    await self.automation_engine.handle_desktop_watch_event(
                        rule_id=str(rule["rule_id"]),
                        user_id=str(rule["user_id"]),
                        event_type="file_modified",
                        path=str(child),
                    )
        self._snapshot = new_snapshot

    def _should_ignore(self, path: Path) -> bool:
        ignored = list(self.config.automation.desktop.ignored_patterns)
        name = path.name
        return any(fnmatch.fnmatch(name.lower(), pattern.lower()) for pattern in ignored)
