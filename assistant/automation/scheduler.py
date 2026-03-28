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
            self._add_job(
                job_id=f"cron-{index}",
                schedule=job.schedule,
                rule_name=f"cron:{index}",
                message=job.message,
                mode=getattr(job, "mode", "direct"),
                user_id=None,
            )
        for job in await self.automation_engine.list_all_dynamic_cron_jobs():
            if bool(job.get("paused")):
                continue
            self._add_job(
                job_id=self._dynamic_job_id(str(job["cron_id"])),
                schedule=str(job["schedule"]),
                rule_name=self._dynamic_rule_name(str(job["cron_id"])),
                message=str(job["message"]),
                mode=str(job.get("mode", "direct")),
                user_id=str(job["user_id"]),
            )
        self.scheduler.start()

    async def stop(self) -> None:
        if self.scheduler is not None and self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    async def enqueue_cron_message(
        self,
        rule_name: str,
        message: str,
        user_id: str | None = None,
        mode: str = "direct",
    ) -> None:
        await self.automation_engine.handle_cron_job(rule_name, message, user_id=user_id, mode=mode)

    async def register_dynamic_job(self, job: dict[str, object]) -> None:
        if self.scheduler is None:
            return
        self._add_job(
            job_id=self._dynamic_job_id(str(job["cron_id"])),
            schedule=str(job["schedule"]),
            rule_name=self._dynamic_rule_name(str(job["cron_id"])),
            message=str(job["message"]),
            mode=str(job.get("mode", "direct")),
            user_id=str(job["user_id"]),
        )

    async def pause_dynamic_job(self, cron_id: str) -> None:
        if self.scheduler is None:
            return
        job = self.scheduler.get_job(self._dynamic_job_id(cron_id))
        if job is not None:
            self.scheduler.remove_job(self._dynamic_job_id(cron_id))

    async def resume_dynamic_job(self, job: dict[str, object]) -> None:
        if self.scheduler is None or bool(job.get("paused")):
            return
        self._add_job(
            job_id=self._dynamic_job_id(str(job["cron_id"])),
            schedule=str(job["schedule"]),
            rule_name=self._dynamic_rule_name(str(job["cron_id"])),
            message=str(job["message"]),
            mode=str(job.get("mode", "direct")),
            user_id=str(job["user_id"]),
        )

    async def remove_dynamic_job(self, cron_id: str) -> None:
        if self.scheduler is None:
            return
        job = self.scheduler.get_job(self._dynamic_job_id(cron_id))
        if job is not None:
            self.scheduler.remove_job(self._dynamic_job_id(cron_id))

    def _add_job(
        self,
        *,
        job_id: str,
        schedule: str,
        rule_name: str,
        message: str,
        mode: str,
        user_id: str | None,
    ) -> None:
        from apscheduler.triggers.cron import CronTrigger  # type: ignore

        trigger = CronTrigger.from_crontab(schedule)
        self.scheduler.add_job(
            self.enqueue_cron_message,
            trigger=trigger,
            id=job_id,
            kwargs={"rule_name": rule_name, "message": message, "user_id": user_id, "mode": mode},
            replace_existing=True,
        )

    def _dynamic_job_id(self, cron_id: str) -> str:
        return f"dynamic-cron-{cron_id}"

    def _dynamic_rule_name(self, cron_id: str) -> str:
        return f"dynamic-cron:{cron_id}"
