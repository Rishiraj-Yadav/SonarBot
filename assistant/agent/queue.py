"""Agent queue with steering support."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Any


class QueueMode(str, Enum):
    STEER = "steer"
    FOLLOWUP = "followup"


@dataclass(slots=True)
class AgentRequest:
    connection_id: str
    session_key: str
    message: str
    request_id: str
    mode: QueueMode = QueueMode.STEER
    metadata: dict[str, Any] | None = None
    silent: bool = False
    system_suffix: str | None = None
    result_future: asyncio.Future[dict[str, Any]] | None = None


class AgentQueue:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[AgentRequest] = asyncio.Queue()
        self._buffer: deque[AgentRequest] = deque()

    async def put(self, request: AgentRequest) -> None:
        await self._queue.put(request)

    async def get(self) -> AgentRequest:
        if self._buffer:
            return self._buffer.popleft()
        return await self._queue.get()

    async def collect_steer_messages(self, session_key: str) -> list[AgentRequest]:
        matched: list[AgentRequest] = []
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item.session_key == session_key and item.mode is QueueMode.STEER:
                matched.append(item)
            else:
                self._buffer.append(item)
        return matched

    def pending_count(self) -> int:
        return self._queue.qsize() + len(self._buffer)
