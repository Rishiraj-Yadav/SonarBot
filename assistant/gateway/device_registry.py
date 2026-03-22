"""Persist seen gateway devices."""

from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite


class DeviceRegistry:
    def __init__(self, config) -> None:
        self.config = config
        self.db_path = config.data_db_path

    async def initialize(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS devices (
                    device_id TEXT PRIMARY KEY,
                    approved INTEGER NOT NULL DEFAULT 1,
                    last_seen TEXT NOT NULL
                )
                """
            )
            await db.commit()

    async def seen(self, device_id: str) -> None:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO devices (device_id, approved, last_seen)
                VALUES (?, 1, ?)
                ON CONFLICT(device_id)
                DO UPDATE SET last_seen = excluded.last_seen
                """,
                (device_id, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()

    async def list_devices(self) -> list[dict[str, object]]:
        await self.initialize()
        rows: list[dict[str, object]] = []
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT device_id, approved, last_seen FROM devices ORDER BY last_seen DESC") as cursor:
                async for device_id, approved, last_seen in cursor:
                    rows.append({"device_id": device_id, "approved": bool(approved), "last_seen": last_seen})
        return rows

    async def approve(self, device_id: str) -> None:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE devices SET approved = 1 WHERE device_id = ?", (device_id,))
            await db.commit()

    async def revoke(self, device_id: str) -> None:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE devices SET approved = 0 WHERE device_id = ?", (device_id,))
            await db.commit()
