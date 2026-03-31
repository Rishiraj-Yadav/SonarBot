"""Persistent storage for automation events, runs, notifications, and approvals."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite

from assistant.automation.models import ApprovalRequest, AutomationEvent, AutomationRun, DynamicCronJob, Notification, utc_now_iso


class AutomationStore:
    def __init__(self, config) -> None:
        self.config = config
        self.db_path = config.data_db_path

    async def initialize(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS automation_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    dedupe_key TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS automation_runs (
                    run_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    rule_name TEXT NOT NULL,
                    session_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    result_text TEXT NOT NULL,
                    notification_id TEXT,
                    approval_state TEXT NOT NULL,
                    error TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS automation_rule_state (
                    user_id TEXT NOT NULL,
                    rule_name TEXT NOT NULL,
                    paused INTEGER NOT NULL DEFAULT 0,
                    last_run_at TEXT NOT NULL DEFAULT '',
                    last_notification_at TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (user_id, rule_name)
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS notifications (
                    notification_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    source TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    delivery_mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    target_channels_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS notification_deliveries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    notification_id TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT NOT NULL,
                    attempted_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS approval_requests (
                    approval_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    decided_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS dynamic_cron_jobs (
                    cron_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    schedule TEXT NOT NULL,
                    message TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'direct',
                    trigger_type TEXT NOT NULL DEFAULT 'cron',
                    run_at TEXT NOT NULL DEFAULT '',
                    paused INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await self._ensure_dynamic_cron_columns(db)
            await db.commit()

    async def _ensure_dynamic_cron_columns(self, db: aiosqlite.Connection) -> None:
        async with db.execute("PRAGMA table_info(dynamic_cron_jobs)") as cursor:
            columns = [str(row[1]) async for row in cursor]
        if "mode" not in columns:
            await db.execute(
                """
                ALTER TABLE dynamic_cron_jobs
                ADD COLUMN mode TEXT NOT NULL DEFAULT 'direct'
                """
            )
        if "trigger_type" not in columns:
            await db.execute(
                """
                ALTER TABLE dynamic_cron_jobs
                ADD COLUMN trigger_type TEXT NOT NULL DEFAULT 'cron'
                """
            )
        if "run_at" not in columns:
            await db.execute(
                """
                ALTER TABLE dynamic_cron_jobs
                ADD COLUMN run_at TEXT NOT NULL DEFAULT ''
                """
            )

    async def record_event(self, event: AutomationEvent, status: str = "queued") -> None:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO automation_events (
                    event_id, event_type, user_id, source, payload_json, dedupe_key, priority, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.event_type,
                    event.user_id,
                    event.source,
                    json.dumps(event.payload, ensure_ascii=False),
                    event.dedupe_key,
                    event.priority,
                    status,
                    event.created_at,
                ),
            )
            await db.commit()

    async def update_event_status(self, event_id: str, status: str) -> None:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE automation_events SET status = ? WHERE event_id = ?",
                (status, event_id),
            )
            await db.commit()

    async def should_skip_for_dedupe(
        self,
        user_id: str,
        rule_name: str,
        dedupe_key: str,
        dedupe_window_seconds: int,
        cooldown_seconds: int,
    ) -> tuple[bool, str]:
        await self.initialize()
        now = datetime.now(timezone.utc)
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT created_at
                FROM automation_events
                WHERE user_id = ? AND source = ? AND dedupe_key = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (user_id, rule_name, dedupe_key),
            ) as cursor:
                dedupe_row = await cursor.fetchone()
            if dedupe_row is not None and dedupe_window_seconds > 0:
                created_at = datetime.fromisoformat(str(dedupe_row[0]))
                if now - created_at <= timedelta(seconds=dedupe_window_seconds):
                    return True, "dedupe"

            async with db.execute(
                """
                SELECT last_run_at, paused
                FROM automation_rule_state
                WHERE user_id = ? AND rule_name = ?
                """,
                (user_id, rule_name),
            ) as cursor:
                state_row = await cursor.fetchone()
            if state_row is not None:
                last_run_at, paused = state_row
                if paused:
                    return True, "paused"
                if last_run_at and cooldown_seconds > 0:
                    last_time = datetime.fromisoformat(str(last_run_at))
                    if now - last_time <= timedelta(seconds=cooldown_seconds):
                        return True, "cooldown"
        return False, ""

    async def create_run(self, run: AutomationRun) -> None:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO automation_runs (
                    run_id, event_id, user_id, rule_name, session_key, status, prompt, result_text,
                    notification_id, approval_state, error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.event_id,
                    run.user_id,
                    run.rule_name,
                    run.session_key,
                    run.status,
                    run.prompt,
                    run.result_text,
                    run.notification_id,
                    run.approval_state,
                    run.error,
                    run.created_at,
                    run.updated_at,
                ),
            )
            await db.execute(
                """
                INSERT INTO automation_rule_state (user_id, rule_name, paused, last_run_at, last_notification_at)
                VALUES (?, ?, 0, ?, '')
                ON CONFLICT(user_id, rule_name)
                DO UPDATE SET last_run_at = excluded.last_run_at
                """,
                (run.user_id, run.rule_name, run.created_at),
            )
            await db.commit()

    async def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        result_text: str = "",
        notification_id: str | None = None,
        approval_state: str = "not_required",
        error: str = "",
    ) -> None:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE automation_runs
                SET status = ?, result_text = ?, notification_id = ?, approval_state = ?, error = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (status, result_text, notification_id, approval_state, error, utc_now_iso(), run_id),
            )
            await db.commit()

    async def list_runs(self, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        await self.initialize()
        rows: list[dict[str, Any]] = []
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT run_id, event_id, rule_name, session_key, status, result_text, notification_id,
                       approval_state, error, created_at, updated_at
                FROM automation_runs
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ) as cursor:
                async for row in cursor:
                    rows.append(
                        {
                            "run_id": row[0],
                            "event_id": row[1],
                            "rule_name": row[2],
                            "session_key": row[3],
                            "status": row[4],
                            "result_text": row[5],
                            "notification_id": row[6],
                            "approval_state": row[7],
                            "error": row[8],
                            "created_at": row[9],
                            "updated_at": row[10],
                        }
                    )
        return rows

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT run_id, event_id, user_id, rule_name, session_key, status, prompt, result_text,
                       notification_id, approval_state, error, created_at, updated_at
                FROM automation_runs
                WHERE run_id = ?
                """,
                (run_id,),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                return None
            return {
                "run_id": row[0],
                "event_id": row[1],
                "user_id": row[2],
                "rule_name": row[3],
                "session_key": row[4],
                "status": row[5],
                "prompt": row[6],
                "result_text": row[7],
                "notification_id": row[8],
                "approval_state": row[9],
                "error": row[10],
                "created_at": row[11],
                "updated_at": row[12],
            }

    async def get_event(self, event_id: str) -> dict[str, Any] | None:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT event_id, event_type, user_id, source, payload_json, dedupe_key, priority, status, created_at
                FROM automation_events
                WHERE event_id = ?
                """,
                (event_id,),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                return None
            return {
                "event_id": row[0],
                "event_type": row[1],
                "user_id": row[2],
                "source": row[3],
                "payload": json.loads(row[4] or "{}"),
                "dedupe_key": row[5],
                "priority": row[6],
                "status": row[7],
                "created_at": row[8],
            }

    async def create_notification(self, notification: Notification) -> None:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO notifications (
                    notification_id, user_id, title, body, source, severity, delivery_mode, status,
                    target_channels_json, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    notification.notification_id,
                    notification.user_id,
                    notification.title,
                    notification.body,
                    notification.source,
                    notification.severity,
                    notification.delivery_mode,
                    notification.status,
                    json.dumps(notification.target_channels),
                    json.dumps(notification.metadata, ensure_ascii=False),
                    notification.created_at,
                    notification.updated_at,
                ),
            )
            await db.commit()

    async def update_notification_status(self, notification_id: str, status: str) -> None:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE notifications
                SET status = ?, updated_at = ?
                WHERE notification_id = ?
                """,
                (status, utc_now_iso(), notification_id),
            )
            await db.commit()

    async def record_delivery(
        self,
        notification_id: str,
        channel: str,
        recipient: str,
        status: str,
        error: str = "",
    ) -> None:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO notification_deliveries (
                    notification_id, channel, recipient, status, error, attempted_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (notification_id, channel, recipient, status, error, utc_now_iso()),
            )
            await db.commit()

    async def list_notifications(self, user_id: str, limit: int = 25) -> list[dict[str, Any]]:
        await self.initialize()
        rows: list[dict[str, Any]] = []
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT notification_id, title, body, source, severity, delivery_mode, status,
                       target_channels_json, metadata_json, created_at, updated_at
                FROM notifications
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ) as cursor:
                async for row in cursor:
                    rows.append(
                        {
                            "notification_id": row[0],
                            "title": row[1],
                            "body": row[2],
                            "source": row[3],
                            "severity": row[4],
                            "delivery_mode": row[5],
                            "status": row[6],
                            "target_channels": json.loads(row[7] or "[]"),
                            "metadata": json.loads(row[8] or "{}"),
                            "created_at": row[9],
                            "updated_at": row[10],
                        }
                    )
        return rows

    async def set_rule_paused(self, user_id: str, rule_name: str, paused: bool) -> None:
        await self.initialize()
        now = utc_now_iso()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO automation_rule_state (user_id, rule_name, paused, last_run_at, last_notification_at)
                VALUES (?, ?, ?, '', '')
                ON CONFLICT(user_id, rule_name)
                DO UPDATE SET paused = excluded.paused
                """,
                (user_id, rule_name, int(paused)),
            )
            await db.commit()

    async def list_rule_state(self, user_id: str) -> dict[str, dict[str, Any]]:
        await self.initialize()
        state: dict[str, dict[str, Any]] = {}
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT rule_name, paused, last_run_at, last_notification_at
                FROM automation_rule_state
                WHERE user_id = ?
                """,
                (user_id,),
            ) as cursor:
                async for rule_name, paused, last_run_at, last_notification_at in cursor:
                    state[str(rule_name)] = {
                        "paused": bool(paused),
                        "last_run_at": last_run_at,
                        "last_notification_at": last_notification_at,
                    }
        return state

    async def mark_rule_notified(self, user_id: str, rule_name: str) -> None:
        await self.initialize()
        now = utc_now_iso()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO automation_rule_state (user_id, rule_name, paused, last_run_at, last_notification_at)
                VALUES (?, ?, 0, '', ?)
                ON CONFLICT(user_id, rule_name)
                DO UPDATE SET last_notification_at = excluded.last_notification_at
                """,
                (user_id, rule_name, now),
            )
            await db.commit()

    async def create_approval(self, approval: ApprovalRequest) -> None:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO approval_requests (
                    approval_id, user_id, run_id, action, status, payload_json, created_at, decided_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval.approval_id,
                    approval.user_id,
                    approval.run_id,
                    approval.action,
                    approval.status,
                    json.dumps(approval.payload, ensure_ascii=False),
                    approval.created_at,
                    approval.decided_at,
                ),
            )
            await db.commit()

    async def decide_approval(self, approval_id: str, status: str) -> None:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE approval_requests
                SET status = ?, decided_at = ?
                WHERE approval_id = ?
                """,
                (status, utc_now_iso(), approval_id),
            )
            await db.commit()

    async def list_approvals(self, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        await self.initialize()
        rows: list[dict[str, Any]] = []
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT approval_id, run_id, action, status, payload_json, created_at, decided_at
                FROM approval_requests
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
                            "run_id": row[1],
                            "action": row[2],
                            "status": row[3],
                            "payload": json.loads(row[4] or "{}"),
                            "created_at": row[5],
                            "decided_at": row[6],
                        }
                    )
        return rows

    async def create_dynamic_cron_job(self, job: DynamicCronJob) -> None:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO dynamic_cron_jobs (
                    cron_id, user_id, schedule, message, mode, trigger_type, run_at, paused, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.cron_id,
                    job.user_id,
                    job.schedule,
                    job.message,
                    job.mode,
                    job.trigger_type,
                    job.run_at,
                    int(job.paused),
                    job.created_at,
                    job.updated_at,
                ),
            )
            await db.commit()

    async def list_dynamic_cron_jobs(self, user_id: str, limit: int = 100) -> list[dict[str, Any]]:
        await self.initialize()
        rows: list[dict[str, Any]] = []
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT cron_id, schedule, message, mode, trigger_type, run_at, paused, created_at, updated_at
                FROM dynamic_cron_jobs
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ) as cursor:
                async for row in cursor:
                    rows.append(
                        {
                            "cron_id": row[0],
                            "user_id": user_id,
                            "schedule": row[1],
                            "message": row[2],
                            "mode": row[3] or "direct",
                            "trigger_type": row[4] or "cron",
                            "run_at": row[5] or "",
                            "paused": bool(row[6]),
                            "created_at": row[7],
                            "updated_at": row[8],
                        }
                    )
        return rows

    async def list_all_dynamic_cron_jobs(self, *, include_paused: bool = False, limit: int = 500) -> list[dict[str, Any]]:
        await self.initialize()
        rows: list[dict[str, Any]] = []
        query = """
                SELECT cron_id, user_id, schedule, message, mode, trigger_type, run_at, paused, created_at, updated_at
                FROM dynamic_cron_jobs
            """
        params: tuple[Any, ...]
        if include_paused:
            query += " ORDER BY created_at ASC LIMIT ?"
            params = (limit,)
        else:
            query += " WHERE paused = 0 ORDER BY created_at ASC LIMIT ?"
            params = (limit,)
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query, params) as cursor:
                async for row in cursor:
                    rows.append(
                        {
                            "cron_id": row[0],
                            "user_id": row[1],
                            "schedule": row[2],
                            "message": row[3],
                            "mode": row[4] or "direct",
                            "trigger_type": row[5] or "cron",
                            "run_at": row[6] or "",
                            "paused": bool(row[7]),
                            "created_at": row[8],
                            "updated_at": row[9],
                        }
                    )
        return rows

    async def get_dynamic_cron_job(self, user_id: str, cron_id: str) -> dict[str, Any] | None:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT cron_id, schedule, message, mode, trigger_type, run_at, paused, created_at, updated_at
                FROM dynamic_cron_jobs
                WHERE user_id = ? AND cron_id = ?
                """,
                (user_id, cron_id),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                return None
            return {
                "cron_id": row[0],
                "user_id": user_id,
                "schedule": row[1],
                "message": row[2],
                "mode": row[3] or "direct",
                "trigger_type": row[4] or "cron",
                "run_at": row[5] or "",
                "paused": bool(row[6]),
                "created_at": row[7],
                "updated_at": row[8],
            }

    async def set_dynamic_cron_job_paused(self, user_id: str, cron_id: str, paused: bool) -> dict[str, Any] | None:
        await self.initialize()
        updated_at = utc_now_iso()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE dynamic_cron_jobs
                SET paused = ?, updated_at = ?
                WHERE user_id = ? AND cron_id = ?
                """,
                (int(paused), updated_at, user_id, cron_id),
            )
            await db.commit()
        return await self.get_dynamic_cron_job(user_id, cron_id)

    async def delete_dynamic_cron_job(self, user_id: str, cron_id: str) -> bool:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM dynamic_cron_jobs WHERE user_id = ? AND cron_id = ?",
                (user_id, cron_id),
            )
            await db.commit()
            return bool(cursor.rowcount)
