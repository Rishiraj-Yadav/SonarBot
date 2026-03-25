"""Persistent user profiles and linked identities."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import aiosqlite


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class UserProfileStore:
    def __init__(self, config) -> None:
        self.config = config
        self.db_path = config.data_db_path

    async def initialize(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS user_profiles (
                    user_id TEXT PRIMARY KEY,
                    primary_channel TEXT NOT NULL,
                    fallback_channels_json TEXT NOT NULL,
                    quiet_hours_start TEXT NOT NULL,
                    quiet_hours_end TEXT NOT NULL,
                    notification_level TEXT NOT NULL,
                    automation_enabled INTEGER NOT NULL,
                    linked_channels_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS user_identity_links (
                    identity_type TEXT NOT NULL,
                    identity_value TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (identity_type, identity_value)
                )
                """
            )
            await db.commit()
        await self.ensure_default_user()

    async def ensure_default_user(self) -> None:
        await self.initialize_tables_only()
        now = utc_now_iso()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO user_profiles (
                    user_id,
                    primary_channel,
                    fallback_channels_json,
                    quiet_hours_start,
                    quiet_hours_end,
                    notification_level,
                    automation_enabled,
                    linked_channels_json,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    primary_channel = excluded.primary_channel,
                    fallback_channels_json = excluded.fallback_channels_json,
                    quiet_hours_start = excluded.quiet_hours_start,
                    quiet_hours_end = excluded.quiet_hours_end,
                    notification_level = excluded.notification_level,
                    automation_enabled = excluded.automation_enabled,
                    updated_at = excluded.updated_at
                """,
                (
                    self.config.users.default_user_id,
                    self.config.users.primary_channel,
                    json.dumps(self.config.users.fallback_channels),
                    self.config.users.quiet_hours_start,
                    self.config.users.quiet_hours_end,
                    self.config.users.notification_level,
                    int(self.config.users.automation_enabled),
                    json.dumps([]),
                    now,
                    now,
                ),
            )
            await db.commit()

    async def initialize_tables_only(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS user_profiles (
                    user_id TEXT PRIMARY KEY,
                    primary_channel TEXT NOT NULL,
                    fallback_channels_json TEXT NOT NULL,
                    quiet_hours_start TEXT NOT NULL,
                    quiet_hours_end TEXT NOT NULL,
                    notification_level TEXT NOT NULL,
                    automation_enabled INTEGER NOT NULL,
                    linked_channels_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS user_identity_links (
                    identity_type TEXT NOT NULL,
                    identity_value TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (identity_type, identity_value)
                )
                """
            )
            await db.commit()

    async def resolve_user_id(
        self,
        identity_type: str,
        identity_value: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT user_id FROM user_identity_links
                WHERE identity_type = ? AND identity_value = ?
                """,
                (identity_type, identity_value),
            ) as cursor:
                row = await cursor.fetchone()
            if row is not None:
                await self._touch_identity(db, identity_type, identity_value, metadata or {})
                return str(row[0])

            user_id = self.config.users.default_user_id
            if self.config.users.auto_link_single_user:
                await self._link_identity(db, user_id, identity_type, identity_value, metadata or {})
                return user_id
            return user_id

    async def get_profile(self, user_id: str) -> dict[str, Any]:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT
                    user_id,
                    primary_channel,
                    fallback_channels_json,
                    quiet_hours_start,
                    quiet_hours_end,
                    notification_level,
                    automation_enabled,
                    linked_channels_json
                FROM user_profiles
                WHERE user_id = ?
                """,
                (user_id,),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                raise KeyError(f"Unknown user '{user_id}'.")
            return {
                "user_id": row[0],
                "primary_channel": row[1],
                "fallback_channels": json.loads(row[2] or "[]"),
                "quiet_hours_start": row[3],
                "quiet_hours_end": row[4],
                "notification_level": row[5],
                "automation_enabled": bool(row[6]),
                "linked_channels": json.loads(row[7] or "[]"),
            }

    async def list_identities(self, user_id: str) -> list[dict[str, Any]]:
        await self.initialize()
        rows: list[dict[str, Any]] = []
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT identity_type, identity_value, metadata_json
                FROM user_identity_links
                WHERE user_id = ?
                ORDER BY identity_type, identity_value
                """,
                (user_id,),
            ) as cursor:
                async for identity_type, identity_value, metadata_json in cursor:
                    rows.append(
                        {
                            "identity_type": identity_type,
                            "identity_value": identity_value,
                            "metadata": json.loads(metadata_json or "{}"),
                        }
                    )
        return rows

    async def get_identity(self, user_id: str, identity_type: str) -> dict[str, Any] | None:
        identities = await self.list_identities(user_id)
        for item in identities:
            if item["identity_type"] == identity_type:
                return item
        return None

    async def list_user_ids(self) -> list[str]:
        await self.initialize()
        rows: list[str] = []
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT user_id
                FROM user_profiles
                ORDER BY user_id
                """
            ) as cursor:
                async for (user_id,) in cursor:
                    rows.append(str(user_id))
        return rows

    async def set_primary_channel(self, user_id: str, channel_name: str) -> None:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE user_profiles
                SET primary_channel = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (channel_name, utc_now_iso(), user_id),
            )
            await db.commit()

    async def _touch_identity(
        self,
        db: aiosqlite.Connection,
        identity_type: str,
        identity_value: str,
        metadata: dict[str, Any],
    ) -> None:
        await db.execute(
            """
            UPDATE user_identity_links
            SET metadata_json = ?, updated_at = ?
            WHERE identity_type = ? AND identity_value = ?
            """,
            (json.dumps(metadata), utc_now_iso(), identity_type, identity_value),
        )
        await db.commit()

    async def _link_identity(
        self,
        db: aiosqlite.Connection,
        user_id: str,
        identity_type: str,
        identity_value: str,
        metadata: dict[str, Any],
    ) -> None:
        now = utc_now_iso()
        await db.execute(
            """
            INSERT INTO user_identity_links (
                identity_type,
                identity_value,
                user_id,
                metadata_json,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(identity_type, identity_value)
            DO UPDATE SET user_id = excluded.user_id, metadata_json = excluded.metadata_json, updated_at = excluded.updated_at
            """,
            (identity_type, identity_value, user_id, json.dumps(metadata), now, now),
        )
        async with db.execute(
            """
            SELECT linked_channels_json FROM user_profiles WHERE user_id = ?
            """,
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
        linked_channels = json.loads(row[0] or "[]") if row else []
        channel_name = metadata.get("channel")
        if channel_name and channel_name not in linked_channels:
            linked_channels.append(channel_name)
            await db.execute(
                """
                UPDATE user_profiles
                SET linked_channels_json = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (json.dumps(sorted(linked_channels)), now, user_id),
            )
        await db.commit()
