"""Persistent store for desktop coworker tasks and transcripts."""

from __future__ import annotations

import json
from typing import Any

import aiosqlite

from assistant.desktop_coworker.models import DesktopCoworkerTask, utc_now_iso


class DesktopCoworkerStore:
    def __init__(self, config) -> None:
        self.config = config
        self.db_path = config.data_db_path

    async def initialize(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS desktop_coworker_tasks (
                    task_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    session_key TEXT NOT NULL,
                    request_text TEXT NOT NULL,
                    status TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    current_step_index INTEGER NOT NULL DEFAULT 0,
                    total_steps INTEGER NOT NULL DEFAULT 0,
                    steps_json TEXT NOT NULL,
                    latest_state_json TEXT NOT NULL,
                    transcript_json TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL DEFAULT ''
                )
                """
            )
            await db.commit()

    async def create_task(self, task: DesktopCoworkerTask) -> None:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO desktop_coworker_tasks (
                    task_id, user_id, session_key, request_text, status, summary, current_step_index,
                    total_steps, steps_json, latest_state_json, transcript_json, error,
                    created_at, updated_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.task_id,
                    task.user_id,
                    task.session_key,
                    task.request_text,
                    task.status,
                    task.summary,
                    int(task.current_step_index),
                    len(task.steps),
                    json.dumps(task.steps, ensure_ascii=False),
                    json.dumps(task.latest_state, ensure_ascii=False),
                    json.dumps(task.transcript, ensure_ascii=False),
                    task.error,
                    task.created_at,
                    task.updated_at,
                    task.completed_at,
                ),
            )
            await db.commit()

    async def update_task(
        self,
        task_id: str,
        *,
        status: str | None = None,
        current_step_index: int | None = None,
        latest_state: dict[str, Any] | None = None,
        transcript: list[dict[str, Any]] | None = None,
        error: str | None = None,
        completed_at: str | None = None,
    ) -> dict[str, Any] | None:
        await self.initialize()
        existing = await self.get_task(task_id)
        if existing is None:
            return None
        next_status = status if status is not None else str(existing["status"])
        next_index = int(current_step_index if current_step_index is not None else existing["current_step_index"])
        next_state = latest_state if latest_state is not None else dict(existing.get("latest_state", {}))
        next_transcript = transcript if transcript is not None else list(existing.get("transcript", []))
        next_error = error if error is not None else str(existing.get("error", ""))
        next_completed = completed_at if completed_at is not None else str(existing.get("completed_at", ""))
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE desktop_coworker_tasks
                SET status = ?, current_step_index = ?, latest_state_json = ?, transcript_json = ?,
                    error = ?, updated_at = ?, completed_at = ?
                WHERE task_id = ?
                """,
                (
                    next_status,
                    next_index,
                    json.dumps(next_state, ensure_ascii=False),
                    json.dumps(next_transcript, ensure_ascii=False),
                    next_error,
                    utc_now_iso(),
                    next_completed,
                    task_id,
                ),
            )
            await db.commit()
        return await self.get_task(task_id)

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT task_id, user_id, session_key, request_text, status, summary, current_step_index,
                       total_steps, steps_json, latest_state_json, transcript_json, error,
                       created_at, updated_at, completed_at
                FROM desktop_coworker_tasks
                WHERE task_id = ?
                """,
                (task_id,),
            ) as cursor:
                row = await cursor.fetchone()
        return self._row_to_dict(row) if row is not None else None

    async def list_tasks(self, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        await self.initialize()
        rows: list[dict[str, Any]] = []
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT task_id, user_id, session_key, request_text, status, summary, current_step_index,
                       total_steps, steps_json, latest_state_json, transcript_json, error,
                       created_at, updated_at, completed_at
                FROM desktop_coworker_tasks
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ) as cursor:
                async for row in cursor:
                    rows.append(self._row_to_dict(row))
        return rows

    async def stop_task(self, task_id: str) -> dict[str, Any] | None:
        return await self.update_task(task_id, status="stopped", completed_at=utc_now_iso())

    def _row_to_dict(self, row: Any) -> dict[str, Any]:
        return {
            "task_id": row[0],
            "user_id": row[1],
            "session_key": row[2],
            "request_text": row[3],
            "status": row[4],
            "summary": row[5],
            "current_step_index": int(row[6]),
            "total_steps": int(row[7]),
            "steps": json.loads(row[8] or "[]"),
            "latest_state": json.loads(row[9] or "{}"),
            "transcript": json.loads(row[10] or "[]"),
            "error": row[11],
            "created_at": row[12],
            "updated_at": row[13],
            "completed_at": row[14],
        }
