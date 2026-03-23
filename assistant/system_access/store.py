"""SQLite persistence for host approvals and file backups."""

from __future__ import annotations

import json
from typing import Any

import aiosqlite

from assistant.system_access.models import HostApprovalRequest, utc_now_iso


class SystemAccessStore:
    def __init__(self, config) -> None:
        self.config = config
        self.db_path = config.data_db_path

    async def initialize(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS host_approval_requests (
                    approval_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    session_key TEXT NOT NULL,
                    connection_id TEXT NOT NULL,
                    channel_name TEXT NOT NULL,
                    action_kind TEXT NOT NULL,
                    target_summary TEXT NOT NULL,
                    category TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    decided_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS host_file_backups (
                    backup_id TEXT PRIMARY KEY,
                    original_path TEXT NOT NULL,
                    backup_path TEXT NOT NULL,
                    action_kind TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    restored_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                )
                """
            )
            await db.commit()

    async def create_approval(self, approval: HostApprovalRequest) -> None:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO host_approval_requests (
                    approval_id, user_id, session_id, session_key, connection_id, channel_name,
                    action_kind, target_summary, category, status, payload_json, created_at, decided_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval.approval_id,
                    approval.user_id,
                    approval.session_id,
                    approval.session_key,
                    approval.connection_id,
                    approval.channel_name,
                    approval.action_kind,
                    approval.target_summary,
                    approval.category,
                    approval.status,
                    json.dumps(approval.payload, ensure_ascii=False),
                    approval.created_at,
                    approval.decided_at,
                    approval.expires_at,
                ),
            )
            await db.commit()

    async def update_approval_status(self, approval_id: str, status: str) -> None:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE host_approval_requests
                SET status = ?, decided_at = ?
                WHERE approval_id = ?
                """,
                (status, utc_now_iso(), approval_id),
            )
            await db.commit()

    async def get_approval(self, approval_id: str) -> dict[str, Any] | None:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT approval_id, user_id, session_id, session_key, connection_id, channel_name,
                       action_kind, target_summary, category, status, payload_json, created_at, decided_at, expires_at
                FROM host_approval_requests
                WHERE approval_id = ?
                """,
                (approval_id,),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "approval_id": row[0],
            "user_id": row[1],
            "session_id": row[2],
            "session_key": row[3],
            "connection_id": row[4],
            "channel_name": row[5],
            "action_kind": row[6],
            "target_summary": row[7],
            "category": row[8],
            "status": row[9],
            "payload": json.loads(row[10] or "{}"),
            "created_at": row[11],
            "decided_at": row[12],
            "expires_at": row[13],
        }

    async def list_approvals(self, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        await self.initialize()
        rows: list[dict[str, Any]] = []
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT approval_id, session_id, session_key, action_kind, target_summary, category,
                       status, payload_json, created_at, decided_at, expires_at
                FROM host_approval_requests
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ) as cursor:
                async for row in cursor:
                    rows.append(
                        {
                            "approval_id": row[0],
                            "session_id": row[1],
                            "session_key": row[2],
                            "action_kind": row[3],
                            "target_summary": row[4],
                            "category": row[5],
                            "status": row[6],
                            "payload": json.loads(row[7] or "{}"),
                            "created_at": row[8],
                            "decided_at": row[9],
                            "expires_at": row[10],
                        }
                    )
        return rows

    async def create_backup(
        self,
        backup_id: str,
        *,
        original_path: str,
        backup_path: str,
        action_kind: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO host_file_backups (
                    backup_id, original_path, backup_path, action_kind, created_at, restored_at, status, metadata_json
                ) VALUES (?, ?, ?, ?, ?, '', 'active', ?)
                """,
                (
                    backup_id,
                    original_path,
                    backup_path,
                    action_kind,
                    utc_now_iso(),
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
            await db.commit()

    async def get_backup(self, backup_id: str) -> dict[str, Any] | None:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT backup_id, original_path, backup_path, action_kind, created_at, restored_at, status, metadata_json
                FROM host_file_backups
                WHERE backup_id = ?
                """,
                (backup_id,),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "backup_id": row[0],
            "original_path": row[1],
            "backup_path": row[2],
            "action_kind": row[3],
            "created_at": row[4],
            "restored_at": row[5],
            "status": row[6],
            "metadata": json.loads(row[7] or "{}"),
        }

    async def mark_backup_restored(self, backup_id: str) -> None:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE host_file_backups
                SET restored_at = ?, status = 'restored'
                WHERE backup_id = ?
                """,
                (utc_now_iso(), backup_id),
            )
            await db.commit()
