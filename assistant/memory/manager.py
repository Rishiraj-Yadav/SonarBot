"""Two-layer memory management."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from assistant.memory.indexer import MemoryIndexer
from assistant.memory.search import MemorySearchEngine


class MemoryManager:
    def __init__(self, config) -> None:
        self.config = config
        self.workspace_dir = config.agent.workspace_dir
        self.daily_dir = self.workspace_dir / "memory"
        self.long_term_path = self.workspace_dir / "MEMORY.md"
        self.daily_dir.mkdir(parents=True, exist_ok=True)
        self.indexer = MemoryIndexer(config)
        self.search_engine = MemorySearchEngine(self, self.indexer)

    async def write_daily(self, content: str) -> dict[str, str]:
        timestamp = datetime.now(timezone.utc)
        path = self.daily_dir / f"{timestamp.date().isoformat()}.md"
        entry = f"\n## {timestamp.strftime('%H:%M:%S %Z')}\n{content.strip()}\n"

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(entry)

        await asyncio.to_thread(_write)
        await asyncio.to_thread(self.indexer.upsert_content, str(path.relative_to(self.workspace_dir)), content, timestamp)
        return {"path": str(path), "status": "written"}

    async def write_long_term(self, key: str, value: str) -> dict[str, str]:
        heading = f"## {key.strip()}"
        content = await asyncio.to_thread(self._read_long_term_raw)
        if not content.strip():
            content = "# Long-Term Memory\n"

        pattern = re.compile(rf"^##\s+{re.escape(key.strip())}\s*$", re.MULTILINE)
        replacement = f"{heading}\n{value.strip()}\n"

        if pattern.search(content):
            lines = content.splitlines()
            output: list[str] = []
            inside_target = False
            for line in lines:
                if line.startswith("## "):
                    if line.strip() == heading:
                        if not inside_target:
                            output.extend(replacement.strip().splitlines())
                            inside_target = True
                        continue
                    if inside_target:
                        inside_target = False
                if not inside_target:
                    output.append(line)
            content = "\n".join(output).rstrip() + "\n"
        else:
            content = content.rstrip() + "\n\n" + replacement

        await asyncio.to_thread(self.long_term_path.write_text, content, encoding="utf-8")
        await asyncio.to_thread(self.indexer.upsert_content, "MEMORY.md", value, datetime.now(timezone.utc))
        return {"path": str(self.long_term_path), "status": "updated", "key": key}

    async def read_today_and_yesterday(self) -> str:
        today = datetime.now(timezone.utc).date()
        yesterday = today - timedelta(days=1)
        parts = []
        for day in [yesterday, today]:
            path = self.daily_dir / f"{day.isoformat()}.md"
            if path.exists():
                content = await asyncio.to_thread(path.read_text, encoding="utf-8")
                parts.append(f"### {day.isoformat()}\n{content.strip()}")
        return "\n\n".join(parts).strip()

    async def read_long_term(self) -> str:
        content = await asyncio.to_thread(self._read_long_term_raw)
        if len(content) > 6000:
            return content[:6000] + "\n[truncated - use memory_search for more]"
        return content

    async def get_memory_file(self, selector: str) -> str:
        today = datetime.now(timezone.utc).date()
        mapping = {
            "today": self.daily_dir / f"{today.isoformat()}.md",
            "yesterday": self.daily_dir / f"{(today - timedelta(days=1)).isoformat()}.md",
            "longterm": self.long_term_path,
        }
        path = mapping[selector]
        if not path.exists():
            return ""
        return await asyncio.to_thread(path.read_text, encoding="utf-8")

    async def search(self, query: str, limit: int = 5) -> list[str]:
        return await asyncio.to_thread(self.search_engine.hybrid_search, query, limit)

    def iter_memory_documents(self) -> list[tuple[str, str]]:
        documents: list[tuple[str, str]] = []
        if self.long_term_path.exists():
            documents.append(("MEMORY.md", self.long_term_path.read_text(encoding="utf-8")))
        for path in sorted(self.daily_dir.glob("*.md")):
            documents.append((str(path.relative_to(self.workspace_dir)), path.read_text(encoding="utf-8")))
        return documents

    def _read_long_term_raw(self) -> str:
        if not self.long_term_path.exists():
            return "# Long-Term Memory\n"
        return self.long_term_path.read_text(encoding="utf-8")
