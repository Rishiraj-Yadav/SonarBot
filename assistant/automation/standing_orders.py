"""Standing order parsing helpers."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from assistant.automation.models import AutomationRule


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

    async def compile_rules(self) -> list[AutomationRule]:
        rules = await self.read_rules()
        compiled: list[AutomationRule] = []
        for index, rule_text in enumerate(rules, start=1):
            slug = re.sub(r"[^a-zA-Z0-9]+", "-", rule_text.lower()).strip("-") or f"standing-order-{index}"
            compiled.append(
                AutomationRule(
                    name=f"standing-order:{slug}",
                    trigger="heartbeat",
                    prompt_or_skill=rule_text,
                    delivery_policy="primary",
                    action_policy="notify_first",
                    cooldown_seconds=3600,
                    dedupe_window_seconds=3600,
                    quiet_hours_behavior="queue",
                    severity="info",
                )
            )
        return compiled
