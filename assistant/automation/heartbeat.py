"""Periodic heartbeat turns."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from uuid import uuid4

from assistant.agent.queue import AgentRequest, QueueMode


class HeartbeatService:
    def __init__(self, config, agent_loop, standing_orders_manager) -> None:
        self.config = config
        self.agent_loop = agent_loop
        self.standing_orders_manager = standing_orders_manager
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
            system_suffix = await self.standing_orders_manager.build_system_suffix()
            await self.agent_loop.enqueue(
                AgentRequest(
                    connection_id="",
                    session_key="main",
                    message="[HEARTBEAT] Check standing orders and any pending tasks.",
                    request_id=f"heartbeat-{uuid4().hex}",
                    mode=QueueMode.FOLLOWUP,
                    silent=True,
                    system_suffix=system_suffix or None,
                    metadata={"source": "heartbeat"},
                )
            )
