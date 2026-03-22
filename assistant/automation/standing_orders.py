"""Standing order parsing helpers."""

from __future__ import annotations

import asyncio
from pathlib import Path


class StandingOrdersManager:
    def __init__(self, workspace_dir: Path) -> None:
        self.path = workspace_dir / "STANDING_ORDERS.md"

    async def read_rules(self) -> list[str]:
        if not self.path.exists():
            return []
        content = await asyncio.to_thread(self.path.read_text, encoding="utf-8")
        return [line[1:].strip() for line in content.splitlines() if line.strip().startswith("-")]

    async def build_system_suffix(self) -> str:
        rules = await self.read_rules()
        if not rules:
            return ""
        joined = "\n".join(f"- {rule}" for rule in rules)
        return f"Active standing orders - evaluate each:\n{joined}"
