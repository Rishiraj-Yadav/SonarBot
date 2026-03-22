"""Watch skill directories and refresh the registry on change."""

from __future__ import annotations

import asyncio
from pathlib import Path

from watchfiles import awatch


class SkillWatcher:
    def __init__(self, registry) -> None:
        self.registry = registry
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self.registry.refresh()
        if self._task is None:
            self._task = asyncio.create_task(self._watch())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _watch(self) -> None:
        watch_paths = [str(path) for path in self.registry.skill_dirs if Path(path).exists()]
        if not watch_paths:
            return
        async for _changes in awatch(*watch_paths):
            self.registry.refresh()
