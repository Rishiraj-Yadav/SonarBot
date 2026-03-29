"""Persistent browser page monitors."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from assistant.automation.models import Notification, utc_now_iso


@dataclass(slots=True)
class BrowserMonitorService:
    config: Any
    runtime: Any
    dispatcher: Any
    _task: asyncio.Task[None] | None = None
    monitors_dir: Path = field(init=False)
    index_path: Path = field(init=False)

    def __post_init__(self) -> None:
        self.monitors_dir = Path(self.config.agent.workspace_dir) / "browser_monitors"
        self.monitors_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.monitors_dir / "watches.json"

    def _load(self) -> list[dict[str, Any]]:
        if not self.index_path.exists():
            return []
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        return list(payload) if isinstance(payload, list) else []

    def _save(self, items: list[dict[str, Any]]) -> None:
        self.index_path.write_text(json.dumps(items, indent=2), encoding="utf-8")

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if self._task is None or self._task.done():
            return
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)

    async def create_watch(self, user_id: str, url: str, condition: str) -> dict[str, Any]:
        watch_id = uuid4().hex[:12]
        baseline = await self._capture(url, watch_id)
        item = {
            "watch_id": watch_id,
            "user_id": user_id,
            "url": url,
            "condition": condition,
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
            "baseline_text_hash": baseline["text_hash"],
            "baseline_screenshot_hash": baseline["screenshot_hash"],
            "baseline_preview": baseline["preview"],
            "last_screenshot_path": baseline["screenshot_path"],
        }
        items = self._load()
        items.append(item)
        self._save(items)
        return item

    async def list_watches(self, user_id: str) -> list[dict[str, Any]]:
        return [item for item in self._load() if str(item.get("user_id", "")) == user_id]

    async def delete_watch(self, user_id: str, watch_id: str) -> bool:
        items = self._load()
        kept = [item for item in items if not (str(item.get("user_id", "")) == user_id and str(item.get("watch_id", "")) == watch_id)]
        if len(kept) == len(items):
            return False
        self._save(kept)
        return True

    async def run_checks(self) -> None:
        items = self._load()
        updated = list(items)
        changed = False
        for index, item in enumerate(items):
            try:
                capture = await self._capture(str(item.get("url", "")), str(item.get("watch_id", "")))
            except Exception:
                continue
            text_changed = capture["text_hash"] != str(item.get("baseline_text_hash", ""))
            screenshot_changed = capture["screenshot_hash"] != str(item.get("baseline_screenshot_hash", ""))
            if not text_changed and not screenshot_changed:
                continue
            notification = Notification(
                notification_id=uuid4().hex,
                user_id=str(item.get("user_id", self.config.users.default_user_id)),
                title="Browser watch changed",
                body=(
                    f"Browser watch triggered for {item.get('url')}.\n"
                    f"Condition: {item.get('condition', '(none specified)')}\n"
                    f"Preview: {capture['preview'][:800]}"
                ),
                source="browser-monitor",
                severity="info",
                delivery_mode="notify",
                status="queued",
                target_channels=[],
                metadata={"watch_id": item.get("watch_id"), "url": item.get("url")},
            )
            await self.dispatcher.dispatch(notification)
            updated[index] = {
                **item,
                "updated_at": utc_now_iso(),
                "baseline_text_hash": capture["text_hash"],
                "baseline_screenshot_hash": capture["screenshot_hash"],
                "baseline_preview": capture["preview"],
                "last_screenshot_path": capture["screenshot_path"],
            }
            changed = True
        if changed:
            self._save(updated)

    async def _run_loop(self) -> None:
        interval_seconds = max(60, int(self.config.automation.heartbeat_interval_minutes) * 60)
        while True:
            try:
                await self.run_checks()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            await asyncio.sleep(interval_seconds)

    async def _capture(self, url: str, watch_id: str) -> dict[str, str]:
        snapshot = await self.runtime.summarize_url_temporarily(
            url,
            headless=True,
            screenshot_name=f"monitor-{watch_id}.png",
        )
        summary = dict(snapshot.get("summary") or {})
        preview = str(summary.get("text", "")).strip()[:2000]
        screenshot_path = str(snapshot.get("screenshot_path", "") or "")
        screenshot_hash = ""
        if screenshot_path:
            candidate = Path(screenshot_path)
            if candidate.exists():
                screenshot_hash = hashlib.sha256(candidate.read_bytes()).hexdigest()
        return {
            "preview": preview,
            "text_hash": hashlib.sha256(preview.encode("utf-8")).hexdigest(),
            "screenshot_hash": screenshot_hash,
            "screenshot_path": screenshot_path,
        }
