"""Daily memory digest generation."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from assistant.utils.logging import get_logger


LOGGER = get_logger("daily_digest")


class DailyDigestRunner:
    def __init__(self, config, model_provider, memory_manager, delivery) -> None:
        self.config = config
        self.model_provider = model_provider
        self.memory_manager = memory_manager
        self.delivery = delivery
        self.session_manager = None
        self.automation_scheduler = None
        self._standalone_scheduler = None

    def bind_runtime(self, *, session_manager=None, automation_scheduler=None) -> None:
        self.session_manager = session_manager
        self.automation_scheduler = automation_scheduler

    async def run(self, user_id: str | None = None) -> str:
        notes = await self._collect_notes()
        joined_notes = "\n\n".join(notes) or "No notes were available."
        prompt = (
            "You are a memory curator. Summarize the key events, decisions,\n"
            "and learnings from the following daily notes. Be concise.\n"
            "Extract: (a) What was accomplished, (b) Patterns or habits noticed,\n"
            "(c) Pending items or follow-ups, (d) One insight worth remembering.\n"
            "Use Markdown. Max 400 words.\n"
            f"Notes: {joined_notes}"
        )
        chunks: list[str] = []
        async for response in self.model_provider.complete(
            messages=[{"role": "user", "content": prompt}],
            system="You create short, useful personal memory digests.",
            tools=[],
            stream=False,
        ):
            if response.text:
                chunks.append(response.text)
        digest = "".join(chunks).strip() or "No daily digest could be generated."
        today_key = f"Daily Digest {datetime.now(timezone.utc).date().isoformat()}"
        await self.memory_manager.write_long_term(today_key, digest)
        if user_id and getattr(self.config.telegram, "bot_token", ""):
            try:
                await self.delivery.send_text(user_id, digest[:3000], channel_name="telegram")
            except Exception as exc:
                LOGGER.warning("daily_digest_telegram_failed", user_id=user_id, error=str(exc))
        return digest

    async def schedule_daily(self, hour: int = 8, minute: int = 0) -> None:
        job_id = "daily-memory-digest"
        if self.automation_scheduler is not None and getattr(self.automation_scheduler, "scheduler", None) is not None:
            from apscheduler.triggers.cron import CronTrigger  # type: ignore

            trigger = CronTrigger(hour=hour, minute=minute)
            self.automation_scheduler.scheduler.add_job(
                self.run,
                trigger=trigger,
                id=job_id,
                kwargs={"user_id": self.config.users.default_user_id},
                replace_existing=True,
            )
            return

        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore
            from apscheduler.triggers.cron import CronTrigger  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("APScheduler is not installed.") from exc

        if self._standalone_scheduler is None:
            self._standalone_scheduler = AsyncIOScheduler()
            self._standalone_scheduler.start()
        trigger = CronTrigger(hour=hour, minute=minute)
        self._standalone_scheduler.add_job(
            self.run,
            trigger=trigger,
            id=job_id,
            kwargs={"user_id": self.config.users.default_user_id},
            replace_existing=True,
        )

    async def _collect_notes(self) -> list[str]:
        notes: list[str] = []
        notes.extend(await self._collect_recent_session_summaries())
        notes.extend(await self._collect_memory_files())
        return notes

    async def _collect_recent_session_summaries(self) -> list[str]:
        if self.session_manager is None:
            return []
        sessions_dir = self.config.sessions_dir
        cutoff = datetime.now(timezone.utc) - timedelta(days=1)
        session_paths = await asyncio.to_thread(lambda: sorted(sessions_dir.glob("*/*.jsonl")))
        collected: list[str] = []
        for path in session_paths:
            try:
                modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
            if modified_at < cutoff:
                continue
            content = await asyncio.to_thread(path.read_text, encoding="utf-8")
            lines = [line for line in content.splitlines() if line.strip()]
            compaction_summaries: list[str] = []
            recent_messages: list[str] = []
            for line in lines:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("record_type") == "compaction":
                    summary_message = record.get("summary_message", {})
                    summary_text = str(summary_message.get("content", "")).strip()
                    if summary_text:
                        compaction_summaries.append(summary_text)
                elif record.get("record_type") == "message":
                    role = str(record.get("role", "")).lower()
                    content_text = str(record.get("content", "")).strip()
                    if role in {"user", "assistant"} and content_text:
                        recent_messages.append(f"{role}: {content_text}")
            if compaction_summaries:
                collected.append(f"## Session {path.stem}\n" + "\n".join(compaction_summaries[-3:]))
            elif recent_messages:
                collected.append(f"## Session {path.stem}\n" + "\n".join(recent_messages[-10:]))
        return collected

    async def _collect_memory_files(self) -> list[str]:
        daily_dir = self.config.agent.workspace_dir / "memory"
        cutoff = datetime.now(timezone.utc).date() - timedelta(days=7)
        memory_paths = await asyncio.to_thread(
            lambda: sorted(
                (
                    path
                    for path in daily_dir.glob("*.md")
                    if path.stem >= cutoff.isoformat()
                ),
                key=lambda item: item.name,
            )
        )
        notes: list[str] = []
        for path in memory_paths:
            try:
                text = await asyncio.to_thread(path.read_text, encoding="utf-8")
            except OSError:
                continue
            if text.strip():
                notes.append(f"## {path.stem}\n{text[:4000]}")
        return notes
