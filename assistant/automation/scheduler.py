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
            from apscheduler.triggers.date import DateTrigger  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("APScheduler is not installed.") from exc

        self.scheduler = AsyncIOScheduler()
        for index, job in enumerate(self.config.automation.cron_jobs):
            self._add_job(
                job_id=f"cron-{index}",
                schedule=job.schedule,
                rule_name=f"cron:{index}",
                message=job.message,
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
                user_id=str(job["user_id"]),
            )
        for reminder in await self.automation_engine.list_all_one_time_reminders():
            if bool(reminder.get("paused")) or bool(reminder.get("fired")):
                continue
            self._add_one_time_job(
                reminder_id=str(reminder["reminder_id"]),
                run_at=str(reminder["run_at"]),
                message=str(reminder["message"]),
                user_id=str(reminder["user_id"]),
            )
        for rule in await self.automation_engine.list_all_desktop_rules():
            if str(rule.get("trigger_type")) != "schedule" or bool(rule.get("paused")):
                continue
            self._add_desktop_job(
                rule_id=str(rule["rule_id"]),
                user_id=str(rule["user_id"]),
                schedule=str(rule["schedule"]),
            )
        for routine in await self.automation_engine.list_all_desktop_routines():
            if bool(routine.get("paused")):
                continue
            trigger_type = str(routine.get("trigger_type"))
            if trigger_type == "schedule":
                self._add_routine_schedule_job(
                    routine_id=str(routine["routine_id"]),
                    user_id=str(routine["user_id"]),
                    schedule=str(routine["schedule"]),
                )
            elif trigger_type == "reminder":
                self._add_routine_reminder_job(
                    routine_id=str(routine["routine_id"]),
                    user_id=str(routine["user_id"]),
                    run_at=str(routine["run_at"]),
                )
        for job in await self.automation_engine.list_report_jobs():
            if bool(job.get("paused")):
                continue
            if job.get("schedule"):
                self._add_report_job(
                    job_id=str(job["job_id"]),
                    schedule=str(job["schedule"]),
                )
            elif job.get("run_once_at"):
                self._add_report_one_shot(
                    job_id=str(job["job_id"]),
                    run_at=str(job["run_once_at"]),
                )
        self.scheduler.start()

    async def stop(self) -> None:
        if self.scheduler is not None and self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    async def enqueue_cron_message(self, rule_name: str, message: str, user_id: str | None = None) -> None:
        await self.automation_engine.handle_cron_job(rule_name, message, user_id=user_id)

    async def register_dynamic_job(self, job: dict[str, object]) -> None:
        if self.scheduler is None:
            return
        self._add_job(
            job_id=self._dynamic_job_id(str(job["cron_id"])),
            schedule=str(job["schedule"]),
            rule_name=self._dynamic_rule_name(str(job["cron_id"])),
            message=str(job["message"]),
            user_id=str(job["user_id"]),
        )

    async def register_one_time_reminder(self, reminder: dict[str, object]) -> None:
        if self.scheduler is None or bool(reminder.get("paused")) or bool(reminder.get("fired")):
            return
        self._add_one_time_job(
            reminder_id=str(reminder["reminder_id"]),
            run_at=str(reminder["run_at"]),
            message=str(reminder["message"]),
            user_id=str(reminder["user_id"]),
        )

    async def register_report_job(self, job) -> None:
        if self.scheduler is None or bool(getattr(job, "paused", False)):
            return
        if getattr(job, "schedule", None):
            self._add_report_job(job_id=str(job.job_id), schedule=str(job.schedule))
        elif getattr(job, "run_once_at", None):
            self._add_report_one_shot(job_id=str(job.job_id), run_at=str(job.run_once_at))

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
            user_id=str(job["user_id"]),
        )

    async def remove_dynamic_job(self, cron_id: str) -> None:
        if self.scheduler is None:
            return
        job = self.scheduler.get_job(self._dynamic_job_id(cron_id))
        if job is not None:
            self.scheduler.remove_job(self._dynamic_job_id(cron_id))

    async def remove_one_time_reminder(self, reminder_id: str) -> None:
        if self.scheduler is None:
            return
        job = self.scheduler.get_job(self._one_time_job_id(reminder_id))
        if job is not None:
            self.scheduler.remove_job(self._one_time_job_id(reminder_id))

    async def remove_report_job(self, job_id: str) -> None:
        if self.scheduler is None:
            return
        job = self.scheduler.get_job(self._report_job_id(job_id))
        if job is not None:
            self.scheduler.remove_job(self._report_job_id(job_id))

    async def register_desktop_rule(self, rule: dict[str, object]) -> None:
        if self.scheduler is None or str(rule.get("trigger_type", "schedule")) != "schedule":
            return
        self._add_desktop_job(
            rule_id=str(rule["rule_id"]),
            user_id=str(rule["user_id"]),
            schedule=str(rule["schedule"]),
        )

    async def remove_desktop_rule(self, rule_id: str) -> None:
        if self.scheduler is None:
            return
        job = self.scheduler.get_job(self._desktop_job_id(rule_id))
        if job is not None:
            self.scheduler.remove_job(self._desktop_job_id(rule_id))

    async def register_desktop_routine(self, routine: dict[str, object]) -> None:
        if self.scheduler is None or bool(routine.get("paused")):
            return
        trigger_type = str(routine.get("trigger_type", "manual"))
        if trigger_type == "schedule":
            self._add_routine_schedule_job(
                routine_id=str(routine["routine_id"]),
                user_id=str(routine["user_id"]),
                schedule=str(routine["schedule"]),
            )
        elif trigger_type == "reminder":
            self._add_routine_reminder_job(
                routine_id=str(routine["routine_id"]),
                user_id=str(routine["user_id"]),
                run_at=str(routine["run_at"]),
            )

    async def remove_desktop_routine(self, routine_id: str) -> None:
        if self.scheduler is None:
            return
        for job_id in (self._routine_schedule_job_id(routine_id), self._routine_reminder_job_id(routine_id)):
            job = self.scheduler.get_job(job_id)
            if job is not None:
                self.scheduler.remove_job(job_id)

    def _add_job(
        self,
        *,
        job_id: str,
        schedule: str,
        rule_name: str,
        message: str,
        user_id: str | None,
    ) -> None:
        from apscheduler.triggers.cron import CronTrigger  # type: ignore

        trigger = CronTrigger.from_crontab(schedule)
        self.scheduler.add_job(
            self.enqueue_cron_message,
            trigger=trigger,
            id=job_id,
            kwargs={"rule_name": rule_name, "message": message, "user_id": user_id},
            replace_existing=True,
        )

    def _add_one_time_job(
        self,
        *,
        reminder_id: str,
        run_at: str,
        message: str,
        user_id: str,
    ) -> None:
        from apscheduler.triggers.date import DateTrigger  # type: ignore
        from datetime import datetime

        trigger = DateTrigger(run_date=datetime.fromisoformat(run_at))
        self.scheduler.add_job(
            self.automation_engine.handle_one_time_reminder,
            trigger=trigger,
            id=self._one_time_job_id(reminder_id),
            kwargs={"reminder_id": reminder_id, "message": message, "user_id": user_id, "run_at": run_at},
            replace_existing=True,
        )

    def _add_report_job(self, job_id: str, schedule: str) -> None:
        from apscheduler.triggers.cron import CronTrigger  # type: ignore

        trigger = CronTrigger.from_crontab(schedule)
        self.scheduler.add_job(
            self.automation_engine.handle_report_job,
            trigger=trigger,
            id=self._report_job_id(job_id),
            kwargs={"job_id": job_id},
            replace_existing=True,
        )

    def _add_report_one_shot(self, job_id: str, run_at: str) -> None:
        from apscheduler.triggers.date import DateTrigger  # type: ignore
        from datetime import datetime

        trigger = DateTrigger(run_date=datetime.fromisoformat(run_at))
        self.scheduler.add_job(
            self.automation_engine.handle_report_job,
            trigger=trigger,
            id=self._report_job_id(job_id),
            kwargs={"job_id": job_id},
            replace_existing=True,
        )

    def _add_desktop_job(
        self,
        *,
        rule_id: str,
        user_id: str,
        schedule: str,
    ) -> None:
        from apscheduler.triggers.cron import CronTrigger  # type: ignore

        trigger = CronTrigger.from_crontab(schedule)
        self.scheduler.add_job(
            self.automation_engine.handle_desktop_schedule_rule,
            trigger=trigger,
            id=self._desktop_job_id(rule_id),
            kwargs={"rule_id": rule_id, "user_id": user_id},
            replace_existing=True,
        )

    def _add_routine_schedule_job(
        self,
        *,
        routine_id: str,
        user_id: str,
        schedule: str,
    ) -> None:
        from apscheduler.triggers.cron import CronTrigger  # type: ignore

        trigger = CronTrigger.from_crontab(schedule)
        self.scheduler.add_job(
            self.automation_engine.handle_desktop_routine_schedule_rule,
            trigger=trigger,
            id=self._routine_schedule_job_id(routine_id),
            kwargs={"routine_id": routine_id, "user_id": user_id},
            replace_existing=True,
        )

    def _add_routine_reminder_job(
        self,
        *,
        routine_id: str,
        user_id: str,
        run_at: str,
    ) -> None:
        from apscheduler.triggers.date import DateTrigger  # type: ignore
        from datetime import datetime

        trigger = DateTrigger(run_date=datetime.fromisoformat(run_at))
        self.scheduler.add_job(
            self.automation_engine.handle_desktop_routine_reminder,
            trigger=trigger,
            id=self._routine_reminder_job_id(routine_id),
            kwargs={"routine_id": routine_id, "user_id": user_id, "run_at": run_at},
            replace_existing=True,
        )

    def _dynamic_job_id(self, cron_id: str) -> str:
        return f"dynamic-cron-{cron_id}"

    def _dynamic_rule_name(self, cron_id: str) -> str:
        return f"dynamic-cron:{cron_id}"

    def _one_time_job_id(self, reminder_id: str) -> str:
        return f"one-time-{reminder_id}"

    def _desktop_job_id(self, rule_id: str) -> str:
        return f"desktop-{rule_id}"

    def _routine_schedule_job_id(self, routine_id: str) -> str:
        return f"routine-schedule-{routine_id}"

    def _routine_reminder_job_id(self, routine_id: str) -> str:
        return f"routine-reminder-{routine_id}"

    def _report_job_id(self, job_id: str) -> str:
        return f"report-job-{job_id}"
