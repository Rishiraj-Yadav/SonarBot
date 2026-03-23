"""JSONL audit trail for host actions."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

from assistant.system_access.models import HostAuditEntry


class SystemAccessAuditLogger:
    def __init__(self, config) -> None:
        self.path = config.system_access.audit_log_path

    async def append(self, entry: HostAuditEntry) -> None:
        payload = entry.to_dict()

        def _write() -> None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

        await asyncio.to_thread(_write)

    async def list_entries(
        self,
        *,
        session_id: str | None = None,
        today_only: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        def _read() -> list[dict[str, Any]]:
            if not self.path.exists():
                return []
            rows: list[dict[str, Any]] = []
            today = datetime.now().date()
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if session_id and payload.get("session_id") != session_id:
                        continue
                    if today_only:
                        try:
                            timestamp = datetime.fromisoformat(str(payload.get("timestamp")))
                        except ValueError:
                            continue
                        if timestamp.date() != today:
                            continue
                    rows.append(payload)
            rows.reverse()
            return rows[:limit]

        return await asyncio.to_thread(_read)
