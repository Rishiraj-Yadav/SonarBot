"""Cron scheduling for proactive tasks."""

from __future__ import annotations

class AutomationScheduler:
    def __init__(self, config, automation_engine) -> None:
        self.config = config
        self.automation_engine = automation_engine
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
                kwargs={"rule_name": f"cron:{index}", "message": job.message},
                replace_existing=True,
            )
        self.scheduler.start()

    async def stop(self) -> None:
        if self.scheduler is not None and self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    async def enqueue_cron_message(self, rule_name: str, message: str) -> None:
        await self.automation_engine.handle_cron_job(rule_name, message)
