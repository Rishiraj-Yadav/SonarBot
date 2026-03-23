"""Periodic heartbeat turns."""

from __future__ import annotations

import asyncio
from contextlib import suppress


class HeartbeatService:
    def __init__(self, config, agent_loop, automation_engine) -> None:
        self.config = config
        self.agent_loop = agent_loop
        self.automation_engine = automation_engine
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _loop(self) -> None:
        interval_seconds = max(1, self.config.automation.heartbeat_interval_minutes) * 60
        while True:
            await asyncio.sleep(interval_seconds)
            if not self.agent_loop.is_idle():
                continue
            await self.automation_engine.handle_heartbeat()
