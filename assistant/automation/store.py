"""Persistent storage for automation events, runs, notifications, and approvals."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite

from assistant.automation.models import (
    ApprovalRequest,
    AutomationEvent,
    AutomationRun,
    DesktopAutomationRule,
    DesktopRoutineRule,
    DynamicCronJob,
    Notification,
    OneTimeReminder,
    ReportJob,
    ReportResult,
    utc_now_iso,
)


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
                    paused INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS one_time_reminders (
                    reminder_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    run_at TEXT NOT NULL,
                    message TEXT NOT NULL,
                    paused INTEGER NOT NULL DEFAULT 0,
                    fired INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS desktop_automation_rules (
                    rule_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    trigger_type TEXT NOT NULL,
                    watch_path TEXT NOT NULL,
                    schedule TEXT NOT NULL,
                    event_types_json TEXT NOT NULL,
                    file_extensions_json TEXT NOT NULL,
                    filename_pattern TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    destination_path TEXT NOT NULL,
                    target_name_template TEXT NOT NULL,
                    content_template TEXT NOT NULL,
                    paused INTEGER NOT NULL DEFAULT 0,
                    cooldown_seconds INTEGER NOT NULL DEFAULT 30,
                    dedupe_window_seconds INTEGER NOT NULL DEFAULT 30,
                    delivery_policy TEXT NOT NULL DEFAULT 'primary',
                    severity TEXT NOT NULL DEFAULT 'info',
                    last_event_at TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS desktop_routine_rules (
                    routine_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    trigger_type TEXT NOT NULL,
                    steps_json TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    schedule TEXT NOT NULL,
                    run_at TEXT NOT NULL,
                    watch_path TEXT NOT NULL,
                    event_types_json TEXT NOT NULL,
                    file_extensions_json TEXT NOT NULL,
                    filename_pattern TEXT NOT NULL,
                    paused INTEGER NOT NULL DEFAULT 0,
                    cooldown_seconds INTEGER NOT NULL DEFAULT 30,
                    dedupe_window_seconds INTEGER NOT NULL DEFAULT 30,
                    delivery_policy TEXT NOT NULL DEFAULT 'primary',
                    severity TEXT NOT NULL DEFAULT 'info',
                    approval_mode TEXT NOT NULL DEFAULT 'ask_on_risky_step',
                    last_event_at TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS report_jobs (
                    job_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    output_format TEXT NOT NULL,
                    save_path TEXT NOT NULL,
                    deliver_via TEXT NOT NULL,
                    schedule TEXT NOT NULL,
                    run_once_at TEXT NOT NULL,
                    paused INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS report_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    save_path TEXT NOT NULL,
                    format TEXT NOT NULL,
                    byte_size INTEGER NOT NULL,
                    generated_at TEXT NOT NULL,
                    summary_preview TEXT NOT NULL
                )
                """
            )
            await db.commit()

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
                    cron_id, user_id, schedule, message, paused, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.cron_id,
                    job.user_id,
                    job.schedule,
                    job.message,
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
                SELECT cron_id, schedule, message, paused, created_at, updated_at
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
                            "paused": bool(row[3]),
                            "created_at": row[4],
                            "updated_at": row[5],
                        }
                    )
        return rows

    async def list_all_dynamic_cron_jobs(self, *, include_paused: bool = False, limit: int = 500) -> list[dict[str, Any]]:
        await self.initialize()
        rows: list[dict[str, Any]] = []
        query = """
                SELECT cron_id, user_id, schedule, message, paused, created_at, updated_at
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
                            "paused": bool(row[4]),
                            "created_at": row[5],
                            "updated_at": row[6],
                        }
                    )
        return rows

    async def get_dynamic_cron_job(self, user_id: str, cron_id: str) -> dict[str, Any] | None:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT cron_id, schedule, message, paused, created_at, updated_at
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
                "paused": bool(row[3]),
                "created_at": row[4],
                "updated_at": row[5],
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

    async def create_one_time_reminder(self, reminder: OneTimeReminder) -> None:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO one_time_reminders (
                    reminder_id, user_id, run_at, message, paused, fired, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reminder.reminder_id,
                    reminder.user_id,
                    reminder.run_at,
                    reminder.message,
                    int(reminder.paused),
                    int(reminder.fired),
                    reminder.created_at,
                    reminder.updated_at,
                ),
            )
            await db.commit()

    async def list_one_time_reminders(self, user_id: str, limit: int = 100) -> list[dict[str, Any]]:
        await self.initialize()
        rows: list[dict[str, Any]] = []
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT reminder_id, run_at, message, paused, fired, created_at, updated_at
                FROM one_time_reminders
                WHERE user_id = ?
                ORDER BY run_at ASC
                LIMIT ?
                """,
                (user_id, limit),
            ) as cursor:
                async for row in cursor:
                    rows.append(
                        {
                            "reminder_id": row[0],
                            "user_id": user_id,
                            "run_at": row[1],
                            "message": row[2],
                            "paused": bool(row[3]),
                            "fired": bool(row[4]),
                            "created_at": row[5],
                            "updated_at": row[6],
                        }
                    )
        return rows

    async def list_all_one_time_reminders(
        self,
        *,
        include_paused: bool = False,
        include_fired: bool = False,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        await self.initialize()
        rows: list[dict[str, Any]] = []
        conditions: list[str] = []
        params: list[Any] = []
        if not include_paused:
            conditions.append("paused = 0")
        if not include_fired:
            conditions.append("fired = 0")
        query = """
                SELECT reminder_id, user_id, run_at, message, paused, fired, created_at, updated_at
                FROM one_time_reminders
            """
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY run_at ASC LIMIT ?"
        params.append(limit)
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query, tuple(params)) as cursor:
                async for row in cursor:
                    rows.append(
                        {
                            "reminder_id": row[0],
                            "user_id": row[1],
                            "run_at": row[2],
                            "message": row[3],
                            "paused": bool(row[4]),
                            "fired": bool(row[5]),
                            "created_at": row[6],
                            "updated_at": row[7],
                        }
                    )
        return rows

    async def get_one_time_reminder(self, user_id: str, reminder_id: str) -> dict[str, Any] | None:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT reminder_id, run_at, message, paused, fired, created_at, updated_at
                FROM one_time_reminders
                WHERE user_id = ? AND reminder_id = ?
                """,
                (user_id, reminder_id),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                return None
            return {
                "reminder_id": row[0],
                "user_id": user_id,
                "run_at": row[1],
                "message": row[2],
                "paused": bool(row[3]),
                "fired": bool(row[4]),
                "created_at": row[5],
                "updated_at": row[6],
            }

    async def set_one_time_reminder_paused(self, user_id: str, reminder_id: str, paused: bool) -> dict[str, Any] | None:
        await self.initialize()
        updated_at = utc_now_iso()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE one_time_reminders
                SET paused = ?, updated_at = ?
                WHERE user_id = ? AND reminder_id = ?
                """,
                (int(paused), updated_at, user_id, reminder_id),
            )
            await db.commit()
        return await self.get_one_time_reminder(user_id, reminder_id)

    async def mark_one_time_reminder_fired(self, user_id: str, reminder_id: str) -> None:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE one_time_reminders
                SET fired = 1, updated_at = ?
                WHERE user_id = ? AND reminder_id = ?
                """,
                (utc_now_iso(), user_id, reminder_id),
            )
            await db.commit()

    async def delete_one_time_reminder(self, user_id: str, reminder_id: str) -> bool:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM one_time_reminders WHERE user_id = ? AND reminder_id = ?",
                (user_id, reminder_id),
            )
            await db.commit()
        return bool(cursor.rowcount)

    async def create_report_job(self, job: ReportJob) -> None:
        await self.initialize()
        updated_at = utc_now_iso()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO report_jobs (
                    job_id, user_id, topic, source_type, source_path, output_format, save_path,
                    deliver_via, schedule, run_once_at, paused, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.job_id,
                    job.user_id,
                    job.topic,
                    job.source_type.value,
                    job.source_path or "",
                    job.output_format.value,
                    job.save_path or "",
                    job.deliver_via,
                    job.schedule or "",
                    job.run_once_at or "",
                    int(job.paused),
                    job.created_at,
                    updated_at,
                ),
            )
            await db.commit()

    async def list_report_jobs(self, user_id: str, limit: int = 100) -> list[dict[str, Any]]:
        await self.initialize()
        rows: list[dict[str, Any]] = []
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT job_id, user_id, topic, source_type, source_path, output_format, save_path,
                       deliver_via, schedule, run_once_at, paused, created_at, updated_at
                FROM report_jobs
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ) as cursor:
                async for row in cursor:
                    rows.append(self._report_job_row_to_dict(row))
        return rows

    async def list_all_report_jobs(self, *, include_paused: bool = False, limit: int = 500) -> list[dict[str, Any]]:
        await self.initialize()
        rows: list[dict[str, Any]] = []
        query = (
            """
            SELECT job_id, user_id, topic, source_type, source_path, output_format, save_path,
                   deliver_via, schedule, run_once_at, paused, created_at, updated_at
            FROM report_jobs
            """
            + ("" if include_paused else " WHERE paused = 0")
            + " ORDER BY created_at DESC LIMIT ?"
        )
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query, (limit,)) as cursor:
                async for row in cursor:
                    rows.append(self._report_job_row_to_dict(row))
        return rows

    async def get_report_job(self, job_id: str, user_id: str | None = None) -> dict[str, Any] | None:
        await self.initialize()
        query = (
            """
            SELECT job_id, user_id, topic, source_type, source_path, output_format, save_path,
                   deliver_via, schedule, run_once_at, paused, created_at, updated_at
            FROM report_jobs
            WHERE job_id = ?
            """
        )
        params: tuple[Any, ...] = (job_id,)
        if user_id is not None:
            query += " AND user_id = ?"
            params = (job_id, user_id)
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query, params) as cursor:
                row = await cursor.fetchone()
        return self._report_job_row_to_dict(row) if row is not None else None

    async def set_report_job_paused(self, user_id: str, job_id: str, paused: bool) -> dict[str, Any] | None:
        await self.initialize()
        updated_at = utc_now_iso()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE report_jobs
                SET paused = ?, updated_at = ?
                WHERE user_id = ? AND job_id = ?
                """,
                (int(paused), updated_at, user_id, job_id),
            )
            await db.commit()
        return await self.get_report_job(job_id, user_id)

    async def delete_report_job(self, user_id: str, job_id: str) -> bool:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM report_jobs WHERE user_id = ? AND job_id = ?",
                (user_id, job_id),
            )
            await db.commit()
        return bool(cursor.rowcount)

    async def create_report_result(self, user_id: str, result: ReportResult) -> None:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO report_results (
                    job_id, user_id, topic, save_path, format, byte_size, generated_at, summary_preview
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.job_id,
                    user_id,
                    result.topic,
                    result.save_path,
                    result.format,
                    result.byte_size,
                    result.generated_at,
                    result.summary_preview,
                ),
            )
            await db.commit()

    def _report_job_row_to_dict(self, row: Any) -> dict[str, Any]:
        return {
            "job_id": row[0],
            "user_id": row[1],
            "topic": row[2],
            "source_type": row[3],
            "source_path": row[4],
            "output_format": row[5],
            "save_path": row[6],
            "deliver_via": row[7],
            "schedule": row[8],
            "run_once_at": row[9],
            "paused": bool(row[10]),
            "created_at": row[11],
            "updated_at": row[12],
        }

    async def create_desktop_rule(self, rule: DesktopAutomationRule) -> None:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO desktop_automation_rules (
                    rule_id, user_id, name, trigger_type, watch_path, schedule, event_types_json,
                    file_extensions_json, filename_pattern, action_type, destination_path,
                    target_name_template, content_template, paused, cooldown_seconds,
                    dedupe_window_seconds, delivery_policy, severity, last_event_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rule.rule_id,
                    rule.user_id,
                    rule.name,
                    rule.trigger_type,
                    rule.watch_path,
                    rule.schedule,
                    json.dumps(rule.event_types),
                    json.dumps(rule.file_extensions),
                    rule.filename_pattern,
                    rule.action_type,
                    rule.destination_path,
                    rule.target_name_template,
                    rule.content_template,
                    int(rule.paused),
                    rule.cooldown_seconds,
                    rule.dedupe_window_seconds,
                    rule.delivery_policy,
                    rule.severity,
                    rule.last_event_at,
                    rule.created_at,
                    rule.updated_at,
                ),
            )
            await db.commit()

    async def list_desktop_rules(self, user_id: str, limit: int = 100) -> list[dict[str, Any]]:
        await self.initialize()
        rows: list[dict[str, Any]] = []
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT rule_id, user_id, name, trigger_type, watch_path, schedule, event_types_json,
                       file_extensions_json, filename_pattern, action_type, destination_path,
                       target_name_template, content_template, paused, cooldown_seconds,
                       dedupe_window_seconds, delivery_policy, severity, last_event_at, created_at, updated_at
                FROM desktop_automation_rules
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ) as cursor:
                async for row in cursor:
                    rows.append(self._desktop_rule_row_to_dict(row))
        return rows

    async def list_all_desktop_rules(self, *, include_paused: bool = False, limit: int = 500) -> list[dict[str, Any]]:
        await self.initialize()
        rows: list[dict[str, Any]] = []
        query = (
            """
            SELECT rule_id, user_id, name, trigger_type, watch_path, schedule, event_types_json,
                   file_extensions_json, filename_pattern, action_type, destination_path,
                   target_name_template, content_template, paused, cooldown_seconds,
                   dedupe_window_seconds, delivery_policy, severity, last_event_at, created_at, updated_at
            FROM desktop_automation_rules
            """
            + ("" if include_paused else " WHERE paused = 0")
            + " ORDER BY created_at DESC LIMIT ?"
        )
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query, (limit,)) as cursor:
                async for row in cursor:
                    rows.append(self._desktop_rule_row_to_dict(row))
        return rows

    async def get_desktop_rule(self, user_id: str, rule_id: str) -> dict[str, Any] | None:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT rule_id, user_id, name, trigger_type, watch_path, schedule, event_types_json,
                       file_extensions_json, filename_pattern, action_type, destination_path,
                       target_name_template, content_template, paused, cooldown_seconds,
                       dedupe_window_seconds, delivery_policy, severity, last_event_at, created_at, updated_at
                FROM desktop_automation_rules
                WHERE user_id = ? AND rule_id = ?
                """,
                (user_id, rule_id),
            ) as cursor:
                row = await cursor.fetchone()
        return self._desktop_rule_row_to_dict(row) if row is not None else None

    async def set_desktop_rule_paused(self, user_id: str, rule_id: str, paused: bool) -> dict[str, Any] | None:
        await self.initialize()
        updated_at = utc_now_iso()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE desktop_automation_rules
                SET paused = ?, updated_at = ?
                WHERE user_id = ? AND rule_id = ?
                """,
                (int(paused), updated_at, user_id, rule_id),
            )
            await db.commit()
        return await self.get_desktop_rule(user_id, rule_id)

    async def update_desktop_rule_last_event(self, user_id: str, rule_id: str, last_event_at: str) -> None:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE desktop_automation_rules
                SET last_event_at = ?, updated_at = ?
                WHERE user_id = ? AND rule_id = ?
                """,
                (last_event_at, utc_now_iso(), user_id, rule_id),
            )
            await db.commit()

    async def delete_desktop_rule(self, user_id: str, rule_id: str) -> bool:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM desktop_automation_rules WHERE user_id = ? AND rule_id = ?",
                (user_id, rule_id),
            )
            await db.commit()
        return bool(cursor.rowcount)

    def _desktop_rule_row_to_dict(self, row: Any) -> dict[str, Any]:
        return {
            "rule_id": row[0],
            "user_id": row[1],
            "name": row[2],
            "trigger_type": row[3],
            "watch_path": row[4],
            "schedule": row[5],
            "event_types": json.loads(row[6] or "[]"),
            "file_extensions": json.loads(row[7] or "[]"),
            "filename_pattern": row[8],
            "action_type": row[9],
            "destination_path": row[10],
            "target_name_template": row[11],
            "content_template": row[12],
            "paused": bool(row[13]),
            "cooldown_seconds": row[14],
            "dedupe_window_seconds": row[15],
            "delivery_policy": row[16],
            "severity": row[17],
            "last_event_at": row[18],
            "created_at": row[19],
            "updated_at": row[20],
        }

    async def create_desktop_routine(self, rule: DesktopRoutineRule) -> None:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO desktop_routine_rules (
                    routine_id, user_id, name, trigger_type, steps_json, summary, schedule, run_at,
                    watch_path, event_types_json, file_extensions_json, filename_pattern, paused,
                    cooldown_seconds, dedupe_window_seconds, delivery_policy, severity, approval_mode,
                    last_event_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rule.routine_id,
                    rule.user_id,
                    rule.name,
                    rule.trigger_type,
                    json.dumps(rule.steps, ensure_ascii=False),
                    rule.summary,
                    rule.schedule,
                    rule.run_at,
                    rule.watch_path,
                    json.dumps(rule.event_types),
                    json.dumps(rule.file_extensions),
                    rule.filename_pattern,
                    int(rule.paused),
                    rule.cooldown_seconds,
                    rule.dedupe_window_seconds,
                    rule.delivery_policy,
                    rule.severity,
                    rule.approval_mode,
                    rule.last_event_at,
                    rule.created_at,
                    rule.updated_at,
                ),
            )
            await db.commit()

    async def list_desktop_routines(self, user_id: str, limit: int = 100) -> list[dict[str, Any]]:
        await self.initialize()
        rows: list[dict[str, Any]] = []
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT routine_id, user_id, name, trigger_type, steps_json, summary, schedule, run_at,
                       watch_path, event_types_json, file_extensions_json, filename_pattern, paused,
                       cooldown_seconds, dedupe_window_seconds, delivery_policy, severity, approval_mode,
                       last_event_at, created_at, updated_at
                FROM desktop_routine_rules
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ) as cursor:
                async for row in cursor:
                    rows.append(self._desktop_routine_row_to_dict(row))
        return rows

    async def list_all_desktop_routines(self, *, include_paused: bool = False, limit: int = 500) -> list[dict[str, Any]]:
        await self.initialize()
        rows: list[dict[str, Any]] = []
        query = (
            """
            SELECT routine_id, user_id, name, trigger_type, steps_json, summary, schedule, run_at,
                   watch_path, event_types_json, file_extensions_json, filename_pattern, paused,
                   cooldown_seconds, dedupe_window_seconds, delivery_policy, severity, approval_mode,
                   last_event_at, created_at, updated_at
            FROM desktop_routine_rules
            """
            + ("" if include_paused else " WHERE paused = 0")
            + " ORDER BY created_at DESC LIMIT ?"
        )
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query, (limit,)) as cursor:
                async for row in cursor:
                    rows.append(self._desktop_routine_row_to_dict(row))
        return rows

    async def get_desktop_routine(self, user_id: str, routine_id: str) -> dict[str, Any] | None:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT routine_id, user_id, name, trigger_type, steps_json, summary, schedule, run_at,
                       watch_path, event_types_json, file_extensions_json, filename_pattern, paused,
                       cooldown_seconds, dedupe_window_seconds, delivery_policy, severity, approval_mode,
                       last_event_at, created_at, updated_at
                FROM desktop_routine_rules
                WHERE user_id = ? AND routine_id = ?
                """,
                (user_id, routine_id),
            ) as cursor:
                row = await cursor.fetchone()
        return self._desktop_routine_row_to_dict(row) if row is not None else None

    async def set_desktop_routine_paused(self, user_id: str, routine_id: str, paused: bool) -> dict[str, Any] | None:
        await self.initialize()
        updated_at = utc_now_iso()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE desktop_routine_rules
                SET paused = ?, updated_at = ?
                WHERE user_id = ? AND routine_id = ?
                """,
                (int(paused), updated_at, user_id, routine_id),
            )
            await db.commit()
        return await self.get_desktop_routine(user_id, routine_id)

    async def update_desktop_routine_last_event(self, user_id: str, routine_id: str, last_event_at: str) -> None:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE desktop_routine_rules
                SET last_event_at = ?, updated_at = ?
                WHERE user_id = ? AND routine_id = ?
                """,
                (last_event_at, utc_now_iso(), user_id, routine_id),
            )
            await db.commit()

    async def delete_desktop_routine(self, user_id: str, routine_id: str) -> bool:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM desktop_routine_rules WHERE user_id = ? AND routine_id = ?",
                (user_id, routine_id),
            )
            await db.commit()
        return bool(cursor.rowcount)

    def _desktop_routine_row_to_dict(self, row: Any) -> dict[str, Any]:
        return {
            "routine_id": row[0],
            "user_id": row[1],
            "name": row[2],
            "trigger_type": row[3],
            "steps": json.loads(row[4] or "[]"),
            "summary": row[5],
            "schedule": row[6],
            "run_at": row[7],
            "watch_path": row[8],
            "event_types": json.loads(row[9] or "[]"),
            "file_extensions": json.loads(row[10] or "[]"),
            "filename_pattern": row[11],
            "paused": bool(row[12]),
            "cooldown_seconds": row[13],
            "dedupe_window_seconds": row[14],
            "delivery_policy": row[15],
            "severity": row[16],
            "approval_mode": row[17],
            "last_event_at": row[18],
            "created_at": row[19],
            "updated_at": row[20],
        }
