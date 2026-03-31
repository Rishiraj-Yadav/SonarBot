"""Advanced automation runtime."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from assistant.agent.queue import AgentRequest, QueueMode
from assistant.automation.delivery import NotificationDispatcher
from assistant.automation.models import (
    ApprovalRequest,
    AutomationEvent,
    AutomationRule,
    AutomationRun,
    DynamicCronJob,
    Notification,
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
    ) -> None:
        self.config = config
        self.agent_loop = agent_loop
        self.session_manager = session_manager
        self.standing_orders_manager = standing_orders_manager
        self.user_profiles = user_profiles
        self.store = store
        self.dispatcher = dispatcher
        self.scheduler = None

    async def initialize(self) -> None:
        await self.store.initialize()
        await self.user_profiles.initialize()

    def set_scheduler(self, scheduler) -> None:
        self.scheduler = scheduler

    async def handle_cron_job(
        self,
        rule_name: str,
        message: str,
        user_id: str | None = None,
        *,
        mode: str | None = None,
    ) -> dict[str, Any]:
        target_user = user_id or self.config.users.default_user_id
        cron_mode = self._resolve_cron_mode(rule_name, mode)
        rule = self._configured_rule(rule_name, "cron") or self._cron_rule(rule_name, message)
        event = self._build_event(
            event_type="cron",
            user_id=target_user,
            source=rule.name,
            payload={"message": message, "mode": cron_mode},
            dedupe_key=f"{rule.name}:{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M')}",
            priority=40,
        )
        try:
            if cron_mode == "direct":
                return await self._run_direct_notification_event(event, rule, message)
            return await self._run_event(event, rule, user_prompt=rule.prompt_or_skill or message)
        finally:
            if rule_name.startswith("dynamic-once:"):
                cron_id = rule_name.removeprefix("dynamic-once:")
                try:
                    await self.delete_dynamic_cron_job(target_user, cron_id)
                except KeyError:
                    pass

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
            *[
                {
                    **self._rule_to_payload(self._cron_rule(f"cron:{index}", job.message), state.get(f"cron:{index}", {})),
                    "schedule": job.schedule,
                    "message": job.message,
                    "mode": getattr(job, "mode", "direct"),
                    "dynamic": False,
                }
                for index, job in enumerate(self.config.automation.cron_jobs)
            ],
            *[self._rule_to_payload(rule, state.get(rule.name, {})) for rule in self.config.automation.rules],
            *[self._rule_to_payload(rule, state.get(rule.name, {})) for rule in await self.standing_orders_manager.compile_rules()],
        ]
        for job in dynamic_jobs:
            trigger_type = str(job.get("trigger_type", "cron"))
            dynamic_rule_name = self._dynamic_once_rule_name(str(job["cron_id"])) if trigger_type == "date" else self._dynamic_cron_rule_name(str(job["cron_id"]))
            dynamic_rule = self._cron_rule(dynamic_rule_name, str(job["message"]))
            payload = self._rule_to_payload(dynamic_rule, state.get(dynamic_rule.name, {}))
            payload["schedule"] = str(job["schedule"])
            payload["run_at"] = str(job.get("run_at", ""))
            payload["trigger_type"] = trigger_type
            payload["message"] = str(job["message"])
            payload["mode"] = str(job.get("mode", "direct"))
            payload["paused"] = bool(job["paused"])
            payload["dynamic"] = True
            payload["cron_id"] = str(job["cron_id"])
            rules.append(payload)
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
        if rule_name.startswith("dynamic-once:"):
            await self.pause_dynamic_cron_job(user_id, rule_name.removeprefix("dynamic-once:"))
            return
        await self.store.set_rule_paused(user_id, rule_name, True)

    async def resume_rule(self, user_id: str, rule_name: str) -> None:
        if rule_name.startswith("dynamic-cron:"):
            await self.resume_dynamic_cron_job(user_id, rule_name.removeprefix("dynamic-cron:"))
            return
        if rule_name.startswith("dynamic-once:"):
            await self.resume_dynamic_cron_job(user_id, rule_name.removeprefix("dynamic-once:"))
            return
        await self.store.set_rule_paused(user_id, rule_name, False)

    async def create_dynamic_cron_job(
        self,
        user_id: str,
        schedule: str,
        message: str,
        mode: str = "direct",
    ) -> dict[str, Any]:
        normalized_schedule = self._validate_cron_schedule(schedule)
        cleaned_message = message.strip()
        if not cleaned_message:
            raise ValueError("Cron job message cannot be empty.")
        normalized_mode = self._normalize_cron_mode(mode)
        job = DynamicCronJob(
            cron_id=uuid4().hex[:12],
            user_id=user_id,
            schedule=normalized_schedule,
            message=cleaned_message,
            mode=normalized_mode,
        )
        return await self._store_dynamic_job(job)

    async def create_one_time_reminder(
        self,
        user_id: str,
        run_at: str,
        message: str,
        mode: str = "direct",
    ) -> dict[str, Any]:
        cleaned_message = message.strip()
        if not cleaned_message:
            raise ValueError("Reminder message cannot be empty.")
        normalized_mode = self._normalize_cron_mode(mode)
        job = DynamicCronJob(
            cron_id=uuid4().hex[:12],
            user_id=user_id,
            schedule="",
            message=cleaned_message,
            mode=normalized_mode,
            trigger_type="date",
            run_at=run_at.strip(),
        )
        return await self._store_dynamic_job(job)

    async def _store_dynamic_job(self, job: DynamicCronJob) -> dict[str, Any]:
        await self.store.create_dynamic_cron_job(job)
        await self.store.set_rule_paused(user_id=job.user_id, rule_name=self._dynamic_rule_name(job), paused=False)
        if self.scheduler is not None:
            await self.scheduler.register_dynamic_job(
                {
                    "cron_id": job.cron_id,
                    "user_id": job.user_id,
                    "schedule": job.schedule,
                    "message": job.message,
                    "mode": job.mode,
                    "trigger_type": job.trigger_type,
                    "run_at": job.run_at,
                    "paused": False,
                }
            )
        return {
            "cron_id": job.cron_id,
            "user_id": job.user_id,
            "schedule": job.schedule,
            "message": job.message,
            "mode": job.mode,
            "trigger_type": job.trigger_type,
            "run_at": job.run_at,
            "paused": job.paused,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
        }

    async def list_dynamic_cron_jobs(self, user_id: str) -> list[dict[str, Any]]:
        return await self.store.list_dynamic_cron_jobs(user_id)

    async def list_all_dynamic_cron_jobs(self) -> list[dict[str, Any]]:
        return await self.store.list_all_dynamic_cron_jobs(include_paused=False)

    async def pause_dynamic_cron_job(self, user_id: str, cron_id: str) -> dict[str, Any]:
        job = await self.store.set_dynamic_cron_job_paused(user_id, cron_id, True)
        if job is None:
            raise KeyError(f"Unknown cron job '{cron_id}'.")
        await self.store.set_rule_paused(user_id, self._dynamic_rule_name(job), True)
        if self.scheduler is not None:
            await self.scheduler.pause_dynamic_job(cron_id)
        return job

    async def resume_dynamic_cron_job(self, user_id: str, cron_id: str) -> dict[str, Any]:
        job = await self.store.set_dynamic_cron_job_paused(user_id, cron_id, False)
        if job is None:
            raise KeyError(f"Unknown cron job '{cron_id}'.")
        await self.store.set_rule_paused(user_id, self._dynamic_rule_name(job), False)
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

    async def replay_run(self, run_id: str) -> dict[str, Any]:
        run = await self.store.get_run(run_id)
        if run is None:
            raise KeyError(f"Unknown automation run '{run_id}'.")
        event = await self.store.get_event(str(run["event_id"]))
        if event is None:
            raise KeyError(f"Automation run '{run_id}' is missing its event payload.")
        rule_name = str(run["rule_name"])
        if rule_name.startswith("cron:"):
            return await self.handle_cron_job(
                rule_name,
                str(event["payload"].get("message", "")),
                user_id=str(run["user_id"]),
                mode=str(event["payload"].get("mode", "direct")),
            )
        if rule_name.startswith("dynamic-cron:"):
            result = await self.handle_cron_job(
                rule_name,
                str(event["payload"].get("message", "")),
                user_id=str(run["user_id"]),
                mode=str(event["payload"].get("mode", "direct")),
            )
            return result
        if rule_name.startswith("dynamic-once:"):
            result = await self.handle_cron_job(
                rule_name,
                str(event["payload"].get("message", "")),
                user_id=str(run["user_id"]),
                mode=str(event["payload"].get("mode", "direct")),
            )
            return result
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
            return {"status": "completed", "rule_name": rule.name, "notified": False, "delivery_status": "skipped:NO_REPLY"}

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
        return {
            "status": "completed",
            "notification_id": delivered.notification_id,
            "rule_name": rule.name,
            "delivery_status": delivered.status,
        }

    async def _run_direct_notification_event(
        self,
        event: AutomationEvent,
        rule: AutomationRule,
        message: str,
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
            return {"status": "skipped", "reason": reason, "rule_name": rule.name, "mode": "direct"}

        run = AutomationRun(
            run_id=uuid4().hex,
            event_id=event.event_id,
            user_id=event.user_id,
            rule_name=rule.name,
            session_key=f"automation:{event.user_id}:{self._slug(rule.name)}",
            status="running",
            prompt=message,
            created_at=utc_now_iso(),
            updated_at=utc_now_iso(),
            result_text=message,
        )
        await self.store.create_run(run)
        await self.store.update_event_status(event.event_id, "running")

        notification = Notification(
            notification_id=uuid4().hex,
            user_id=event.user_id,
            title=self._notification_title(rule, message),
            body=message,
            source=rule.name,
            severity=rule.severity or self.config.automation.notifications.default_severity,
            delivery_mode=rule.delivery_policy,
            status="queued",
            target_channels=[],
            metadata={
                "rule_name": rule.name,
                "event_id": event.event_id,
                "delivery_policy": rule.delivery_policy,
                "cron_mode": "direct",
            },
        )
        delivered = await self.dispatcher.dispatch(notification)
        await self.store.finish_run(
            run.run_id,
            status="completed",
            result_text=message,
            notification_id=delivered.notification_id,
        )
        await self.store.update_event_status(event.event_id, "completed")
        return {
            "status": "completed",
            "notification_id": delivered.notification_id,
            "rule_name": rule.name,
            "delivery_status": delivered.status,
            "mode": "direct",
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

    def _dynamic_rule_name(self, job: dict[str, Any] | DynamicCronJob) -> str:
        trigger_type = str(getattr(job, "trigger_type", None) or job.get("trigger_type", "cron"))
        cron_id = str(getattr(job, "cron_id", None) or job.get("cron_id", ""))
        if trigger_type == "date":
            return self._dynamic_once_rule_name(cron_id)
        return self._dynamic_cron_rule_name(cron_id)

    def _normalize_cron_mode(self, value: str | None) -> str:
        normalized = str(value or "direct").strip().lower()
        return "ai" if normalized == "ai" else "direct"

    def _resolve_cron_mode(self, rule_name: str, mode: str | None) -> str:
        if mode is not None:
            return self._normalize_cron_mode(mode)
        if rule_name.startswith("dynamic-cron:") or rule_name.startswith("dynamic-once:"):
            return "direct"
        if rule_name.startswith("cron:"):
            try:
                index = int(rule_name.split(":", maxsplit=1)[1])
            except (IndexError, ValueError):
                return "direct"
            if 0 <= index < len(self.config.automation.cron_jobs):
                return self._normalize_cron_mode(getattr(self.config.automation.cron_jobs[index], "mode", "direct"))
        return "direct"

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

    def _dynamic_once_rule_name(self, cron_id: str) -> str:
        return f"dynamic-once:{cron_id}"

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
