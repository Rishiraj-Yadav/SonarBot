"""Advanced automation runtime."""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from assistant.agent.queue import AgentRequest, QueueMode
from assistant.automation.delivery import NotificationDispatcher
from assistant.automation.desktop_executor import DesktopAutomationExecutor
from assistant.automation.desktop_routine_executor import DesktopRoutineExecutor
from assistant.automation.models import (
    ApprovalRequest,
    AutomationEvent,
    AutomationRule,
    AutomationRun,
    DesktopAutomationRule,
    DesktopRoutineRule,
    DynamicCronJob,
    Notification,
    OneTimeReminder,
    ReportFormat,
    ReportJob,
    ReportResult,
    utc_now_iso,
)


class AutomationEngine:
    def __init__(
        self,
        config,
        agent_loop,
        session_manager,
        standing_orders_manager,
        user_profiles,
        store,
        dispatcher: NotificationDispatcher,
        system_access_manager=None,
        tool_registry=None,
    ) -> None:
        self.config = config
        self.agent_loop = agent_loop
        self.session_manager = session_manager
        self.standing_orders_manager = standing_orders_manager
        self.user_profiles = user_profiles
        self.store = store
        self.dispatcher = dispatcher
        self.scheduler = None
        self.system_access_manager = system_access_manager
        self.desktop_executor = DesktopAutomationExecutor(system_access_manager) if system_access_manager is not None else None
        self.tool_registry = tool_registry
        self.desktop_routine_executor = (
            DesktopRoutineExecutor(tool_registry, config) if tool_registry is not None else None
        )
        self.report_generator = None
        self.digest_runner = None

    async def initialize(self) -> None:
        await self.store.initialize()
        await self.user_profiles.initialize()

    def set_scheduler(self, scheduler) -> None:
        self.scheduler = scheduler
        if self.digest_runner is not None and hasattr(self.digest_runner, "bind_runtime"):
            self.digest_runner.bind_runtime(automation_scheduler=scheduler)

    def set_report_generator(self, report_generator) -> None:
        self.report_generator = report_generator

    def set_digest_runner(self, digest_runner) -> None:
        self.digest_runner = digest_runner
        if self.scheduler is not None and hasattr(digest_runner, "bind_runtime"):
            digest_runner.bind_runtime(automation_scheduler=self.scheduler)

    async def handle_cron_job(self, rule_name: str, message: str, user_id: str | None = None) -> dict[str, Any]:
        target_user = user_id or self.config.users.default_user_id
        rule = self._configured_rule(rule_name, "cron") or self._cron_rule(rule_name, message)
        event = self._build_event(
            event_type="cron",
            user_id=target_user,
            source=rule.name,
            payload={"message": message},
            dedupe_key=f"{rule.name}:{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M')}",
            priority=40,
        )
        return await self._run_event(event, rule, user_prompt=rule.prompt_or_skill or message)

    async def handle_heartbeat(self, user_id: str | None = None) -> dict[str, Any]:
        target_user = user_id or self.config.users.default_user_id
        compiled_rules = await self.standing_orders_manager.compile_rules()
        if not compiled_rules:
            return {"status": "skipped", "reason": "no-rules"}
        joined_rules = "\n".join(f"- {rule.prompt_or_skill}" for rule in compiled_rules)
        rule = AutomationRule(
            name="heartbeat:standing-orders",
            trigger="heartbeat",
            prompt_or_skill="Evaluate active standing orders and notify only when there is meaningful new information.",
            delivery_policy="primary",
            action_policy="notify_first",
            cooldown_seconds=300,
            dedupe_window_seconds=300,
            quiet_hours_behavior="queue",
            severity="info",
        )
        event = self._build_event(
            event_type="heartbeat",
            user_id=target_user,
            source=rule.name,
            payload={"standing_orders": [item.prompt_or_skill for item in compiled_rules]},
            dedupe_key=self._hash_payload(rule.name, {"standing_orders": [item.prompt_or_skill for item in compiled_rules]}),
            priority=60,
        )
        system_suffix = (
            "## Automation Mode\n"
            "You are running as SonarBot background automation. Gather information, summarize for the user, "
            "and avoid taking high-impact side effects. Reply with NO_REPLY if there is nothing new to report.\n\n"
            "## Active Standing Orders\n"
            f"{joined_rules}"
        )
        return await self._run_event(
            event,
            rule,
            user_prompt="[HEARTBEAT] Check standing orders and any pending tasks.",
            system_suffix=system_suffix,
        )

    async def handle_webhook(
        self,
        name: str,
        payload: dict[str, Any],
        message: str,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        target_user = user_id or self.config.users.default_user_id
        rule = self._configured_rule(f"webhook:{name}", f"webhook:{name}") or self._webhook_rule(name, message)
        event = self._build_event(
            event_type=f"webhook:{name}",
            user_id=target_user,
            source=rule.name,
            payload=payload,
            dedupe_key=self._hash_payload(rule.name, payload),
            priority=50,
        )
        return await self._run_event(event, rule, user_prompt=rule.prompt_or_skill or message)

    async def list_notifications(self, user_id: str) -> list[dict[str, Any]]:
        return await self.store.list_notifications(user_id, limit=50)

    async def list_runs(self, user_id: str) -> list[dict[str, Any]]:
        return await self.store.list_runs(user_id, limit=50)

    async def list_rules(self, user_id: str) -> list[dict[str, Any]]:
        state = await self.store.list_rule_state(user_id)
        dynamic_jobs = await self.store.list_dynamic_cron_jobs(user_id)
        rules = [
            *[self._rule_to_payload(self._cron_rule(f"cron:{index}", job.message), state.get(f"cron:{index}", {})) for index, job in enumerate(self.config.automation.cron_jobs)],
            *[self._rule_to_payload(rule, state.get(rule.name, {})) for rule in self.config.automation.rules],
            *[self._rule_to_payload(rule, state.get(rule.name, {})) for rule in await self.standing_orders_manager.compile_rules()],
        ]
        for job in dynamic_jobs:
            dynamic_rule = self._dynamic_cron_rule(str(job["cron_id"]), str(job["message"]))
            payload = self._rule_to_payload(dynamic_rule, state.get(dynamic_rule.name, {}))
            payload["schedule"] = str(job["schedule"])
            payload["message"] = str(job["message"])
            payload["paused"] = bool(job["paused"])
            payload["dynamic"] = True
            payload["cron_id"] = str(job["cron_id"])
            rules.append(payload)
        one_time_reminders = await self.store.list_one_time_reminders(user_id)
        for reminder in one_time_reminders:
            reminder_rule = self._one_time_reminder_rule(str(reminder["reminder_id"]), str(reminder["message"]))
            payload = self._rule_to_payload(reminder_rule, state.get(reminder_rule.name, {}))
            payload["run_at"] = str(reminder["run_at"])
            payload["message"] = str(reminder["message"])
            payload["paused"] = bool(reminder["paused"])
            payload["fired"] = bool(reminder["fired"])
            payload["one_time"] = True
            payload["reminder_id"] = str(reminder["reminder_id"])
            rules.append(payload)
        desktop_rules = await self.store.list_desktop_rules(user_id)
        for desktop_rule in desktop_rules:
            rules.append(self._desktop_rule_to_payload(desktop_rule, state.get(self._desktop_rule_name(str(desktop_rule["rule_id"])), {})))
        desktop_routines = await self.store.list_desktop_routines(user_id)
        for routine in desktop_routines:
            rules.append(
                self._desktop_routine_to_payload(
                    routine,
                    state.get(self._desktop_routine_rule_name(str(routine["routine_id"])), {}),
                )
            )
        webhook_names = sorted(self.config.automation.webhooks.keys())
        for webhook_name in webhook_names:
            rule = self._webhook_rule(webhook_name, f"Webhook event from {webhook_name}")
            rules.append(self._rule_to_payload(rule, state.get(rule.name, {})))
        aggregate = self._rule_to_payload(
            AutomationRule(
                name="heartbeat:standing-orders",
                trigger="heartbeat",
                prompt_or_skill="Aggregate standing order evaluation",
                cooldown_seconds=300,
                dedupe_window_seconds=300,
            ),
            state.get("heartbeat:standing-orders", {}),
        )
        rules.append(aggregate)
        return rules

    async def pause_rule(self, user_id: str, rule_name: str) -> None:
        if rule_name.startswith("dynamic-cron:"):
            await self.pause_dynamic_cron_job(user_id, rule_name.removeprefix("dynamic-cron:"))
            return
        if rule_name.startswith("desktop:"):
            await self.pause_desktop_rule(user_id, rule_name.removeprefix("desktop:"))
            return
        if rule_name.startswith("routine:"):
            await self.pause_desktop_routine(user_id, rule_name.removeprefix("routine:"))
            return
        await self.store.set_rule_paused(user_id, rule_name, True)

    async def resume_rule(self, user_id: str, rule_name: str) -> None:
        if rule_name.startswith("dynamic-cron:"):
            await self.resume_dynamic_cron_job(user_id, rule_name.removeprefix("dynamic-cron:"))
            return
        if rule_name.startswith("desktop:"):
            await self.resume_desktop_rule(user_id, rule_name.removeprefix("desktop:"))
            return
        if rule_name.startswith("routine:"):
            await self.resume_desktop_routine(user_id, rule_name.removeprefix("routine:"))
            return
        await self.store.set_rule_paused(user_id, rule_name, False)

    async def delete_rule(self, user_id: str, rule_name: str) -> bool:
        if rule_name.startswith("dynamic-cron:"):
            return await self.delete_dynamic_cron_job(user_id, rule_name.removeprefix("dynamic-cron:"))
        if rule_name.startswith("desktop:"):
            return await self.delete_desktop_rule(user_id, rule_name.removeprefix("desktop:"))
        if rule_name.startswith("routine:"):
            return await self.delete_desktop_routine(user_id, rule_name.removeprefix("routine:"))
        return False

    async def create_dynamic_cron_job(self, user_id: str, schedule: str, message: str) -> dict[str, Any]:
        normalized_schedule = self._validate_cron_schedule(schedule)
        cleaned_message = message.strip()
        if not cleaned_message:
            raise ValueError("Cron job message cannot be empty.")
        job = DynamicCronJob(
            cron_id=uuid4().hex[:12],
            user_id=user_id,
            schedule=normalized_schedule,
            message=cleaned_message,
        )
        await self.store.create_dynamic_cron_job(job)
        await self.store.set_rule_paused(user_id, self._dynamic_cron_rule_name(job.cron_id), False)
        if self.scheduler is not None:
            await self.scheduler.register_dynamic_job(
                {
                    "cron_id": job.cron_id,
                    "user_id": user_id,
                    "schedule": job.schedule,
                    "message": job.message,
                    "paused": False,
                }
            )
        return {
            "cron_id": job.cron_id,
            "user_id": job.user_id,
            "schedule": job.schedule,
            "message": job.message,
            "paused": job.paused,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
        }

    async def create_one_time_reminder(self, user_id: str, run_at: datetime, message: str) -> dict[str, Any]:
        target_time = run_at.astimezone(timezone.utc)
        if target_time <= datetime.now(timezone.utc):
            raise ValueError("One-time reminder time must be in the future.")
        cleaned_message = message.strip()
        if not cleaned_message:
            raise ValueError("Reminder message cannot be empty.")
        reminder = OneTimeReminder(
            reminder_id=uuid4().hex[:12],
            user_id=user_id,
            run_at=target_time.isoformat(),
            message=cleaned_message,
        )
        await self.store.create_one_time_reminder(reminder)
        await self.store.set_rule_paused(user_id, self._one_time_reminder_rule_name(reminder.reminder_id), False)
        if self.scheduler is not None:
            await self.scheduler.register_one_time_reminder(
                {
                    "reminder_id": reminder.reminder_id,
                    "user_id": user_id,
                    "run_at": reminder.run_at,
                    "message": reminder.message,
                    "paused": False,
                    "fired": False,
                }
            )
        return {
            "reminder_id": reminder.reminder_id,
            "user_id": reminder.user_id,
            "run_at": reminder.run_at,
            "message": reminder.message,
            "paused": reminder.paused,
            "fired": reminder.fired,
            "created_at": reminder.created_at,
            "updated_at": reminder.updated_at,
        }

    async def list_one_time_reminders(self, user_id: str) -> list[dict[str, Any]]:
        return await self.store.list_one_time_reminders(user_id)

    async def pause_one_time_reminder(self, user_id: str, reminder_id: str) -> dict[str, Any]:
        reminder = await self.store.set_one_time_reminder_paused(user_id, reminder_id, True)
        if reminder is None:
            raise KeyError(f"Unknown reminder '{reminder_id}'.")
        if self.scheduler is not None:
            await self.scheduler.remove_one_time_reminder(reminder_id)
        return reminder

    async def resume_one_time_reminder(self, user_id: str, reminder_id: str) -> dict[str, Any]:
        reminder = await self.store.set_one_time_reminder_paused(user_id, reminder_id, False)
        if reminder is None:
            raise KeyError(f"Unknown reminder '{reminder_id}'.")
        if self.scheduler is not None:
            await self.scheduler.register_one_time_reminder(reminder)
        return reminder

    async def delete_one_time_reminder(self, user_id: str, reminder_id: str) -> bool:
        deleted = await self.store.delete_one_time_reminder(user_id, reminder_id)
        if deleted and self.scheduler is not None:
            await self.scheduler.remove_one_time_reminder(reminder_id)
        return deleted

    async def create_report_job(self, job: ReportJob) -> ReportJob:
        normalized_job = ReportJob.model_validate(job.model_dump())
        if not normalized_job.topic.strip():
            raise ValueError("Report topic cannot be empty.")
        if normalized_job.schedule:
            normalized_job.schedule = self._validate_cron_schedule(normalized_job.schedule)
        if normalized_job.run_once_at:
            run_once_at = datetime.fromisoformat(normalized_job.run_once_at)
            if run_once_at.tzinfo is None:
                run_once_at = run_once_at.replace(tzinfo=timezone.utc)
            normalized_job.run_once_at = run_once_at.astimezone(timezone.utc).isoformat()
        if not normalized_job.output_format:
            normalized_job.output_format = ReportFormat(self.config.reports.default_format)
        if not normalized_job.deliver_via:
            normalized_job.deliver_via = self.config.reports.default_deliver_via
        await self.store.create_report_job(normalized_job)
        if self.scheduler is not None and not normalized_job.paused:
            await self.scheduler.register_report_job(normalized_job)
        return normalized_job

    async def list_report_jobs(self, user_id: str | None = None) -> list[dict]:
        if user_id is not None:
            return await self.store.list_report_jobs(user_id)
        return await self.store.list_all_report_jobs(include_paused=True)

    async def list_all_report_jobs(self) -> list[dict[str, Any]]:
        return await self.store.list_all_report_jobs(include_paused=False)

    async def pause_report_job(self, user_id: str, job_id: str) -> dict[str, Any]:
        job = await self.store.set_report_job_paused(user_id, job_id, True)
        if job is None:
            raise KeyError(f"Unknown report job '{job_id}'.")
        if self.scheduler is not None:
            await self.scheduler.remove_report_job(job_id)
        return job

    async def resume_report_job(self, user_id: str, job_id: str) -> dict[str, Any]:
        job = await self.store.set_report_job_paused(user_id, job_id, False)
        if job is None:
            raise KeyError(f"Unknown report job '{job_id}'.")
        if self.scheduler is not None:
            await self.scheduler.register_report_job(ReportJob.model_validate(job))
        return job

    async def delete_report_job(self, job_id: str, user_id: str | None = None) -> bool:
        job = await self.store.get_report_job(job_id, user_id)
        if job is None:
            return False
        deleted = await self.store.delete_report_job(str(job["user_id"]), job_id)
        if deleted and self.scheduler is not None:
            await self.scheduler.remove_report_job(job_id)
        return deleted

    async def run_report_job_now(self, job_id: str) -> ReportResult:
        if self.report_generator is None:
            raise RuntimeError("Report generator is not configured.")
        job_payload = await self.store.get_report_job(job_id)
        if job_payload is None:
            raise KeyError(f"Unknown report job '{job_id}'.")
        job = ReportJob.model_validate(job_payload)
        return await self.generate_report_now(job, notify_channel=True)

    async def generate_report_now(self, job: ReportJob, *, notify_channel: bool = False) -> ReportResult:
        if self.report_generator is None:
            raise RuntimeError("Report generator is not configured.")
        result = await self.report_generator.generate(job)
        await self.store.create_report_result(job.user_id, result)
        deliver_via = str(job.deliver_via or self.config.reports.default_deliver_via).lower()
        if notify_channel:
            await self.report_generator._deliver(result, job)
        elif deliver_via in {"memory", "all"}:
            delivery_job = job
            if deliver_via == "all":
                delivery_job = job.model_copy(update={"deliver_via": "memory"})
            await self.report_generator._deliver(result, delivery_job)
        return result

    async def handle_report_job(self, job_id: str) -> None:
        if self.report_generator is None:
            raise RuntimeError("Report generator is not configured.")
        job_payload = await self.store.get_report_job(job_id)
        if job_payload is None or bool(job_payload.get("paused")):
            return
        job = ReportJob.model_validate(job_payload)
        result = await self.generate_report_now(job, notify_channel=True)
        if job.run_once_at and not job.schedule:
            await self.store.set_report_job_paused(job.user_id, job.job_id, True)
            if self.scheduler is not None:
                await self.scheduler.remove_report_job(job.job_id)

    async def create_desktop_automation_rule(
        self,
        user_id: str,
        *,
        name: str,
        trigger_type: str,
        watch_path: str = "",
        schedule: str = "",
        event_types: list[str] | None = None,
        file_extensions: list[str] | None = None,
        filename_pattern: str = "*",
        action_type: str = "notify",
        destination_path: str = "",
        target_name_template: str = "",
        content_template: str = "",
        cooldown_seconds: int = 30,
        dedupe_window_seconds: int = 30,
        delivery_policy: str = "primary",
        severity: str = "info",
    ) -> dict[str, Any]:
        cleaned_name = name.strip()
        if not cleaned_name:
            raise ValueError("Desktop automation rule name cannot be empty.")
        if trigger_type not in {"file_watch", "schedule"}:
            raise ValueError("Desktop automation trigger_type must be 'file_watch' or 'schedule'.")
        if trigger_type == "file_watch" and not watch_path.strip():
            raise ValueError("Desktop file-watch rules require a watch path.")
        if trigger_type == "schedule":
            schedule = self._validate_cron_schedule(schedule)
        rule = DesktopAutomationRule(
            rule_id=uuid4().hex[:12],
            user_id=user_id,
            name=cleaned_name,
            trigger_type=trigger_type,
            watch_path=watch_path.strip(),
            schedule=schedule.strip(),
            event_types=event_types or ["file_created"],
            file_extensions=[item.strip().lstrip(".").lower() for item in (file_extensions or []) if item.strip()],
            filename_pattern=filename_pattern.strip() or "*",
            action_type=action_type,
            destination_path=destination_path.strip(),
            target_name_template=target_name_template.strip(),
            content_template=content_template.strip(),
            cooldown_seconds=max(0, int(cooldown_seconds)),
            dedupe_window_seconds=max(0, int(dedupe_window_seconds)),
            delivery_policy=delivery_policy,
            severity=severity,
        )
        await self.store.create_desktop_rule(rule)
        await self.store.set_rule_paused(user_id, self._desktop_rule_name(rule.rule_id), False)
        if self.scheduler is not None and rule.trigger_type == "schedule":
            await self.scheduler.register_desktop_rule(
                {
                    "rule_id": rule.rule_id,
                    "user_id": user_id,
                    "schedule": rule.schedule,
                    "name": rule.name,
                }
            )
        return await self.store.get_desktop_rule(user_id, rule.rule_id) or {}

    async def list_all_desktop_rules(self) -> list[dict[str, Any]]:
        return await self.store.list_all_desktop_rules(include_paused=False)

    async def create_desktop_routine_rule(
        self,
        user_id: str,
        *,
        name: str,
        trigger_type: str,
        steps: list[dict[str, Any]],
        summary: str = "",
        schedule: str = "",
        run_at: str = "",
        watch_path: str = "",
        event_types: list[str] | None = None,
        file_extensions: list[str] | None = None,
        filename_pattern: str = "*",
        cooldown_seconds: int = 30,
        dedupe_window_seconds: int = 30,
        delivery_policy: str = "primary",
        severity: str = "info",
        approval_mode: str = "ask_on_risky_step",
    ) -> dict[str, Any]:
        cleaned_name = name.strip()
        if not cleaned_name:
            raise ValueError("Desktop routine name cannot be empty.")
        if trigger_type not in {"manual", "schedule", "reminder", "file_watch"}:
            raise ValueError("Desktop routine trigger_type must be manual, schedule, reminder, or file_watch.")
        if not steps:
            raise ValueError("Desktop routines require at least one step.")
        normalized_schedule = ""
        normalized_run_at = ""
        normalized_watch_path = watch_path.strip()
        if trigger_type == "schedule":
            normalized_schedule = self._validate_cron_schedule(schedule)
        if trigger_type == "reminder":
            if not run_at.strip():
                raise ValueError("Reminder routines require run_at.")
            normalized_run_at = datetime.fromisoformat(run_at).astimezone(timezone.utc).isoformat()
        if trigger_type == "file_watch" and not normalized_watch_path:
            raise ValueError("File-watch routines require a watch path.")
        rule = DesktopRoutineRule(
            routine_id=uuid4().hex[:12],
            user_id=user_id,
            name=cleaned_name,
            trigger_type=trigger_type,
            steps=steps,
            summary=summary.strip() or self._routine_summary_from_steps(steps),
            schedule=normalized_schedule,
            run_at=normalized_run_at,
            watch_path=normalized_watch_path,
            event_types=event_types or (["file_created"] if trigger_type == "file_watch" else ["manual"]),
            file_extensions=[item.strip().lstrip(".").lower() for item in (file_extensions or []) if item.strip()],
            filename_pattern=filename_pattern.strip() or "*",
            cooldown_seconds=max(0, int(cooldown_seconds)),
            dedupe_window_seconds=max(0, int(dedupe_window_seconds)),
            delivery_policy=delivery_policy,
            severity=severity,
            approval_mode=approval_mode,
        )
        await self.store.create_desktop_routine(rule)
        await self.store.set_rule_paused(user_id, self._desktop_routine_rule_name(rule.routine_id), False)
        created = await self.store.get_desktop_routine(user_id, rule.routine_id) or {}
        if self.scheduler is not None and rule.trigger_type in {"schedule", "reminder"}:
            await self.scheduler.register_desktop_routine(created)
        return created

    async def list_all_desktop_routines(self) -> list[dict[str, Any]]:
        return await self.store.list_all_desktop_routines(include_paused=False)

    async def pause_desktop_routine(self, user_id: str, routine_id: str) -> dict[str, Any]:
        routine = await self.store.set_desktop_routine_paused(user_id, routine_id, True)
        if routine is None:
            raise KeyError(f"Unknown desktop routine '{routine_id}'.")
        await self.store.set_rule_paused(user_id, self._desktop_routine_rule_name(routine_id), True)
        if self.scheduler is not None:
            await self.scheduler.remove_desktop_routine(routine_id)
        return routine

    async def resume_desktop_routine(self, user_id: str, routine_id: str) -> dict[str, Any]:
        routine = await self.store.set_desktop_routine_paused(user_id, routine_id, False)
        if routine is None:
            raise KeyError(f"Unknown desktop routine '{routine_id}'.")
        await self.store.set_rule_paused(user_id, self._desktop_routine_rule_name(routine_id), False)
        if self.scheduler is not None and str(routine.get("trigger_type")) in {"schedule", "reminder"}:
            await self.scheduler.register_desktop_routine(routine)
        return routine

    async def delete_desktop_routine(self, user_id: str, routine_id: str) -> bool:
        deleted = await self.store.delete_desktop_routine(user_id, routine_id)
        if not deleted:
            raise KeyError(f"Unknown desktop routine '{routine_id}'.")
        await self.store.set_rule_paused(user_id, self._desktop_routine_rule_name(routine_id), True)
        if self.scheduler is not None:
            await self.scheduler.remove_desktop_routine(routine_id)
        return True

    async def run_desktop_routine_now(self, user_id: str, routine_id: str, *, notify: bool = False) -> dict[str, Any]:
        routine = await self.store.get_desktop_routine(user_id, routine_id)
        if routine is None:
            raise KeyError(f"Unknown desktop routine '{routine_id}'.")
        event = self._build_event(
            event_type="routine:manual",
            user_id=user_id,
            source=self._desktop_routine_rule_name(routine_id),
            payload={"trigger_type": "manual"},
            dedupe_key=f"{routine_id}:manual:{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')}",
            priority=58,
        )
        return await self._run_desktop_routine_event(event, routine, notify=notify)

    async def pause_desktop_rule(self, user_id: str, rule_id: str) -> dict[str, Any]:
        rule = await self.store.set_desktop_rule_paused(user_id, rule_id, True)
        if rule is None:
            raise KeyError(f"Unknown desktop rule '{rule_id}'.")
        await self.store.set_rule_paused(user_id, self._desktop_rule_name(rule_id), True)
        if self.scheduler is not None and str(rule.get("trigger_type")) == "schedule":
            await self.scheduler.remove_desktop_rule(rule_id)
        return rule

    async def resume_desktop_rule(self, user_id: str, rule_id: str) -> dict[str, Any]:
        rule = await self.store.set_desktop_rule_paused(user_id, rule_id, False)
        if rule is None:
            raise KeyError(f"Unknown desktop rule '{rule_id}'.")
        await self.store.set_rule_paused(user_id, self._desktop_rule_name(rule_id), False)
        if self.scheduler is not None and str(rule.get("trigger_type")) == "schedule":
            await self.scheduler.register_desktop_rule(rule)
        return rule

    async def delete_desktop_rule(self, user_id: str, rule_id: str) -> bool:
        deleted = await self.store.delete_desktop_rule(user_id, rule_id)
        if not deleted:
            raise KeyError(f"Unknown desktop rule '{rule_id}'.")
        await self.store.set_rule_paused(user_id, self._desktop_rule_name(rule_id), True)
        if self.scheduler is not None:
            await self.scheduler.remove_desktop_rule(rule_id)
        return True

    async def handle_desktop_watch_event(self, rule_id: str, user_id: str, event_type: str, path: str) -> dict[str, Any]:
        rule = await self.store.get_desktop_rule(user_id, rule_id)
        if rule is None or self.desktop_executor is None:
            return {"status": "skipped", "reason": "missing-rule"}
        if not self.desktop_executor.matches_event(rule, event_type=event_type, path=path):
            return {"status": "skipped", "reason": "filter"}
        event = self._build_event(
            event_type=f"desktop:{event_type}",
            user_id=user_id,
            source=self._desktop_rule_name(rule_id),
            payload={"path": path, "event_type": event_type, "trigger_type": "file_watch"},
            dedupe_key=f"{rule_id}:{event_type}:{path}",
            priority=55,
        )
        return await self._run_desktop_event(event, rule)

    async def handle_desktop_schedule_rule(self, rule_id: str, user_id: str) -> dict[str, Any]:
        rule = await self.store.get_desktop_rule(user_id, rule_id)
        if rule is None or self.desktop_executor is None:
            return {"status": "skipped", "reason": "missing-rule"}
        event = self._build_event(
            event_type="desktop:schedule",
            user_id=user_id,
            source=self._desktop_rule_name(rule_id),
            payload={"path": str(rule.get("watch_path", "")), "event_type": "scheduled", "trigger_type": "schedule"},
            dedupe_key=f"{rule_id}:{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M')}",
            priority=55,
        )
        return await self._run_desktop_event(event, rule)

    async def handle_desktop_routine_watch_event(self, routine_id: str, user_id: str, event_type: str, path: str) -> dict[str, Any]:
        routine = await self.store.get_desktop_routine(user_id, routine_id)
        if routine is None or self.desktop_routine_executor is None:
            return {"status": "skipped", "reason": "missing-routine"}
        if not self._routine_matches_event(routine, event_type=event_type, path=path):
            return {"status": "skipped", "reason": "filter"}
        event = self._build_event(
            event_type=f"routine:{event_type}",
            user_id=user_id,
            source=self._desktop_routine_rule_name(routine_id),
            payload={"path": path, "event_type": event_type, "trigger_type": "file_watch"},
            dedupe_key=f"{routine_id}:{event_type}:{path}",
            priority=56,
        )
        return await self._run_desktop_routine_event(event, routine)

    async def handle_desktop_routine_schedule_rule(self, routine_id: str, user_id: str) -> dict[str, Any]:
        routine = await self.store.get_desktop_routine(user_id, routine_id)
        if routine is None or self.desktop_routine_executor is None:
            return {"status": "skipped", "reason": "missing-routine"}
        event = self._build_event(
            event_type="routine:schedule",
            user_id=user_id,
            source=self._desktop_routine_rule_name(routine_id),
            payload={"trigger_type": "schedule"},
            dedupe_key=f"{routine_id}:{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M')}",
            priority=56,
        )
        return await self._run_desktop_routine_event(event, routine)

    async def handle_desktop_routine_reminder(self, routine_id: str, user_id: str, run_at: str) -> dict[str, Any]:
        routine = await self.store.get_desktop_routine(user_id, routine_id)
        if routine is None or self.desktop_routine_executor is None:
            return {"status": "skipped", "reason": "missing-routine"}
        event = self._build_event(
            event_type="routine:reminder",
            user_id=user_id,
            source=self._desktop_routine_rule_name(routine_id),
            payload={"trigger_type": "reminder", "run_at": run_at},
            dedupe_key=f"{routine_id}:{run_at}",
            priority=56,
        )
        return await self._run_desktop_routine_event(event, routine)

    async def list_dynamic_cron_jobs(self, user_id: str) -> list[dict[str, Any]]:
        return await self.store.list_dynamic_cron_jobs(user_id)

    async def list_all_dynamic_cron_jobs(self) -> list[dict[str, Any]]:
        return await self.store.list_all_dynamic_cron_jobs(include_paused=False)

    async def list_all_one_time_reminders(self) -> list[dict[str, Any]]:
        return await self.store.list_all_one_time_reminders(include_paused=False, include_fired=False)

    async def pause_dynamic_cron_job(self, user_id: str, cron_id: str) -> dict[str, Any]:
        job = await self.store.set_dynamic_cron_job_paused(user_id, cron_id, True)
        if job is None:
            raise KeyError(f"Unknown cron job '{cron_id}'.")
        await self.store.set_rule_paused(user_id, self._dynamic_cron_rule_name(cron_id), True)
        if self.scheduler is not None:
            await self.scheduler.pause_dynamic_job(cron_id)
        return job

    async def resume_dynamic_cron_job(self, user_id: str, cron_id: str) -> dict[str, Any]:
        job = await self.store.set_dynamic_cron_job_paused(user_id, cron_id, False)
        if job is None:
            raise KeyError(f"Unknown cron job '{cron_id}'.")
        await self.store.set_rule_paused(user_id, self._dynamic_cron_rule_name(cron_id), False)
        if self.scheduler is not None:
            await self.scheduler.resume_dynamic_job(job)
        return job

    async def delete_dynamic_cron_job(self, user_id: str, cron_id: str) -> bool:
        deleted = await self.store.delete_dynamic_cron_job(user_id, cron_id)
        if not deleted:
            raise KeyError(f"Unknown cron job '{cron_id}'.")
        await self.store.set_rule_paused(user_id, self._dynamic_cron_rule_name(cron_id), True)
        if self.scheduler is not None:
            await self.scheduler.remove_dynamic_job(cron_id)
        return True

    async def handle_one_time_reminder(self, reminder_id: str, message: str, user_id: str, run_at: str) -> dict[str, Any]:
        rule_name = self._one_time_reminder_rule_name(reminder_id)
        rule = self._one_time_reminder_rule(reminder_id, message)
        event = self._build_event(
            event_type="one-time",
            user_id=user_id,
            source=rule.name,
            payload={"message": message, "run_at": run_at},
            dedupe_key=f"{rule.name}:{run_at}",
            priority=45,
        )
        result = await self._run_event(event, rule, user_prompt=rule.prompt_or_skill or message)
        await self.store.mark_one_time_reminder_fired(user_id, reminder_id)
        return result

    async def replay_run(self, run_id: str) -> dict[str, Any]:
        run = await self.store.get_run(run_id)
        if run is None:
            raise KeyError(f"Unknown automation run '{run_id}'.")
        event = await self.store.get_event(str(run["event_id"]))
        if event is None:
            raise KeyError(f"Automation run '{run_id}' is missing its event payload.")
        rule_name = str(run["rule_name"])
        if rule_name.startswith("cron:"):
            return await self.handle_cron_job(rule_name, str(event["payload"].get("message", "")), user_id=str(run["user_id"]))
        if rule_name.startswith("dynamic-cron:"):
            return await self.handle_cron_job(rule_name, str(event["payload"].get("message", "")), user_id=str(run["user_id"]))
        if rule_name.startswith("one-time:"):
            return await self.handle_one_time_reminder(
                rule_name.removeprefix("one-time:"),
                str(event["payload"].get("message", "")),
                user_id=str(run["user_id"]),
                run_at=str(event["payload"].get("run_at", "")),
            )
        if rule_name.startswith("desktop:"):
            return await self.handle_desktop_schedule_rule(
                rule_name.removeprefix("desktop:"),
                user_id=str(run["user_id"]),
            )
        if rule_name.startswith("routine:"):
            return await self.run_desktop_routine_now(
                user_id=str(run["user_id"]),
                routine_id=rule_name.removeprefix("routine:"),
                notify=True,
            )
        if rule_name.startswith("webhook:"):
            return await self.handle_webhook(
                rule_name.removeprefix("webhook:"),
                dict(event["payload"]),
                str(event["payload"].get("message", run["prompt"])),
                user_id=str(run["user_id"]),
            )
        if rule_name.startswith("heartbeat:") or rule_name.startswith("standing-order:"):
            return await self.handle_heartbeat(user_id=str(run["user_id"]))
        raise KeyError(f"Replay is not supported for rule '{rule_name}'.")

    async def list_approvals(self, user_id: str) -> list[dict[str, Any]]:
        return await self.store.list_approvals(user_id, limit=50)

    async def decide_approval(self, approval_id: str, decision: str) -> None:
        await self.store.decide_approval(approval_id, decision)

    async def _run_event(
        self,
        event: AutomationEvent,
        rule: AutomationRule,
        *,
        user_prompt: str,
        system_suffix: str | None = None,
    ) -> dict[str, Any]:
        await self.initialize()
        should_skip, reason = await self.store.should_skip_for_dedupe(
            event.user_id,
            rule.name,
            event.dedupe_key,
            rule.dedupe_window_seconds,
            rule.cooldown_seconds,
        )
        await self.store.record_event(event, status="skipped" if should_skip else "queued")
        if should_skip:
            return {"status": "skipped", "reason": reason, "rule_name": rule.name}

        session_key = f"automation:{event.user_id}:{self._slug(rule.name)}"
        run = AutomationRun(
            run_id=uuid4().hex,
            event_id=event.event_id,
            user_id=event.user_id,
            rule_name=rule.name,
            session_key=session_key,
            status="running",
            prompt=user_prompt,
            created_at=utc_now_iso(),
            updated_at=utc_now_iso(),
        )
        await self.store.create_run(run)
        await self.store.update_event_status(event.event_id, "running")

        direct_notification_text = self._direct_notification_text(
            rule=rule,
            user_prompt=user_prompt,
            payload=event.payload,
        )
        if direct_notification_text is not None:
            if not direct_notification_text or direct_notification_text.upper() == "NO_REPLY":
                await self.store.finish_run(run.run_id, status="completed", result_text=direct_notification_text)
                await self.store.update_event_status(event.event_id, "completed")
                return {"status": "completed", "rule_name": rule.name, "notified": False}
            notification = Notification(
                notification_id=uuid4().hex,
                user_id=event.user_id,
                title=self._notification_title(rule, direct_notification_text),
                body=direct_notification_text,
                source=rule.name,
                severity=rule.severity or self.config.automation.notifications.default_severity,
                delivery_mode=rule.delivery_policy,
                status="queued",
                target_channels=[],
                metadata={
                    "rule_name": rule.name,
                    "event_id": event.event_id,
                    "delivery_policy": rule.delivery_policy,
                    "delivery_mode": "direct",
                },
            )
            delivered = await self.dispatcher.dispatch(notification)
            await self.store.finish_run(
                run.run_id,
                status="completed",
                result_text=direct_notification_text,
                notification_id=delivered.notification_id,
            )
            await self.store.update_event_status(event.event_id, "completed")
            return {"status": "completed", "notification_id": delivered.notification_id, "rule_name": rule.name}

        result_future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        request = AgentRequest(
            connection_id="",
            session_key=session_key,
            message=user_prompt,
            request_id=f"automation-{run.run_id}",
            mode=QueueMode.FOLLOWUP,
            metadata={
                "source": "automation",
                "automation_run_id": run.run_id,
                "automation_event_id": event.event_id,
                "user_id": event.user_id,
                "trace_id": uuid4().hex,
                "rule_name": rule.name,
            },
            silent=True,
            system_suffix=await self._build_system_suffix(event.user_id, rule, event.payload, system_suffix),
            result_future=result_future,
        )
        await self.agent_loop.enqueue(request)
        try:
            result = await asyncio.wait_for(result_future, timeout=120)
        except asyncio.TimeoutError:
            await self.store.finish_run(run.run_id, status="failed", error="Automation run timed out.")
            await self.store.update_event_status(event.event_id, "failed")
            return {"status": "failed", "error": "timeout", "rule_name": rule.name}

        assistant_text = await self._extract_notification_text(run.session_key, str(result.get("assistant_text", "")).strip())
        if not assistant_text or assistant_text.upper() == "NO_REPLY":
            await self.store.finish_run(run.run_id, status="completed", result_text=assistant_text)
            await self.store.update_event_status(event.event_id, "completed")
            return {"status": "completed", "rule_name": rule.name, "notified": False}

        if rule.action_policy != "notify_first" and self.config.automation.approvals.enabled:
            approval = ApprovalRequest(
                approval_id=uuid4().hex,
                user_id=event.user_id,
                run_id=run.run_id,
                action="automation-action",
                status="pending",
                payload={"rule_name": rule.name, "result_text": assistant_text},
            )
            await self.store.create_approval(approval)
            await self.store.finish_run(
                run.run_id,
                status="pending_approval",
                result_text=assistant_text,
                approval_state="pending",
            )
            await self.store.update_event_status(event.event_id, "pending_approval")
            return {"status": "pending_approval", "approval_id": approval.approval_id, "rule_name": rule.name}

        notification = Notification(
            notification_id=uuid4().hex,
            user_id=event.user_id,
            title=self._notification_title(rule, assistant_text),
            body=assistant_text,
            source=rule.name,
            severity=rule.severity or self.config.automation.notifications.default_severity,
            delivery_mode=rule.delivery_policy,
            status="queued",
            target_channels=[],
            metadata={"rule_name": rule.name, "event_id": event.event_id, "delivery_policy": rule.delivery_policy},
        )
        delivered = await self.dispatcher.dispatch(notification)
        await self.store.finish_run(
            run.run_id,
            status="completed",
            result_text=assistant_text,
            notification_id=delivered.notification_id,
        )
        await self.store.update_event_status(event.event_id, "completed")
        return {"status": "completed", "notification_id": delivered.notification_id, "rule_name": rule.name}

    async def _run_desktop_event(self, event: AutomationEvent, rule: dict[str, Any]) -> dict[str, Any]:
        should_skip, reason = await self.store.should_skip_for_dedupe(
            event.user_id,
            self._desktop_rule_name(str(rule["rule_id"])),
            event.dedupe_key,
            int(rule.get("dedupe_window_seconds", 30)),
            int(rule.get("cooldown_seconds", 30)),
        )
        await self.store.record_event(event, status="skipped" if should_skip else "queued")
        if should_skip:
            return {"status": "skipped", "reason": reason, "rule_name": self._desktop_rule_name(str(rule["rule_id"]))}
        session_key = f"automation:{event.user_id}:{self._slug(str(rule['name']))}"
        run = AutomationRun(
            run_id=uuid4().hex,
            event_id=event.event_id,
            user_id=event.user_id,
            rule_name=self._desktop_rule_name(str(rule["rule_id"])),
            session_key=session_key,
            status="running",
            prompt=str(rule["name"]),
            created_at=utc_now_iso(),
            updated_at=utc_now_iso(),
        )
        await self.store.create_run(run)
        await self.store.update_event_status(event.event_id, "running")
        result = await self.desktop_executor.execute(
            rule=rule,
            event_payload=event.payload,
            session_key=session_key,
            session_id=run.run_id,
            user_id=event.user_id,
        )
        message = str(result.get("message", "")).strip()
        notification = Notification(
            notification_id=uuid4().hex,
            user_id=event.user_id,
            title=message[:80] or f"Desktop automation: {rule['name']}",
            body=message,
            source=self._desktop_rule_name(str(rule["rule_id"])),
            severity=str(rule.get("severity", "info")),
            delivery_mode=str(rule.get("delivery_policy", "primary")),
            status="queued",
            target_channels=[],
            metadata={"rule_name": rule["name"], "event_id": event.event_id, "desktop_rule_id": rule["rule_id"]},
        )
        delivered = await self.dispatcher.dispatch(notification)
        await self.store.finish_run(
            run.run_id,
            status="completed",
            result_text=message,
            notification_id=delivered.notification_id,
        )
        await self.store.update_event_status(event.event_id, "completed")
        await self.store.update_desktop_rule_last_event(event.user_id, str(rule["rule_id"]), utc_now_iso())
        return {"status": "completed", "notification_id": delivered.notification_id, "rule_name": rule["name"]}

    async def _run_desktop_routine_event(
        self,
        event: AutomationEvent,
        routine: dict[str, Any],
        *,
        notify: bool = True,
    ) -> dict[str, Any]:
        should_skip, reason = await self.store.should_skip_for_dedupe(
            event.user_id,
            self._desktop_routine_rule_name(str(routine["routine_id"])),
            event.dedupe_key,
            int(routine.get("dedupe_window_seconds", 30)),
            int(routine.get("cooldown_seconds", 30)),
        )
        await self.store.record_event(event, status="skipped" if should_skip else "queued")
        if should_skip:
            return {"status": "skipped", "reason": reason, "rule_name": self._desktop_routine_rule_name(str(routine["routine_id"]))}
        session_key = f"automation:{event.user_id}:{self._slug(str(routine['name']))}"
        run = AutomationRun(
            run_id=uuid4().hex,
            event_id=event.event_id,
            user_id=event.user_id,
            rule_name=self._desktop_routine_rule_name(str(routine["routine_id"])),
            session_key=session_key,
            status="running",
            prompt=str(routine["name"]),
            created_at=utc_now_iso(),
            updated_at=utc_now_iso(),
        )
        await self.store.create_run(run)
        await self.store.update_event_status(event.event_id, "running")
        result = await self.desktop_routine_executor.execute(
            rule=routine,
            event_payload=event.payload,
            session_key=session_key,
            session_id=run.run_id,
            user_id=event.user_id,
        )
        message = str(result.get("message", "")).strip()
        notification_id: str | None = None
        if notify and message:
            notification = Notification(
                notification_id=uuid4().hex,
                user_id=event.user_id,
                title=message.splitlines()[0][:80] or f"Desktop routine: {routine['name']}",
                body=message,
                source=self._desktop_routine_rule_name(str(routine["routine_id"])),
                severity=str(routine.get("severity", "info")),
                delivery_mode=str(routine.get("delivery_policy", "primary")),
                status="queued",
                target_channels=[],
                metadata={
                    "routine_name": routine["name"],
                    "event_id": event.event_id,
                    "desktop_routine_id": routine["routine_id"],
                    "summary": result.get("summary", ""),
                    "steps": result.get("steps", []),
                },
            )
            delivered = await self.dispatcher.dispatch(notification)
            notification_id = delivered.notification_id
        await self.store.finish_run(
            run.run_id,
            status=str(result.get("status", "completed")),
            result_text=message,
            notification_id=notification_id,
            error="" if str(result.get("status", "completed")) == "completed" else message,
        )
        await self.store.update_event_status(event.event_id, str(result.get("status", "completed")))
        await self.store.update_desktop_routine_last_event(event.user_id, str(routine["routine_id"]), utc_now_iso())
        return {
            "status": str(result.get("status", "completed")),
            "notification_id": notification_id,
            "rule_name": routine["name"],
            "message": message,
            "steps": result.get("steps", []),
            "summary": result.get("summary", ""),
        }

    def _build_event(
        self,
        *,
        event_type: str,
        user_id: str,
        source: str,
        payload: dict[str, Any],
        dedupe_key: str,
        priority: int,
    ) -> AutomationEvent:
        return AutomationEvent(
            event_type=event_type,
            user_id=user_id,
            source=source,
            payload=payload,
            dedupe_key=dedupe_key,
            priority=priority,
        )

    async def _build_system_suffix(
        self,
        user_id: str,
        rule: AutomationRule,
        payload: dict[str, Any],
        existing_suffix: str | None,
    ) -> str:
        base = (
            "## Automation Mode\n"
            f"Rule: {rule.name}\n"
            f"Trigger: {rule.trigger}\n"
            f"Action policy: {rule.action_policy}\n"
            "You are running as a background automation worker. Gather information, summarize clearly, "
            "and do not take high-impact external side effects without approval. If there is nothing useful "
            "to tell the user, reply with NO_REPLY.\n\n"
        )
        suffix_parts = [base]
        recent_context = await self._build_recent_context(user_id)
        if recent_context:
            suffix_parts.append("## Recent Linked Context\n" + recent_context)
        if payload:
            suffix_parts.append("## Event Payload\n" + json.dumps(payload, ensure_ascii=False, indent=2))
        if existing_suffix:
            suffix_parts.append(existing_suffix)
        return "\n\n".join(part for part in suffix_parts if part).strip()

    async def _build_recent_context(self, user_id: str) -> str:
        identities = await self.user_profiles.list_identities(user_id)
        candidate_session_keys = ["main", "webchat_main"]
        for identity in identities:
            if identity["identity_type"] == "telegram":
                candidate_session_keys.append(f"telegram:{identity['identity_value']}")

        snippets: list[str] = []
        seen_keys: set[str] = set()
        for session_key in candidate_session_keys:
            if session_key in seen_keys:
                continue
            seen_keys.add(session_key)
            latest_path = await self.session_manager.latest_session_path(session_key)
            if latest_path is None:
                continue
            history = await self.session_manager.session_history(session_key, limit=4)
            if not history:
                continue
            lines = []
            for message in history:
                role = str(message.get("role", "")).strip().lower()
                if role not in {"user", "assistant"}:
                    continue
                content = str(message.get("content", "")).strip()
                if not content:
                    continue
                lines.append(f"{role}: {content[:200]}")
            if lines:
                snippets.append(f"[{session_key}]\n" + "\n".join(lines))
        return "\n\n".join(snippets[:3])

    async def _extract_notification_text(self, session_key: str, fallback_text: str) -> str:
        history = await self.session_manager.session_history(session_key, limit=40)
        if not history:
            return fallback_text

        last_tool_index = -1
        for index, message in enumerate(history):
            if str(message.get("role", "")).strip().lower() == "tool":
                last_tool_index = index

        search_space = history[last_tool_index + 1 :] if last_tool_index >= 0 else history
        for message in reversed(search_space):
            if str(message.get("role", "")).strip().lower() != "assistant":
                continue
            content = str(message.get("content", "")).strip()
            if not content:
                continue
            if self._is_non_deliverable_text(content):
                continue
            return content

        if fallback_text and not self._is_non_deliverable_text(fallback_text):
            return fallback_text
        return ""

    def _is_non_deliverable_text(self, text: str) -> bool:
        normalized = text.strip()
        if not normalized:
            return True
        lowered = normalized.lower()
        if lowered in {"no_reply", "no_reply."}:
            return True
        if lowered.startswith("assistant requested tool call"):
            return True
        if lowered.startswith("i will begin gathering information"):
            return True
        if lowered.startswith("okay, i will check"):
            return True
        return False

    def _notification_title(self, rule: AutomationRule, assistant_text: str) -> str:
        first_line = assistant_text.splitlines()[0].strip()
        if first_line:
            return first_line[:80]
        return f"Automation update: {rule.name}"

    def _direct_notification_text(
        self,
        *,
        rule: AutomationRule,
        user_prompt: str,
        payload: dict[str, Any],
    ) -> str | None:
        if str(rule.action_policy).strip().lower() != "notify_first":
            return None
        candidate = str(payload.get("message", "") or user_prompt).strip()
        if not candidate:
            return None
        trigger = str(rule.trigger).strip().lower()
        if trigger == "one-time":
            return candidate
        if trigger != "cron":
            return None
        normalized = re.sub(r"\s+", " ", candidate).strip().lower()
        if normalized == "cron test message from sonarbot":
            return candidate
        if normalized.startswith("reminder:") or normalized.startswith("this is your reminder"):
            return candidate
        if normalized.startswith("dont forget to ") or normalized.startswith("don't forget to "):
            return candidate
        if normalized.startswith("time to ") or normalized.startswith("it is time to ") or normalized.startswith("it's time to "):
            return candidate
        if "?" in candidate:
            return None
        if any(
            keyword in normalized
            for keyword in (
                "briefing",
                "digest",
                "summary",
                "summarize",
                "report",
                "analyze",
                "analysis",
                "research",
                "review",
                "scan",
                "search",
                "look up",
                "check ",
                "monitor",
                "gmail",
                "email",
                "calendar",
                "github",
                "browser",
                "open ",
                "send ",
                "create ",
                "generate ",
            )
        ):
            return None
        words = re.findall(r"[a-z0-9']+", normalized)
        if 0 < len(words) <= 14:
            return candidate
        return None

    def _cron_rule(self, rule_name: str, message: str) -> AutomationRule:
        return AutomationRule(
            name=rule_name,
            trigger="cron",
            prompt_or_skill=message,
            delivery_policy="primary",
            action_policy="notify_first",
            cooldown_seconds=0,
            dedupe_window_seconds=0,
            quiet_hours_behavior="queue",
            severity="info",
        )

    def _dynamic_cron_rule(self, cron_id: str, message: str) -> AutomationRule:
        return self._cron_rule(self._dynamic_cron_rule_name(cron_id), message)

    def _one_time_reminder_rule(self, reminder_id: str, message: str) -> AutomationRule:
        return AutomationRule(
            name=self._one_time_reminder_rule_name(reminder_id),
            trigger="one-time",
            prompt_or_skill=message,
            delivery_policy="primary",
            action_policy="notify_first",
            cooldown_seconds=0,
            dedupe_window_seconds=0,
            quiet_hours_behavior="queue",
            severity="info",
        )

    def _webhook_rule(self, name: str, message: str) -> AutomationRule:
        return AutomationRule(
            name=f"webhook:{name}",
            trigger="webhook",
            prompt_or_skill=message,
            delivery_policy="primary",
            action_policy="notify_first",
            cooldown_seconds=0,
            dedupe_window_seconds=300,
            quiet_hours_behavior="queue",
            severity="info",
        )

    def _configured_rule(self, name: str, trigger: str) -> AutomationRule | None:
        for item in self.config.automation.rules:
            if item.name == name:
                return AutomationRule(
                    name=item.name,
                    trigger=item.trigger,
                    prompt_or_skill=item.prompt_or_skill,
                    enabled=item.enabled,
                    conditions=dict(item.conditions),
                    action_policy=item.action_policy,
                    delivery_policy=item.delivery_policy,
                    cooldown_seconds=item.cooldown_seconds,
                    dedupe_window_seconds=item.dedupe_window_seconds,
                    quiet_hours_behavior=item.quiet_hours_behavior,
                    severity=item.severity,
                )
        return None

    def _dynamic_cron_rule_name(self, cron_id: str) -> str:
        return f"dynamic-cron:{cron_id}"

    def _one_time_reminder_rule_name(self, reminder_id: str) -> str:
        return f"one-time:{reminder_id}"

    def _desktop_rule_name(self, rule_id: str) -> str:
        return f"desktop:{rule_id}"

    def _desktop_routine_rule_name(self, routine_id: str) -> str:
        return f"routine:{routine_id}"

    def _rule_to_payload(self, rule: AutomationRule, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": rule.name,
            "trigger": rule.trigger,
            "enabled": rule.enabled,
            "action_policy": rule.action_policy,
            "delivery_policy": rule.delivery_policy,
            "cooldown_seconds": rule.cooldown_seconds,
            "dedupe_window_seconds": rule.dedupe_window_seconds,
            "quiet_hours_behavior": rule.quiet_hours_behavior,
            "severity": rule.severity,
            "paused": bool(state.get("paused", False)),
            "last_run_at": state.get("last_run_at", ""),
            "last_notification_at": state.get("last_notification_at", ""),
        }

    def _desktop_rule_to_payload(self, rule: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": self._desktop_rule_name(str(rule["rule_id"])),
            "display_name": str(rule["name"]),
            "trigger": "desktop",
            "trigger_type": str(rule["trigger_type"]),
            "watch_path": str(rule.get("watch_path", "")),
            "schedule": str(rule.get("schedule", "")),
            "event_types": list(rule.get("event_types", [])),
            "file_extensions": list(rule.get("file_extensions", [])),
            "filename_pattern": str(rule.get("filename_pattern", "*")),
            "action_type": str(rule.get("action_type", "notify")),
            "destination_path": str(rule.get("destination_path", "")),
            "paused": bool(rule.get("paused", False) or state.get("paused", False)),
            "last_run_at": state.get("last_run_at", ""),
            "last_notification_at": state.get("last_notification_at", ""),
            "last_event_at": str(rule.get("last_event_at", "")),
            "severity": str(rule.get("severity", "info")),
            "delivery_policy": str(rule.get("delivery_policy", "primary")),
        }

    def _desktop_routine_to_payload(self, routine: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        steps = list(routine.get("steps", []))
        risky_step_count = self.desktop_routine_executor.risky_step_count(steps) if self.desktop_routine_executor is not None else 0
        return {
            "name": self._desktop_routine_rule_name(str(routine["routine_id"])),
            "display_name": str(routine["name"]),
            "trigger": "desktop_routine",
            "trigger_type": str(routine.get("trigger_type", "manual")),
            "schedule": str(routine.get("schedule", "")),
            "run_at": str(routine.get("run_at", "")),
            "watch_path": str(routine.get("watch_path", "")),
            "event_types": list(routine.get("event_types", [])),
            "file_extensions": list(routine.get("file_extensions", [])),
            "filename_pattern": str(routine.get("filename_pattern", "*")),
            "summary": str(routine.get("summary", "")),
            "steps": steps,
            "step_count": len(steps),
            "risky_step_count": risky_step_count,
            "approval_mode": str(routine.get("approval_mode", "ask_on_risky_step")),
            "paused": bool(routine.get("paused", False) or state.get("paused", False)),
            "last_run_at": state.get("last_run_at", ""),
            "last_notification_at": state.get("last_notification_at", ""),
            "last_event_at": str(routine.get("last_event_at", "")),
            "severity": str(routine.get("severity", "info")),
            "delivery_policy": str(routine.get("delivery_policy", "primary")),
            "routine": True,
        }

    def _routine_matches_event(self, routine: dict[str, Any], *, event_type: str, path: str) -> bool:
        event_types = [str(item).lower() for item in routine.get("event_types", [])]
        if event_types and event_type.lower() not in event_types:
            return False
        candidate = Path(path)
        suffix = candidate.suffix.lower().lstrip(".")
        allowed_extensions = [str(item).lower().lstrip(".") for item in routine.get("file_extensions", []) if item]
        if allowed_extensions and suffix not in allowed_extensions:
            return False
        pattern = str(routine.get("filename_pattern", "*") or "*")
        return fnmatch.fnmatch(candidate.name.lower(), pattern.lower())

    def _routine_summary_from_steps(self, steps: list[dict[str, Any]]) -> str:
        if self.desktop_routine_executor is None:
            return "desktop routine"
        return self.desktop_routine_executor.summarize_steps(steps)

    def _hash_payload(self, source: str, payload: dict[str, Any]) -> str:
        raw = json.dumps({"source": source, "payload": payload}, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _slug(self, value: str) -> str:
        return re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-") or "automation"

    def _validate_cron_schedule(self, schedule: str) -> str:
        normalized = " ".join(schedule.split())
        if len(normalized.split()) != 5:
            raise ValueError("Cron schedule must have 5 fields, for example: 0 8 * * *")
        try:
            from apscheduler.triggers.cron import CronTrigger  # type: ignore

            CronTrigger.from_crontab(normalized)
        except Exception as exc:
            raise ValueError(f"Invalid cron schedule '{schedule}'.") from exc
        return normalized
