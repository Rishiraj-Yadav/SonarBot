"""Cron scheduling for proactive tasks."""

from __future__ import annotations

from assistant.agent.queue import AgentRequest, QueueMode


class AutomationScheduler:
    def __init__(self, config, agent_loop) -> None:
        self.config = config
        self.agent_loop = agent_loop
        self.scheduler = None

    async def start(self) -> None:
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore
            from apscheduler.triggers.cron import CronTrigger  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("APScheduler is not installed.") from exc

        self.scheduler = AsyncIOScheduler()
        for index, job in enumerate(self.config.automation.cron_jobs):
            trigger = CronTrigger.from_crontab(job.schedule)
            self.scheduler.add_job(
                self.enqueue_cron_message,
                trigger=trigger,
                id=f"cron-{index}",
                kwargs={"message": job.message},
                replace_existing=True,
            )
        self.scheduler.start()

    async def stop(self) -> None:
        if self.scheduler is not None and self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    async def enqueue_cron_message(self, message: str) -> None:
        await self.agent_loop.enqueue(
            AgentRequest(
                connection_id="",
                session_key="main",
                message=message,
                request_id=f"cron-{hash(message)}",
                mode=QueueMode.FOLLOWUP,
                metadata={"source": "cron"},
            )
        )
