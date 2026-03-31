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
            trigger_type = str(job.get("trigger_type", "cron"))
            self._add_job(
                job_id=self._dynamic_job_id(str(job["cron_id"])),
                rule_name=self._dynamic_rule_name(str(job["cron_id"]), trigger_type=trigger_type),
                message=str(job["message"]),
                mode=str(job.get("mode", "direct")),
                user_id=str(job["user_id"]),
                trigger_type=trigger_type,
                schedule=str(job.get("schedule", "")),
                run_at=str(job.get("run_at", "")),
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
            rule_name=self._dynamic_rule_name(str(job["cron_id"]), trigger_type=str(job.get("trigger_type", "cron"))),
            message=str(job["message"]),
            mode=str(job.get("mode", "direct")),
            user_id=str(job["user_id"]),
            trigger_type=str(job.get("trigger_type", "cron")),
            schedule=str(job.get("schedule", "")),
            run_at=str(job.get("run_at", "")),
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
            rule_name=self._dynamic_rule_name(str(job["cron_id"]), trigger_type=str(job.get("trigger_type", "cron"))),
            message=str(job["message"]),
            mode=str(job.get("mode", "direct")),
            user_id=str(job["user_id"]),
            trigger_type=str(job.get("trigger_type", "cron")),
            schedule=str(job.get("schedule", "")),
            run_at=str(job.get("run_at", "")),
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
        rule_name: str,
        message: str,
        mode: str,
        user_id: str | None,
        trigger_type: str = "cron",
        schedule: str = "",
        run_at: str = "",
    ) -> None:
        if trigger_type == "date":
            from apscheduler.triggers.date import DateTrigger  # type: ignore

            trigger = DateTrigger(run_date=run_at)
        else:
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

    def _dynamic_rule_name(self, cron_id: str, *, trigger_type: str = "cron") -> str:
        if trigger_type == "date":
            return f"dynamic-once:{cron_id}"
        return f"dynamic-cron:{cron_id}"
