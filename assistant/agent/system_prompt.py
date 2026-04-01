"""Dynamic system prompt builder with file watching."""

from __future__ import annotations

import asyncio
from pathlib import Path

from assistant.skills.formatter import format_skills_for_prompt

from watchfiles import awatch

PROMPT_FILES = ["SOUL.md", "AGENTS.md", "USER.md", "IDENTITY.md", "TOOLS.md", "BOOT.md", "STANDING_ORDERS.md"]


class SystemPromptBuilder:
    def __init__(
        self,
        workspace_dir: Path,
        memory_manager=None,
        skill_registry=None,
        max_chars_per_file: int = 4000,
    ) -> None:
        self.workspace_dir = workspace_dir
        self.memory_manager = memory_manager
        self.skill_registry = skill_registry
        self.max_chars_per_file = max_chars_per_file
        self._cache: str | None = None
        self._lock = asyncio.Lock()
        self._watch_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._watch_task is None:
            self._watch_task = asyncio.create_task(self._watch_workspace())

    async def stop(self) -> None:
        if self._watch_task is None:
            return
        self._watch_task.cancel()
        try:
            await self._watch_task
        except asyncio.CancelledError:
            pass
        self._watch_task = None

    def invalidate(self) -> None:
        self._cache = None

    async def build(self) -> str:
        async with self._lock:
            if self._cache is None:
                sections = []
                for filename in PROMPT_FILES:
                    path = self.workspace_dir / filename
                    if not path.exists():
                        sections.append(f"## {filename}\n[missing]")
                        continue
                    content = await asyncio.to_thread(path.read_text, encoding="utf-8")
                    if len(content) > self.max_chars_per_file:
                        content = content[: self.max_chars_per_file] + "\n[truncated]"
                    sections.append(f"## {filename}\n{content.strip()}")

                self._cache = "\n\n".join(sections).strip()

            static_prompt = self._cache

        sections = [static_prompt]
        if self.memory_manager is not None:
            daily = await self.memory_manager.read_today_and_yesterday()
            long_term = await self.memory_manager.read_long_term()
            memory_section = (
                "## Memory Snapshot\n"
                f"### Recent Daily Memory\n{daily or '[empty]'}\n\n"
                f"### Long-Term Memory\n{long_term or '[empty]'}"
            )
            sections.append(memory_section)
        if self.skill_registry is not None:
            skills_xml = format_skills_for_prompt(self.skill_registry.list_enabled())
            sections.append(f"## Skills\n{skills_xml}")
        return "\n\n".join(section for section in sections if section).strip()

    async def _watch_workspace(self) -> None:
        async for changes in awatch(self.workspace_dir):
            if any(Path(path).name in PROMPT_FILES for _, path in changes):
                self.invalidate()
