"""Route validated protocol requests into the agent runtime."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from assistant.agent.session import create_message
from assistant.agent.queue import AgentRequest, QueueMode
from assistant.gateway.protocol import AgentSendParams, RequestFrame, ResponseFrame
from assistant.utils.logging import get_logger


LOGGER = get_logger("skill_router")


@dataclass(slots=True)
class GatewayRouter:
    config: Any
    agent_loop: Any
    connection_manager: Any
    session_manager: Any
    memory_manager: Any
    skill_registry: Any
    hook_runner: Any
    presence_registry: Any
    oauth_flow_manager: Any
    tool_registry: Any
    automation_engine: Any
    user_profiles: Any
    started_at: datetime
    system_access_manager: Any = None
    coworker_service: Any = None

    async def handle_request(self, connection_id: str, request: RequestFrame) -> ResponseFrame:
        if request.method == "health":
            return ResponseFrame(id=request.id, ok=True, payload=self.health_payload())

        if request.method == "agent.send":
            params = AgentSendParams.model_validate(request.params)
            connection = self.connection_manager.get_connection(connection_id)
            metadata = {
                "user_id": getattr(connection, "user_id", "") or self.config.users.default_user_id,
                "channel": getattr(connection, "channel_name", "ws"),
                "device_id": getattr(connection, "device_id", ""),
            }
            return await self.route_user_message(
                connection_id=connection_id,
                request_id=request.id,
                session_key=params.session_key,
                message=params.message,
                metadata=metadata,
            )

        return ResponseFrame(id=request.id, ok=False, error=f"Unknown method '{request.method}'.")

    async def route_user_message(
        self,
        connection_id: str,
        request_id: str,
        session_key: str,
        message: str,
        metadata: dict[str, Any] | None = None,
        mode: QueueMode = QueueMode.STEER,
        silent: bool = False,
        system_suffix: str | None = None,
    ) -> ResponseFrame:
        metadata = dict(metadata or {})
        metadata.setdefault("trace_id", uuid4().hex)
        metadata.setdefault("user_id", self.config.users.default_user_id)
        metadata.setdefault("connection_id", connection_id)
        stripped = message.strip()
        system_suffix = self._augment_system_suffix_for_intent(stripped, system_suffix)
        if stripped.startswith("/"):
            return await self._handle_slash_command(
                connection_id=connection_id,
                request_id=request_id,
                session_key=session_key,
                raw_command=stripped,
            )
        oauth_provider = self._match_oauth_connect_request(stripped)
        if oauth_provider is not None:
            return await self._start_oauth_flow(request_id, session_key, oauth_provider)
        if self._looks_like_oauth_status_request(stripped):
            return await self._handle_slash_command(
                connection_id=connection_id,
                request_id=request_id,
                session_key=session_key,
                raw_command="/oauth-status",
            )
        shortcut = await self._handle_tool_shortcut(request_id, session_key, stripped, metadata)
        if shortcut is not None:
            return shortcut

        skill_activation = await self._resolve_skill_intent(stripped)
        if skill_activation is not None:
            skill, activation_source = skill_activation
            metadata["activated_skill"] = skill.name
            metadata["skill_activation_source"] = activation_source
            await self._fire_message_received(session_key, message, metadata)
            skill_prompt = self.skill_registry.load_skill_prompt(skill.name)
            combined_suffix = self._append_system_suffix(
                system_suffix,
                "## Active Skill",
                skill_prompt,
            )
            self._log_skill_activation(skill.name, activation_source, metadata.get("trace_id", ""))
            await self.agent_loop.enqueue(
                AgentRequest(
                    connection_id=connection_id,
                    session_key=session_key,
                    message=message,
                    request_id=request_id,
                    mode=mode,
                    metadata=metadata,
                    silent=silent,
                    system_suffix=combined_suffix,
                )
            )
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={
                    "queued": True,
                    "session_key": session_key,
                    "activated_skill": skill.name,
                    "activation_source": activation_source,
                },
            )

        await self._fire_message_received(session_key, message, metadata)

        await self.agent_loop.enqueue(
            AgentRequest(
                connection_id=connection_id,
                session_key=session_key,
                message=message,
                request_id=request_id,
                mode=mode,
                metadata=metadata,
                silent=silent,
                system_suffix=system_suffix,
            )
        )
        return ResponseFrame(id=request_id, ok=True, payload={"queued": True, "session_key": session_key})

    def health_payload(self) -> dict[str, object]:
        uptime_seconds = int((datetime.now(timezone.utc) - self.started_at).total_seconds())
        return {
            "status": "ok",
            "uptime_seconds": uptime_seconds,
            "active_connections": self.connection_manager.active_count(),
            "active_sessions": self.session_manager.active_count(),
            "channels": self.connection_manager.active_channels(),
            "pending_requests": self.agent_loop.queue.pending_count(),
            "model": self.config.agent.model,
            "active_skills": self.skill_registry.active_count(),
            "agents": self.presence_registry.snapshot(),
        }

    async def dashboard_payload(self, session_key: str = "webchat_main") -> dict[str, Any]:
        health = self.health_payload()
        status = await self.session_manager.session_status(session_key)
        recent_messages = await self.session_manager.session_history(session_key, limit=10)
        return {
            "session": status,
            "uptime_seconds": health["uptime_seconds"],
            "recent_messages_count": len(recent_messages),
            "active_skills_count": self.skill_registry.active_count(),
        }

    async def _handle_slash_command(
        self,
        connection_id: str,
        request_id: str,
        session_key: str,
        raw_command: str,
    ) -> ResponseFrame:
        parts = raw_command[1:].split(maxsplit=1)
        command_name = parts[0].lower().split("@", maxsplit=1)[0]
        arguments = parts[1] if len(parts) > 1 else ""

        if command_name in {"new", "reset"}:
            previous = await self.session_manager.load_or_create(session_key)
            user_id = await self._resolve_user_id(connection_id, session_key)
            hook_event = await self.hook_runner.fire_event(
                f"command:{command_name}",
                context={
                    "session_key": session_key,
                    "command": command_name,
                    "arguments": arguments,
                    "session_id": previous.session_id,
                    "session_path": str(previous.storage_path),
                    "workspace_dir": str(self.config.agent.workspace_dir),
                    "logs_dir": str(self.config.logs_dir),
                    "user_id": user_id,
                },
            )
            session = await self.session_manager.reset_session(session_key)
            extra = self._flatten_hook_messages(hook_event.messages)
            response_text = f"Started a new session: {session.session_id}"
            if extra:
                response_text = f"{response_text}\n\n{extra}"
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={"queued": False, "session_key": session_key, "command_response": response_text},
            )

        if command_name == "stop":
            user_id = await self._resolve_user_id(connection_id, session_key)
            hook_event = await self.hook_runner.fire_event(
                "command:stop",
                context={
                    "session_key": session_key,
                    "command": command_name,
                    "arguments": arguments,
                    "logs_dir": str(self.config.logs_dir),
                    "user_id": user_id,
                },
            )
            cancelled = await self.agent_loop.cancel_session(session_key)
            extra = self._flatten_hook_messages(hook_event.messages)
            response_text = "Stopped the active turn." if cancelled else "No active turn was running for this session."
            if extra:
                response_text = f"{response_text}\n\n{extra}"
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={
                    "queued": False,
                    "cancelled": cancelled,
                    "session_key": session_key,
                    "command_response": response_text,
                },
            )

        if command_name == "memory":
            user_id = await self._resolve_user_id(connection_id, session_key)
            hook_event = await self.hook_runner.fire_event(
                "command:memory",
                context={
                    "session_key": session_key,
                    "command": command_name,
                    "arguments": arguments,
                    "logs_dir": str(self.config.logs_dir),
                    "user_id": user_id,
                },
            )
            memory_text = await self.memory_manager.read_long_term()
            extra = self._flatten_hook_messages(hook_event.messages)
            response_text = memory_text or "Long-term memory is currently empty."
            if extra:
                response_text = f"{response_text}\n\n{extra}"
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={"queued": False, "session_key": session_key, "command_response": response_text},
            )

        if command_name == "status":
            user_id = await self._resolve_user_id(connection_id, session_key)
            hook_event = await self.hook_runner.fire_event(
                "command:status",
                context={
                    "session_key": session_key,
                    "command": command_name,
                    "arguments": arguments,
                    "logs_dir": str(self.config.logs_dir),
                    "user_id": user_id,
                },
            )
            session_status = await self.session_manager.session_status(session_key)
            runtime_status = self.agent_loop.status()
            payload = {"session": session_status, "runtime": runtime_status}
            extra = self._flatten_hook_messages(hook_event.messages)
            response_text = json.dumps(payload, indent=2)
            if extra:
                response_text = f"{response_text}\n\n{extra}"
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={"queued": False, **payload, "session_key": session_key, "command_response": response_text},
            )

        if command_name == "skills":
            user_id = await self._resolve_user_id(connection_id, session_key)
            hook_event = await self.hook_runner.fire_event(
                "command:skills",
                context={
                    "session_key": session_key,
                    "command": command_name,
                    "arguments": arguments,
                    "logs_dir": str(self.config.logs_dir),
                    "user_id": user_id,
                },
            )
            enabled_skills = self.skill_registry.list_enabled()
            response_lines = [
                self._format_skill_summary(skill) for skill in enabled_skills
            ] or ["No skills are currently enabled."]
            extra = self._flatten_hook_messages(hook_event.messages)
            response_text = "\n".join(response_lines)
            if extra:
                response_text = f"{response_text}\n\n{extra}"
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={
                    "queued": False,
                    "skills": [skill.name for skill in enabled_skills],
                    "skill_details": [self._skill_payload(skill) for skill in enabled_skills],
                    "session_key": session_key,
                    "command_response": response_text,
                },
            )

        if command_name == "oauth":
            provider = arguments.strip().lower()
            if provider not in {"google", "github"}:
                return ResponseFrame(id=request_id, ok=False, error="Use /oauth google or /oauth github.")
            return await self._start_oauth_flow(request_id, session_key, provider)

        if command_name in {"oauth-status", "oauth_status"}:
            user_id = await self._resolve_user_id(connection_id, session_key)
            hook_event = await self.hook_runner.fire_event(
                "command:oauth-status",
                context={
                    "session_key": session_key,
                    "command": command_name,
                    "arguments": arguments,
                    "logs_dir": str(self.config.logs_dir),
                    "user_id": user_id,
                },
            )
            providers = await self.oauth_flow_manager.token_manager.list_connected()
            if providers:
                lines = [
                    f"- {item['provider']} ({item.get('user_id', 'default')}), expires: {item.get('expires_at', 'unknown')}"
                    for item in providers
                ]
                response_text = "Connected OAuth providers:\n" + "\n".join(lines)
            else:
                response_text = "No OAuth providers are connected yet."
            extra = self._flatten_hook_messages(hook_event.messages)
            if extra:
                response_text = f"{response_text}\n\n{extra}"
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={"queued": False, "session_key": session_key, "command_response": response_text, "providers": providers},
            )

        if command_name == "notifications":
            user_id = await self._resolve_user_id(connection_id, session_key)
            notifications = await self.automation_engine.list_notifications(user_id)
            if notifications:
                lines = [f"- {item['title']} ({item['source']}, {item['status']})" for item in notifications[:10]]
                response_text = "Recent notifications:\n" + "\n".join(lines)
            else:
                response_text = "No automation notifications yet."
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={"queued": False, "session_key": session_key, "command_response": response_text, "notifications": notifications},
            )

        if command_name in {"automation-runs", "automation_runs"}:
            user_id = await self._resolve_user_id(connection_id, session_key)
            runs = await self.automation_engine.list_runs(user_id)
            if runs:
                lines = [f"- {item['rule_name']} -> {item['status']} ({item['created_at']})" for item in runs[:10]]
                response_text = "Recent automation runs:\n" + "\n".join(lines)
            else:
                response_text = "No automation runs yet."
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={"queued": False, "session_key": session_key, "command_response": response_text, "runs": runs},
            )

        if command_name in {"automation-rules", "automation_rules"}:
            user_id = await self._resolve_user_id(connection_id, session_key)
            rules = await self.automation_engine.list_rules(user_id)
            lines = [
                f"- {item['name']} ({item['trigger']}): {'paused' if item['paused'] else 'active'}"
                for item in rules
            ] or ["No automation rules configured."]
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={"queued": False, "session_key": session_key, "command_response": "Automation rules:\n" + "\n".join(lines), "rules": rules},
            )

        if command_name == "desktop":
            if self.automation_engine is None or not getattr(self.config.automation.desktop, "enabled", False):
                return ResponseFrame(id=request_id, ok=False, error="Desktop automation is not enabled.")
            user_id = await self._resolve_user_id(connection_id, session_key)
            subcommand, subargs = self._split_command_arguments(arguments)
            subcommand = subcommand.lower()
            if subcommand in {"", "help"}:
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={
                        "queued": False,
                        "session_key": session_key,
                        "command_response": self._desktop_help_text(),
                    },
                )
            if subcommand in {"list", "ls"}:
                rules = await self.automation_engine.list_rules(user_id)
                desktop_rules = self._filter_desktop_rules(rules)
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={
                        "queued": False,
                        "session_key": session_key,
                        "command_response": self._format_desktop_rules_response(desktop_rules),
                        "rules": desktop_rules,
                    },
                )
            if subcommand in {"pause", "resume", "delete", "remove", "rm"}:
                if not subargs.strip():
                    return ResponseFrame(id=request_id, ok=False, error=f"Use /desktop {subcommand} <rule_name>.")
                rules = await self.automation_engine.list_rules(user_id)
                desktop_rules = self._filter_desktop_rules(rules)
                matched_rule, error_text = self._match_desktop_rule_reference(desktop_rules, subargs)
                if error_text:
                    return ResponseFrame(id=request_id, ok=False, error=error_text)
                assert matched_rule is not None
                rule_name = str(matched_rule.get("name", ""))
                display_name = str(matched_rule.get("display_name") or rule_name)
                if subcommand == "pause":
                    await self.automation_engine.pause_rule(user_id, rule_name)
                    response_text = f"Paused desktop automation '{display_name}'."
                elif subcommand == "resume":
                    await self.automation_engine.resume_rule(user_id, rule_name)
                    response_text = f"Resumed desktop automation '{display_name}'."
                else:
                    await self.automation_engine.delete_rule(user_id, rule_name)
                    response_text = f"Deleted desktop automation '{display_name}'."
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={"queued": False, "session_key": session_key, "command_response": response_text},
                )
            return ResponseFrame(id=request_id, ok=False, error="Unknown /desktop subcommand. Use /desktop help.")

        if command_name == "routine":
            if self.automation_engine is None or not getattr(self.config.automation.desktop, "enabled", False):
                return ResponseFrame(id=request_id, ok=False, error="Desktop routines are not enabled.")
            user_id = await self._resolve_user_id(connection_id, session_key)
            subcommand, subargs = self._split_command_arguments(arguments)
            subcommand = subcommand.lower()
            if subcommand in {"", "help"}:
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={
                        "queued": False,
                        "session_key": session_key,
                        "command_response": self._routine_help_text(),
                    },
                )
            rules = await self.automation_engine.list_rules(user_id)
            desktop_routines = self._filter_desktop_routines(rules)
            if subcommand in {"list", "ls"}:
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={
                        "queued": False,
                        "session_key": session_key,
                        "command_response": self._format_desktop_routines_response(desktop_routines),
                        "rules": desktop_routines,
                    },
                )
            if subcommand == "show":
                if not subargs.strip():
                    return ResponseFrame(id=request_id, ok=False, error="Use /routine show <routine_name>.")
                matched_rule, error_text = self._match_desktop_routine_reference(desktop_routines, subargs)
                if error_text:
                    return ResponseFrame(id=request_id, ok=False, error=error_text)
                assert matched_rule is not None
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={
                        "queued": False,
                        "session_key": session_key,
                        "command_response": self._format_desktop_routine_show_response(matched_rule),
                        "rule": matched_rule,
                    },
                )
            if subcommand == "run":
                if not subargs.strip():
                    return ResponseFrame(id=request_id, ok=False, error="Use /routine run <routine_name>.")
                matched_rule, error_text = self._match_desktop_routine_reference(desktop_routines, subargs)
                if error_text:
                    return ResponseFrame(id=request_id, ok=False, error=error_text)
                assert matched_rule is not None
                result = await self.automation_engine.run_desktop_routine_now(
                    user_id,
                    str(matched_rule.get("name", "")).removeprefix("routine:"),
                    notify=False,
                )
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={
                        "queued": False,
                        "session_key": session_key,
                        "command_response": str(result.get("message", "Routine completed.")),
                        "result": result,
                    },
                )
            if subcommand in {"pause", "resume", "delete", "remove", "rm"}:
                if not subargs.strip():
                    return ResponseFrame(id=request_id, ok=False, error=f"Use /routine {subcommand} <routine_name>.")
                matched_rule, error_text = self._match_desktop_routine_reference(desktop_routines, subargs)
                if error_text:
                    return ResponseFrame(id=request_id, ok=False, error=error_text)
                assert matched_rule is not None
                rule_name = str(matched_rule.get("name", ""))
                display_name = str(matched_rule.get("display_name") or rule_name)
                if subcommand == "pause":
                    await self.automation_engine.pause_rule(user_id, rule_name)
                    response_text = f"Paused desktop routine '{display_name}'."
                elif subcommand == "resume":
                    await self.automation_engine.resume_rule(user_id, rule_name)
                    response_text = f"Resumed desktop routine '{display_name}'."
                else:
                    await self.automation_engine.delete_rule(user_id, rule_name)
                    response_text = f"Deleted desktop routine '{display_name}'."
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={"queued": False, "session_key": session_key, "command_response": response_text},
                )
            return ResponseFrame(id=request_id, ok=False, error="Unknown /routine subcommand. Use /routine help.")

        if command_name == "cron":
            user_id = await self._resolve_user_id(connection_id, session_key)
            subcommand, subargs = self._split_command_arguments(arguments)
            subcommand = subcommand.lower()
            if subcommand in {"", "help"}:
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={
                        "queued": False,
                        "session_key": session_key,
                        "command_response": self._cron_help_text(),
                    },
                )
            if subcommand in {"add", "create"}:
                schedule, cron_message = self._parse_cron_add_arguments(subargs)
                if not schedule or not cron_message:
                    return ResponseFrame(id=request_id, ok=False, error="Use /cron add \"0 8 * * *\" \"Message\" or /cron add 0 8 * * * | Message.")
                try:
                    job = await self.automation_engine.create_dynamic_cron_job(user_id, schedule, cron_message)
                except ValueError as exc:
                    return ResponseFrame(id=request_id, ok=False, error=str(exc))
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={
                        "queued": False,
                        "session_key": session_key,
                        "command_response": (
                            f"Created cron job '{job['cron_id']}' on {job['schedule']}.\n"
                            f"Message: {job['message']}"
                        ),
                        "cron_job": job,
                    },
                )
            if subcommand in {"list", "ls"}:
                dynamic_jobs = await self.automation_engine.list_dynamic_cron_jobs(user_id)
                lines: list[str] = []
                if dynamic_jobs:
                    lines.append("Chat-created cron jobs:")
                    lines.extend(
                        f"- {item['cron_id']}: {'paused' if item['paused'] else 'active'} | {item['schedule']} | {item['message']}"
                        for item in dynamic_jobs
                    )
                else:
                    lines.append("Chat-created cron jobs: none")
                if self.config.automation.cron_jobs:
                    lines.append("")
                    lines.append("Config cron jobs:")
                    lines.extend(
                        f"- config:{index}: active | {job.schedule} | {job.message}"
                        for index, job in enumerate(self.config.automation.cron_jobs)
                    )
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={
                        "queued": False,
                        "session_key": session_key,
                        "command_response": "\n".join(lines),
                        "cron_jobs": dynamic_jobs,
                    },
                )
            if subcommand == "pause":
                cron_id = subargs.strip()
                if not cron_id:
                    return ResponseFrame(id=request_id, ok=False, error="Use /cron pause <cron_id>.")
                try:
                    job = await self.automation_engine.pause_dynamic_cron_job(user_id, cron_id)
                except KeyError as exc:
                    return ResponseFrame(id=request_id, ok=False, error=str(exc))
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={
                        "queued": False,
                        "session_key": session_key,
                        "command_response": f"Paused cron job '{cron_id}'.",
                        "cron_job": job,
                    },
                )
            if subcommand == "resume":
                cron_id = subargs.strip()
                if not cron_id:
                    return ResponseFrame(id=request_id, ok=False, error="Use /cron resume <cron_id>.")
                try:
                    job = await self.automation_engine.resume_dynamic_cron_job(user_id, cron_id)
                except KeyError as exc:
                    return ResponseFrame(id=request_id, ok=False, error=str(exc))
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={
                        "queued": False,
                        "session_key": session_key,
                        "command_response": f"Resumed cron job '{cron_id}'.",
                        "cron_job": job,
                    },
                )
            if subcommand in {"delete", "remove", "rm"}:
                cron_id = subargs.strip()
                if not cron_id:
                    return ResponseFrame(id=request_id, ok=False, error="Use /cron delete <cron_id>.")
                try:
                    await self.automation_engine.delete_dynamic_cron_job(user_id, cron_id)
                except KeyError as exc:
                    return ResponseFrame(id=request_id, ok=False, error=str(exc))
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={
                        "queued": False,
                        "session_key": session_key,
                        "command_response": f"Deleted cron job '{cron_id}'.",
                    },
                )
            return ResponseFrame(id=request_id, ok=False, error="Unknown /cron subcommand. Use /cron help.")

        if command_name == "apps":
            if not getattr(self.config.desktop_apps, "enabled", False):
                return ResponseFrame(id=request_id, ok=False, error="Desktop app control is not enabled.")
            subcommand, subargs = self._split_command_arguments(arguments)
            subcommand = subcommand.lower()
            if subcommand in {"", "help"}:
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={
                        "queued": False,
                        "session_key": session_key,
                        "command_response": self._apps_help_text(),
                    },
                )
            tool_name: str | None = None
            tool_payload: dict[str, Any] = {}
            if subcommand in {"list", "ls"}:
                tool_name = "apps_list_windows"
            elif subcommand == "open":
                if not subargs.strip():
                    return ResponseFrame(id=request_id, ok=False, error="Use /apps open <alias>.")
                tool_name = "apps_open"
                tool_payload = {"target": subargs.strip()}
            elif subcommand in {"focus", "switch"}:
                if not subargs.strip():
                    return ResponseFrame(id=request_id, ok=False, error="Use /apps focus <window_or_alias>.")
                tool_name = "apps_focus"
                tool_payload = {"target": subargs.strip()}
            elif subcommand == "minimize":
                if not subargs.strip():
                    return ResponseFrame(id=request_id, ok=False, error="Use /apps minimize <window_or_alias>.")
                tool_name = "apps_minimize"
                tool_payload = {"target": subargs.strip()}
            elif subcommand == "maximize":
                if not subargs.strip():
                    return ResponseFrame(id=request_id, ok=False, error="Use /apps maximize <window_or_alias>.")
                tool_name = "apps_maximize"
                tool_payload = {"target": subargs.strip()}
            elif subcommand == "restore":
                if not subargs.strip():
                    return ResponseFrame(id=request_id, ok=False, error="Use /apps restore <window_or_alias>.")
                tool_name = "apps_restore"
                tool_payload = {"target": subargs.strip()}
            elif subcommand in {"left", "right"}:
                if not subargs.strip():
                    return ResponseFrame(id=request_id, ok=False, error=f"Use /apps {subcommand} <window_or_alias>.")
                tool_name = "apps_snap"
                tool_payload = {"target": subargs.strip(), "position": subcommand}
            else:
                return ResponseFrame(id=request_id, ok=False, error="Unknown /apps subcommand. Use /apps help.")

            try:
                result = await self.tool_registry.dispatch(tool_name, tool_payload)
            except Exception as exc:
                return ResponseFrame(id=request_id, ok=False, error=str(exc))
            response_action = "focus" if subcommand == "switch" else ("snap" if subcommand in {"left", "right"} else subcommand)
            response_text = self._format_app_control_response(response_action, result)
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={"queued": False, "session_key": session_key, "command_response": response_text},
            )

        if command_name == "screen":
            if not getattr(self.config.desktop_vision, "enabled", False):
                return ResponseFrame(id=request_id, ok=False, error="Desktop vision is not enabled.")
            subcommand, subargs = self._split_command_arguments(arguments)
            subcommand = subcommand.lower()
            if subcommand in {"", "help"}:
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={
                        "queued": False,
                        "session_key": session_key,
                        "command_response": self._screen_help_text(),
                    },
                )
            try:
                if subcommand == "active":
                    result = await self.tool_registry.dispatch("desktop_active_window", {})
                    response_text = self._format_desktop_vision_response("active", result)
                elif subcommand in {"capture", "shot", "screenshot"}:
                    result = await self.tool_registry.dispatch("desktop_screenshot", {})
                    response_text = self._format_desktop_vision_response("capture", result)
                elif subcommand == "window":
                    result = await self.tool_registry.dispatch("desktop_window_screenshot", {})
                    response_text = self._format_desktop_vision_response("window", result)
                elif subcommand == "read":
                    target = "window" if subargs.strip().lower() in {"window", "active", "active-window", "active window"} else "desktop"
                    result = await self.tool_registry.dispatch("desktop_read_screen", {"target": target})
                    response_text = self._format_desktop_vision_response("read", result)
                else:
                    return ResponseFrame(id=request_id, ok=False, error="Unknown /screen subcommand. Use /screen help.")
            except Exception as exc:
                return ResponseFrame(id=request_id, ok=False, error=str(exc))
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={"queued": False, "session_key": session_key, "command_response": response_text},
            )

        if command_name == "input":
            if not getattr(self.config.desktop_input, "enabled", False):
                return ResponseFrame(id=request_id, ok=False, error="Desktop input is not enabled.")
            subcommand, subargs = self._split_command_arguments(arguments)
            subcommand = subcommand.lower()
            if subcommand in {"", "help"}:
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={
                        "queued": False,
                        "session_key": session_key,
                        "command_response": self._input_help_text(),
                    },
                )
            try:
                if subcommand in {"position", "pos"}:
                    result = await self.tool_registry.dispatch("desktop_mouse_position", {})
                    response_text = self._format_desktop_input_response("position", result)
                elif subcommand == "move":
                    coords = self._parse_input_coordinates(subargs)
                    if coords is None:
                        return ResponseFrame(id=request_id, ok=False, error="Use /input move <x> <y>.")
                    result = await self.tool_registry.dispatch("desktop_mouse_move", {"x": coords[0], "y": coords[1]})
                    response_text = self._format_desktop_input_response("move", result)
                elif subcommand in {"click", "right-click", "rightclick", "double-click", "doubleclick"}:
                    coords = self._parse_input_coordinates(subargs)
                    if coords is None:
                        return ResponseFrame(id=request_id, ok=False, error=f"Use /input {subcommand} <x> <y>.")
                    payload = {"x": coords[0], "y": coords[1]}
                    action = "click"
                    if subcommand in {"right-click", "rightclick"}:
                        payload["button"] = "right"
                        action = "right-click"
                    elif subcommand in {"double-click", "doubleclick"}:
                        payload["count"] = 2
                        action = "double-click"
                    result = await self.tool_registry.dispatch("desktop_mouse_click", payload)
                    response_text = self._format_desktop_input_response(action, result)
                elif subcommand == "scroll":
                    scroll_args = self._parse_input_scroll_arguments(subargs)
                    if scroll_args is None:
                        return ResponseFrame(id=request_id, ok=False, error="Use /input scroll up|down <amount>.")
                    result = await self.tool_registry.dispatch(
                        "desktop_mouse_scroll",
                        {"direction": scroll_args[0], "amount": scroll_args[1]},
                    )
                    response_text = self._format_desktop_input_response("scroll", result)
                elif subcommand == "type":
                    text = subargs.strip()
                    if not text:
                        return ResponseFrame(id=request_id, ok=False, error="Use /input type <text>.")
                    result = await self.tool_registry.dispatch("desktop_keyboard_type", {"text": text})
                    response_text = self._format_desktop_input_response("type", result)
                elif subcommand == "hotkey":
                    hotkey = subargs.strip()
                    if not hotkey:
                        return ResponseFrame(id=request_id, ok=False, error="Use /input hotkey <keys>.")
                    result = await self.tool_registry.dispatch("desktop_keyboard_hotkey", {"hotkey": hotkey})
                    response_text = self._format_desktop_input_response("hotkey", result)
                else:
                    return ResponseFrame(id=request_id, ok=False, error="Unknown /input subcommand. Use /input help.")
            except Exception as exc:
                return ResponseFrame(id=request_id, ok=False, error=str(exc))
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={"queued": False, "session_key": session_key, "command_response": response_text},
            )

        if command_name == "clipboard":
            if not getattr(self.config.desktop_input, "enabled", False):
                return ResponseFrame(id=request_id, ok=False, error="Desktop input is not enabled.")
            subcommand, subargs = self._split_command_arguments(arguments)
            subcommand = subcommand.lower()
            if subcommand in {"", "help"}:
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={
                        "queued": False,
                        "session_key": session_key,
                        "command_response": self._clipboard_help_text(),
                    },
                )
            try:
                if subcommand in {"get", "read"}:
                    result = await self.tool_registry.dispatch("desktop_clipboard_read", {})
                    response_text = self._format_desktop_input_response("clipboard-read", result)
                elif subcommand in {"set", "write"}:
                    text = subargs.strip()
                    if not text:
                        return ResponseFrame(id=request_id, ok=False, error="Use /clipboard set <text>.")
                    result = await self.tool_registry.dispatch("desktop_clipboard_write", {"text": text})
                    response_text = self._format_desktop_input_response("clipboard-write", result)
                else:
                    return ResponseFrame(id=request_id, ok=False, error="Unknown /clipboard subcommand. Use /clipboard help.")
            except Exception as exc:
                return ResponseFrame(id=request_id, ok=False, error=str(exc))
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={"queued": False, "session_key": session_key, "command_response": response_text},
            )

        if command_name == "coworker":
            if self.coworker_service is None or not getattr(self.config.desktop_coworker, "enabled", False):
                return ResponseFrame(id=request_id, ok=False, error="Desktop coworker is not enabled.")
            user_id = await self._resolve_user_id(connection_id, session_key)
            subcommand, subargs = self._split_command_arguments(arguments)
            subcommand = subcommand.lower()
            if subcommand in {"", "help"}:
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={"queued": False, "session_key": session_key, "command_response": self._coworker_help_text()},
                )
            try:
                if subcommand == "plan":
                    if not subargs.strip():
                        return ResponseFrame(id=request_id, ok=False, error="Use /coworker plan <task>.")
                    task = await self.coworker_service.plan_task(
                        user_id=user_id,
                        session_key=session_key,
                        request_text=subargs.strip(),
                    )
                    response_text = self._format_coworker_task(task, planned=True)
                elif subcommand == "run":
                    if not subargs.strip():
                        return ResponseFrame(id=request_id, ok=False, error="Use /coworker run <task or task_id>.")
                    candidate = subargs.strip()
                    if re.fullmatch(r"[0-9a-fA-F]{12}", candidate):
                        task = await self.coworker_service.run_task(
                            user_id=user_id,
                            task_id=candidate,
                            connection_id=connection_id,
                            channel_name=str(self.connection_manager.get_connection(connection_id).channel_name)
                            if self.connection_manager.get_connection(connection_id) is not None
                            else "",
                        )
                    else:
                        task = await self.coworker_service.run_task_request(
                            user_id=user_id,
                            session_key=session_key,
                            request_text=candidate,
                            connection_id=connection_id,
                            channel_name=str(self.connection_manager.get_connection(connection_id).channel_name)
                            if self.connection_manager.get_connection(connection_id) is not None
                            else "",
                        )
                    response_text = self._format_coworker_task(task)
                elif subcommand == "step":
                    if not subargs.strip():
                        return ResponseFrame(id=request_id, ok=False, error="Use /coworker step <task_id>.")
                    task = await self.coworker_service.step_task(
                        user_id=user_id,
                        task_id=subargs.strip(),
                        connection_id=connection_id,
                        channel_name=str(self.connection_manager.get_connection(connection_id).channel_name)
                        if self.connection_manager.get_connection(connection_id) is not None
                        else "",
                    )
                    response_text = self._format_coworker_task(task)
                elif subcommand in {"status", "show"}:
                    if not subargs.strip():
                        return ResponseFrame(id=request_id, ok=False, error="Use /coworker status <task_id>.")
                    task = await self.coworker_service.get_task(user_id=user_id, task_id=subargs.strip())
                    response_text = self._format_coworker_task(task, planned=str(task.get("status")) == "planned")
                elif subcommand == "stop":
                    if not subargs.strip():
                        return ResponseFrame(id=request_id, ok=False, error="Use /coworker stop <task_id>.")
                    task = await self.coworker_service.stop_task(user_id=user_id, task_id=subargs.strip())
                    response_text = self._format_coworker_task(task)
                elif subcommand == "history":
                    tasks = await self.coworker_service.list_tasks(user_id=user_id, limit=12)
                    response_text = self._format_coworker_history(tasks)
                else:
                    return ResponseFrame(id=request_id, ok=False, error="Unknown /coworker subcommand. Use /coworker help.")
            except (ValueError, KeyError, RuntimeError) as exc:
                return ResponseFrame(id=request_id, ok=False, error=str(exc))
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={"queued": False, "session_key": session_key, "command_response": response_text},
            )

        if command_name == "vscode":
            if not getattr(self.config.app_skills, "enabled", False) or not getattr(self.config.app_skills, "vscode_enabled", True):
                return ResponseFrame(id=request_id, ok=False, error="VS Code skill pack is not enabled.")
            user_id = await self._resolve_user_id(connection_id, session_key)
            context = self._app_skill_context(session_key, user_id, connection_id=connection_id)
            subcommand, subargs = self._split_command_arguments(arguments)
            subcommand = subcommand.lower()
            if subcommand in {"", "help"}:
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={"queued": False, "session_key": session_key, "command_response": self._vscode_help_text()},
                )
            try:
                if subcommand in {"open", "project"}:
                    if not subargs.strip():
                        return ResponseFrame(id=request_id, ok=False, error="Use /vscode open <path_or_name>.")
                    result = await self.tool_registry.dispatch(
                        "vscode_open_target",
                        {"target": subargs.strip(), "prefer": "directory", **context},
                    )
                    response_text = self._format_vscode_response("open", result)
                elif subcommand == "file":
                    if not subargs.strip():
                        return ResponseFrame(id=request_id, ok=False, error="Use /vscode file <path_or_name>.")
                    result = await self.tool_registry.dispatch(
                        "vscode_open_target",
                        {"target": subargs.strip(), "prefer": "file", **context},
                    )
                    response_text = self._format_vscode_response("open", result)
                elif subcommand == "search":
                    if not subargs.strip():
                        return ResponseFrame(id=request_id, ok=False, error="Use /vscode search <query>.")
                    result = await self.tool_registry.dispatch(
                        "vscode_search",
                        {"query": subargs.strip(), "prefer": "either", **context},
                    )
                    response_text = self._format_vscode_response("search", result)
                else:
                    return ResponseFrame(id=request_id, ok=False, error="Unknown /vscode subcommand. Use /vscode help.")
            except Exception as exc:
                return ResponseFrame(id=request_id, ok=False, error=str(exc))
            return ResponseFrame(id=request_id, ok=True, payload={"queued": False, "session_key": session_key, "command_response": response_text})

        if command_name == "doc":
            if not getattr(self.config.app_skills, "enabled", False) or not getattr(self.config.app_skills, "documents_enabled", True):
                return ResponseFrame(id=request_id, ok=False, error="Document skill pack is not enabled.")
            user_id = await self._resolve_user_id(connection_id, session_key)
            context = self._app_skill_context(session_key, user_id, connection_id=connection_id)
            subcommand, subargs = self._split_command_arguments(arguments)
            subcommand = subcommand.lower()
            if subcommand in {"", "help"}:
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={"queued": False, "session_key": session_key, "command_response": self._document_help_text()},
                )
            try:
                if subcommand == "read":
                    if not subargs.strip():
                        return ResponseFrame(id=request_id, ok=False, error="Use /doc read <path_or_name>.")
                    result = await self.tool_registry.dispatch("document_read", {"path": subargs.strip(), **context})
                    response_text = self._format_document_response("read", result)
                elif subcommand == "create":
                    path_text, content_text = self._split_delimited_arguments(subargs, expected_parts=2)
                    if not path_text:
                        return ResponseFrame(id=request_id, ok=False, error="Use /doc create <path> :: <content>.")
                    result = await self.tool_registry.dispatch(
                        "document_create",
                        {"path": path_text, "content": content_text or "", **context},
                    )
                    response_text = self._format_document_response("create", result)
                elif subcommand == "replace":
                    path_text, find_text, replace_text = self._split_delimited_arguments(subargs, expected_parts=3)
                    if not path_text or find_text is None or replace_text is None:
                        return ResponseFrame(id=request_id, ok=False, error="Use /doc replace <path> :: <find> :: <replace>.")
                    result = await self.tool_registry.dispatch(
                        "document_replace_text",
                        {"path": path_text, "find_text": find_text, "replace_text": replace_text, **context},
                    )
                    response_text = self._format_document_response("replace", result)
                else:
                    return ResponseFrame(id=request_id, ok=False, error="Unknown /doc subcommand. Use /doc help.")
            except Exception as exc:
                return ResponseFrame(id=request_id, ok=False, error=str(exc))
            return ResponseFrame(id=request_id, ok=True, payload={"queued": False, "session_key": session_key, "command_response": response_text})

        if command_name == "excel":
            if not getattr(self.config.app_skills, "enabled", False) or not getattr(self.config.app_skills, "excel_enabled", True):
                return ResponseFrame(id=request_id, ok=False, error="Excel skill pack is not enabled.")
            user_id = await self._resolve_user_id(connection_id, session_key)
            context = self._app_skill_context(session_key, user_id, connection_id=connection_id)
            subcommand, subargs = self._split_command_arguments(arguments)
            subcommand = subcommand.lower()
            if subcommand in {"", "help"}:
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={"queued": False, "session_key": session_key, "command_response": self._excel_help_text()},
                )
            try:
                if subcommand == "create":
                    path_text, headers_text = self._split_delimited_arguments(subargs, expected_parts=2)
                    if not path_text:
                        return ResponseFrame(id=request_id, ok=False, error="Use /excel create <path> :: <header1,header2,...>.")
                    headers = self._parse_csv_values(headers_text or "")
                    result = await self.tool_registry.dispatch(
                        "excel_create_workbook",
                        {"path": path_text, "headers": headers, **context},
                    )
                    response_text = self._format_excel_response("create", result)
                elif subcommand in {"append-row", "append"}:
                    path_text, values_text = self._split_delimited_arguments(subargs, expected_parts=2)
                    if not path_text or values_text is None:
                        return ResponseFrame(id=request_id, ok=False, error="Use /excel append-row <path> :: <value1,value2,...>.")
                    result = await self.tool_registry.dispatch(
                        "excel_append_row",
                        {"path": path_text, "values": self._parse_csv_values(values_text), **context},
                    )
                    response_text = self._format_excel_response("append", result)
                elif subcommand in {"preview", "read"}:
                    path_text, limit_text = self._split_delimited_arguments(subargs, expected_parts=2)
                    target = path_text or subargs.strip()
                    if not target:
                        return ResponseFrame(id=request_id, ok=False, error="Use /excel preview <path> [:: limit].")
                    limit = self._parse_browser_limit(limit_text or "", default=8)
                    result = await self.tool_registry.dispatch("excel_preview", {"path": target, "limit": limit, **context})
                    response_text = self._format_excel_response("preview", result)
                else:
                    return ResponseFrame(id=request_id, ok=False, error="Unknown /excel subcommand. Use /excel help.")
            except Exception as exc:
                return ResponseFrame(id=request_id, ok=False, error=str(exc))
            return ResponseFrame(id=request_id, ok=True, payload={"queued": False, "session_key": session_key, "command_response": response_text})

        if command_name == "system":
            if not getattr(self.config.app_skills, "enabled", False) or not getattr(self.config.app_skills, "system_enabled", True):
                return ResponseFrame(id=request_id, ok=False, error="System control pack is not enabled.")
            user_id = await self._resolve_user_id(connection_id, session_key)
            context = self._app_skill_context(session_key, user_id, connection_id=connection_id)
            subcommand, subargs = self._split_command_arguments(arguments)
            subcommand = subcommand.lower()
            if subcommand in {"", "help"}:
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={"queued": False, "session_key": session_key, "command_response": self._system_help_text()},
                )
            try:
                if subcommand in {"settings", "open"}:
                    page = subargs.strip() or "settings"
                    result = await self.tool_registry.dispatch("system_open_settings", {"page": page, **context})
                    response_text = self._format_system_response("settings", result)
                elif subcommand == "status":
                    result = await self.tool_registry.dispatch("system_snapshot", {})
                    response_text = self._format_system_response("status", result)
                elif subcommand == "volume":
                    set_match = re.match(r"^set\s+(\d+)\s*$", subargs.strip(), flags=re.IGNORECASE)
                    if set_match is not None:
                        result = await self.tool_registry.dispatch("system_volume_set", {"percent": int(set_match.group(1)), **context})
                        response_text = self._format_system_response("volume-set", result)
                    else:
                        result = await self.tool_registry.dispatch("system_volume_status", {})
                        response_text = self._format_system_response("volume", result)
                elif subcommand == "brightness":
                    set_match = re.match(r"^set\s+(\d+)\s*$", subargs.strip(), flags=re.IGNORECASE)
                    if set_match is not None:
                        result = await self.tool_registry.dispatch("system_brightness_set", {"percent": int(set_match.group(1)), **context})
                        response_text = self._format_system_response("brightness-set", result)
                    else:
                        result = await self.tool_registry.dispatch("system_brightness_status", {})
                        response_text = self._format_system_response("brightness", result)
                elif subcommand == "bluetooth":
                    result = await self.tool_registry.dispatch("system_bluetooth_status", {})
                    response_text = self._format_system_response("bluetooth", result)
                else:
                    return ResponseFrame(id=request_id, ok=False, error="Unknown /system subcommand. Use /system help.")
            except Exception as exc:
                return ResponseFrame(id=request_id, ok=False, error=str(exc))
            return ResponseFrame(id=request_id, ok=True, payload={"queued": False, "session_key": session_key, "command_response": response_text})

        if command_name == "task":
            if not getattr(self.config.app_skills, "enabled", False) or not getattr(self.config.app_skills, "task_manager_enabled", True):
                return ResponseFrame(id=request_id, ok=False, error="Task Manager skill pack is not enabled.")
            subcommand, _subargs = self._split_command_arguments(arguments)
            subcommand = subcommand.lower()
            if subcommand in {"", "help"}:
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={"queued": False, "session_key": session_key, "command_response": self._task_help_text()},
                )
            try:
                if subcommand == "open":
                    result = await self.tool_registry.dispatch("task_manager_open", {})
                    response_text = self._format_task_response("open", result)
                elif subcommand == "summary":
                    result = await self.tool_registry.dispatch("task_manager_summary", {})
                    response_text = self._format_task_response("summary", result)
                else:
                    return ResponseFrame(id=request_id, ok=False, error="Unknown /task subcommand. Use /task help.")
            except Exception as exc:
                return ResponseFrame(id=request_id, ok=False, error=str(exc))
            return ResponseFrame(id=request_id, ok=True, payload={"queued": False, "session_key": session_key, "command_response": response_text})

        if command_name == "preset":
            if not getattr(self.config.app_skills, "enabled", False) or not getattr(self.config.app_skills, "presets_enabled", True):
                return ResponseFrame(id=request_id, ok=False, error="Preset skills are not enabled.")
            user_id = await self._resolve_user_id(connection_id, session_key)
            subcommand, subargs = self._split_command_arguments(arguments)
            subcommand = subcommand.lower()
            if subcommand in {"", "help"}:
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={"queued": False, "session_key": session_key, "command_response": self._preset_help_text()},
                )
            try:
                if subcommand == "list":
                    result = await self.tool_registry.dispatch("preset_list", {})
                    response_text = self._format_preset_response("list", result)
                elif subcommand == "run":
                    if not subargs.strip():
                        return ResponseFrame(id=request_id, ok=False, error="Use /preset run <study-mode|work-mode|meeting-mode>.")
                    result = await self.tool_registry.dispatch("preset_run", {"name": subargs.strip(), "user_id": user_id})
                    response_text = self._format_preset_response("run", result)
                else:
                    return ResponseFrame(id=request_id, ok=False, error="Unknown /preset subcommand. Use /preset help.")
            except Exception as exc:
                return ResponseFrame(id=request_id, ok=False, error=str(exc))
            return ResponseFrame(id=request_id, ok=True, payload={"queued": False, "session_key": session_key, "command_response": response_text})

        if command_name == "browser-skill":
            if not getattr(self.config.app_skills, "enabled", False) or not getattr(self.config.app_skills, "browser_enabled", True):
                return ResponseFrame(id=request_id, ok=False, error="Browser skill pack is not enabled.")
            user_id = await self._resolve_user_id(connection_id, session_key)
            subcommand, subargs = self._split_command_arguments(arguments)
            subcommand = subcommand.lower()
            if subcommand in {"", "help"}:
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={"queued": False, "session_key": session_key, "command_response": self._browser_skill_help_text()},
                )
            try:
                if subcommand == "open":
                    workspace = subargs.strip()
                    if not workspace:
                        return ResponseFrame(id=request_id, ok=False, error="Use /browser-skill open <study|work|meeting>.")
                    result = await self.tool_registry.dispatch("browser_workspace_open", {"workspace": workspace, "user_id": user_id})
                    response_text = self._format_browser_skill_response(result)
                else:
                    return ResponseFrame(id=request_id, ok=False, error="Unknown /browser-skill subcommand. Use /browser-skill help.")
            except Exception as exc:
                return ResponseFrame(id=request_id, ok=False, error=str(exc))
            return ResponseFrame(id=request_id, ok=True, payload={"queued": False, "session_key": session_key, "command_response": response_text})

        if command_name == "browser":
            if not self.tool_registry.has("browser_sessions_list"):
                return ResponseFrame(id=request_id, ok=False, error="Browser automation is not configured.")
            user_id = await self._resolve_user_id(connection_id, session_key)
            subcommand, subargs = self._split_command_arguments(arguments)
            subcommand = subcommand.lower()
            if subcommand in {"", "help"}:
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={
                        "queued": False,
                        "session_key": session_key,
                        "command_response": self._browser_help_text(),
                    },
                )
            if subcommand in {"profiles", "sessions"}:
                result = await self.tool_registry.dispatch("browser_sessions_list", {"user_id": user_id})
                response_text = self._format_browser_profiles_response(result.get("sessions", []))
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={"queued": False, "session_key": session_key, "command_response": response_text},
                )
            if subcommand == "state":
                runtime = getattr(self.tool_registry, "browser_runtime", None)
                state = runtime.current_state() if runtime is not None else {}
                response_text = self._format_browser_state_response(state)
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={"queued": False, "session_key": session_key, "command_response": response_text},
                )
            if subcommand == "tabs":
                result = await self.tool_registry.dispatch("browser_tabs_list", {"user_id": user_id})
                response_text = self._format_browser_tabs_response(result)
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={"queued": False, "session_key": session_key, "command_response": response_text},
                )
            if subcommand == "downloads":
                limit = self._parse_browser_limit(subargs, default=8)
                result = await self.tool_registry.dispatch("browser_downloads_list", {"limit": limit, "user_id": user_id})
                response_text = self._format_browser_downloads_response(result.get("downloads", []))
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={"queued": False, "session_key": session_key, "command_response": response_text},
                )
            if subcommand == "logs":
                limit = self._parse_browser_limit(subargs, default=8)
                result = await self.tool_registry.dispatch("browser_logs", {"limit": limit, "user_id": user_id})
                response_text = self._format_browser_logs_response(result.get("logs", []))
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={"queued": False, "session_key": session_key, "command_response": response_text},
                )
            if subcommand == "open":
                url = subargs.strip()
                if not url:
                    return ResponseFrame(id=request_id, ok=False, error="Use /browser open <url>.")
                result = await self.tool_registry.dispatch("browser_tab_open", {"url": url, "user_id": user_id})
                response_text = (
                    f"Opened a new browser tab.\n"
                    f"Title: {result.get('title', '(unknown)')}\n"
                    f"URL: {result.get('url', url)}\n"
                    f"Tab id: {result.get('tab_id', 'unknown')}"
                )
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={"queued": False, "session_key": session_key, "command_response": response_text},
                )
            if subcommand == "switch":
                tab_id = subargs.strip()
                if not tab_id:
                    return ResponseFrame(id=request_id, ok=False, error="Use /browser switch <tab_id>.")
                result = await self.tool_registry.dispatch("browser_tab_switch", {"tab_id": tab_id, "user_id": user_id})
                response_text = (
                    f"Switched to tab {result.get('tab_id', tab_id)}.\n"
                    f"Title: {result.get('title', '(unknown)')}\n"
                    f"URL: {result.get('url', 'unknown')}"
                )
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={"queued": False, "session_key": session_key, "command_response": response_text},
                )
            if subcommand == "close":
                tab_id = subargs.strip()
                if not tab_id:
                    return ResponseFrame(id=request_id, ok=False, error="Use /browser close <tab_id>.")
                result = await self.tool_registry.dispatch("browser_tab_close", {"tab_id": tab_id, "user_id": user_id})
                response_text = (
                    f"Closed tab {tab_id}.\n"
                    f"Current tab: {result.get('current_tab_id', 'none')}"
                )
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={"queued": False, "session_key": session_key, "command_response": response_text},
                )
            if subcommand == "screenshot":
                result = await self.tool_registry.dispatch("browser_screenshot", {"user_id": user_id})
                response_text = (
                    f"Captured a browser screenshot.\n"
                    f"URL: {result.get('url', 'unknown')}\n"
                    f"Tab id: {result.get('tab_id', 'unknown')}\n"
                    f"Saved at: {result.get('path', 'unknown')}"
                )
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={"queued": False, "session_key": session_key, "command_response": response_text},
                )
            if subcommand == "login":
                site_name, profile_name = self._parse_browser_login_arguments(subargs)
                if not site_name:
                    return ResponseFrame(id=request_id, ok=False, error="Use /browser login <site_name> [profile_name].")
                result = await self.tool_registry.dispatch(
                    "browser_login",
                    {"site_name": site_name, "profile_name": profile_name or "default", "user_id": user_id},
                )
                response_text = (
                    f"Saved browser profile '{result.get('profile_name', profile_name or 'default')}' for {result.get('site_name', site_name)}.\n"
                    f"Status: {result.get('status', 'active')}\n"
                    f"URL: {result.get('url', 'unknown')}"
                )
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={"queued": False, "session_key": session_key, "command_response": response_text},
                )
            return ResponseFrame(id=request_id, ok=False, error="Unknown /browser subcommand. Use /browser help.")

        if command_name == "pause-rule":
            user_id = await self._resolve_user_id(connection_id, session_key)
            rule_name = arguments.strip()
            if not rule_name:
                return ResponseFrame(id=request_id, ok=False, error="Use /pause-rule <rule_name>.")
            await self.automation_engine.pause_rule(user_id, rule_name)
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={"queued": False, "session_key": session_key, "command_response": f"Paused rule '{rule_name}'."},
            )

        if command_name == "resume-rule":
            user_id = await self._resolve_user_id(connection_id, session_key)
            rule_name = arguments.strip()
            if not rule_name:
                return ResponseFrame(id=request_id, ok=False, error="Use /resume-rule <rule_name>.")
            await self.automation_engine.resume_rule(user_id, rule_name)
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={"queued": False, "session_key": session_key, "command_response": f"Resumed rule '{rule_name}'."},
            )

        if command_name == "replay-run":
            run_id = arguments.strip()
            if not run_id:
                return ResponseFrame(id=request_id, ok=False, error="Use /replay-run <run_id>.")
            replay = await self.automation_engine.replay_run(run_id)
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={"queued": False, "session_key": session_key, "command_response": json.dumps(replay, indent=2)},
            )

        if command_name == "approvals":
            user_id = await self._resolve_user_id(connection_id, session_key)
            approvals = await self.automation_engine.list_approvals(user_id)
            if approvals:
                lines = [f"- {item['approval_id']}: {item['status']} ({item['action']})" for item in approvals[:10]]
                response_text = "Approval queue:\n" + "\n".join(lines)
            else:
                response_text = "No pending approvals."
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={"queued": False, "session_key": session_key, "command_response": response_text, "approvals": approvals},
            )

        if command_name in {"approve", "reject"}:
            approval_id = arguments.strip()
            if not approval_id:
                return ResponseFrame(id=request_id, ok=False, error=f"Use /{command_name} <approval_id>.")
            decision = "approved" if command_name == "approve" else "rejected"
            await self.automation_engine.decide_approval(approval_id, decision)
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={"queued": False, "session_key": session_key, "command_response": f"{decision.title()} approval '{approval_id}'."},
            )

        if command_name in {"host-approvals", "host_approvals"}:
            if self.system_access_manager is None:
                return ResponseFrame(id=request_id, ok=False, error="System access is not configured.")
            user_id = await self._resolve_user_id(connection_id, session_key)
            approvals = await self.system_access_manager.list_approvals(user_id)
            if approvals:
                lines = [
                    f"- {item['approval_id']}: {item['status']} ({item['action_kind']} -> {item['target_summary']})"
                    for item in approvals[:10]
                ]
                response_text = "Host approval queue:\n" + "\n".join(lines)
            else:
                response_text = "No pending or recent host approvals."
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={
                    "queued": False,
                    "session_key": session_key,
                    "command_response": response_text,
                    "host_approvals": approvals,
                },
            )

        if command_name in {"host-approve", "host_approve", "host-reject", "host_reject"}:
            if self.system_access_manager is None:
                return ResponseFrame(id=request_id, ok=False, error="System access is not configured.")
            approval_id = arguments.strip()
            if not approval_id:
                user_id = await self._resolve_user_id(connection_id, session_key)
                approval_id, info_text = await self._resolve_default_host_approval(user_id)
                if approval_id is None:
                    return ResponseFrame(
                        id=request_id,
                        ok=True,
                        payload={
                            "queued": False,
                            "session_key": session_key,
                            "command_response": info_text or "No host approvals found.",
                        },
                    )
            decision = "approved" if "approve" in command_name else "rejected"
            approval = await self.system_access_manager.decide_approval(approval_id, decision)
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={
                    "queued": False,
                    "session_key": session_key,
                    "command_response": f"{decision.title()} host approval '{approval_id}'.",
                    "host_approval": approval,
                },
            )

        skill = self.skill_registry.find_user_invocable(command_name)
        if skill is not None:
            skill_prompt = self.skill_registry.load_skill_prompt(skill.name)
            await self.hook_runner.fire_event(
                f"command:{command_name}",
                context={
                    "session_key": session_key,
                    "command": command_name,
                    "arguments": arguments,
                    "skill": skill.name,
                    "logs_dir": str(self.config.logs_dir),
                },
            )
            await self.agent_loop.enqueue(
                AgentRequest(
                    connection_id=connection_id,
                    session_key=session_key,
                    message=raw_command,
                    request_id=request_id,
                    mode=QueueMode.STEER,
                    metadata={"skill_command": skill.name, "activated_skill": skill.name, "skill_activation_source": "slash"},
                    system_suffix=f"## Active Skill\n{skill_prompt}",
                )
            )
            self._log_skill_activation(skill.name, "slash", "")
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={"queued": True, "session_key": session_key, "skill": skill.name, "activated_skill": skill.name},
            )

        return ResponseFrame(id=request_id, ok=False, error=f"Unknown slash command '/{command_name}'.")

    async def _start_oauth_flow(self, request_id: str, session_key: str, provider: str) -> ResponseFrame:
        try:
            result = await self.oauth_flow_manager.start_oauth_flow(provider)
        except Exception as exc:
            return ResponseFrame(id=request_id, ok=False, error=str(exc))
        response_text = (
            f"Open this URL in your browser to connect {provider}:\n"
            f"{result['authorize_url']}\n\n"
            f"After approving access, SonarBot will receive the callback at:\n{result['redirect_uri']}\n\n"
            "Then come back here and run /oauth-status."
        )
        return ResponseFrame(
            id=request_id,
            ok=True,
            payload={
                "queued": False,
                "session_key": session_key,
                "command_response": response_text,
                "authorize_url": result["authorize_url"],
                "redirect_uri": result["redirect_uri"],
                "provider": provider,
            },
        )

    def _flatten_hook_messages(self, messages: list[dict[str, Any]]) -> str:
        lines = []
        for item in messages:
            text = item.get("text") or item.get("content")
            if text:
                lines.append(str(text))
        return "\n\n".join(lines)

    def _match_oauth_connect_request(self, message: str) -> str | None:
        lowered = message.lower()
        if "google" in lowered and ("connect" in lowered or "oauth" in lowered) and "account" in lowered:
            return "google"
        if "github" in lowered and ("connect" in lowered or "oauth" in lowered) and "account" in lowered:
            return "github"
        if re.search(r"\bconnect\b.*\bgoogle\b", lowered):
            return "google"
        if re.search(r"\bconnect\b.*\bgithub\b", lowered):
            return "github"
        return None

    def _looks_like_oauth_status_request(self, message: str) -> bool:
        lowered = message.lower().strip()
        return lowered in {"oauth status", "show oauth status", "show connected oauth providers"}

    async def _resolve_skill_intent(self, message: str) -> tuple[Any, str] | None:
        matches = self.skill_registry.match_natural_language(message)
        if not matches:
            return None

        top = matches[0]
        if top.exact:
            return top.skill, "exact"

        if top.score >= 6:
            second_score = matches[1].score if len(matches) > 1 else -999
            if top.score - second_score >= 3:
                return top.skill, "heuristic"

        plausible = [item for item in matches[:3] if item.score > 0]
        if len(plausible) < 2:
            return None

        classified = await self._classify_skill_match(message, plausible)
        if classified is None:
            return None
        return classified, "classifier"

    async def _classify_skill_match(self, message: str, matches: list[Any]) -> Any | None:
        has_tool = getattr(self.tool_registry, "has", None)
        if callable(has_tool) and not has_tool("llm_task"):
            return None

        prompt_lines = [
            "Choose the best SonarBot skill for the user's message.",
            "Return strict JSON only: {\"skill\": \"<name-or-none>\", \"confidence\": <0.0-1.0>}.",
            "Only choose a skill if the fit is strong. Otherwise return {\"skill\": \"none\", \"confidence\": 0}.",
            f"User message: {message}",
            "Candidate skills:",
        ]
        for item in matches:
            skill = item.skill
            prompt_lines.append(
                json.dumps(
                    {
                        "name": skill.name,
                        "description": skill.description,
                        "aliases": skill.aliases,
                        "activation_examples": skill.activation_examples,
                        "keywords": skill.keywords,
                    },
                    ensure_ascii=False,
                )
            )
        try:
            result = await self.tool_registry.dispatch(
                "llm_task",
                {"prompt": "\n".join(prompt_lines), "model": "cheap"},
            )
        except Exception:
            return None

        content = str(result.get("content", "")).strip()
        payload = self._parse_classifier_payload(content)
        if not payload:
            return None
        selected_name = str(payload.get("skill", "none")).strip()
        confidence = payload.get("confidence", 0)
        try:
            confidence_value = float(confidence)
        except (TypeError, ValueError):
            return None
        if confidence_value < 0.8 or selected_name.lower() == "none":
            return None
        for item in matches:
            if item.skill.name == selected_name:
                return item.skill
        return None

    def _parse_classifier_payload(self, content: str) -> dict[str, Any] | None:
        if not content:
            return None
        candidate = content.strip()
        fenced = re.search(r"\{.*\}", candidate, flags=re.DOTALL)
        if fenced is not None:
            candidate = fenced.group(0)
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    async def _handle_tool_shortcut(
        self,
        request_id: str,
        session_key: str,
        message: str,
        metadata: dict[str, Any],
    ) -> ResponseFrame | None:
        lowered = message.lower().strip()
        cron_shortcut = await self._handle_natural_language_cron_shortcut(
            request_id,
            session_key,
            message,
            lowered,
            metadata,
        )
        if cron_shortcut is not None:
            return cron_shortcut

        desktop_routine_shortcut = await self._handle_desktop_routine_shortcut(
            request_id,
            session_key,
            message,
            lowered,
            metadata,
        )
        if desktop_routine_shortcut is not None:
            return desktop_routine_shortcut

        one_time_shortcut = await self._handle_natural_language_one_time_reminder_shortcut(
            request_id,
            session_key,
            message,
            lowered,
            metadata,
        )
        if one_time_shortcut is not None:
            return one_time_shortcut

        desktop_vision_shortcut = await self._handle_desktop_vision_shortcut(
            request_id,
            session_key,
            message,
            lowered,
            metadata,
        )
        if desktop_vision_shortcut is not None:
            return desktop_vision_shortcut

        desktop_input_shortcut = await self._handle_desktop_input_shortcut(
            request_id,
            session_key,
            message,
            lowered,
            metadata,
        )
        if desktop_input_shortcut is not None:
            return desktop_input_shortcut

        desktop_coworker_shortcut = await self._handle_desktop_coworker_shortcut(
            request_id,
            session_key,
            message,
            lowered,
            metadata,
        )
        if desktop_coworker_shortcut is not None:
            return desktop_coworker_shortcut

        app_skill_shortcut = await self._handle_app_skill_shortcut(
            request_id,
            session_key,
            message,
            lowered,
            metadata,
        )
        if app_skill_shortcut is not None:
            return app_skill_shortcut

        app_control_shortcut = await self._handle_app_control_shortcut(
            request_id,
            session_key,
            message,
            lowered,
            metadata,
        )
        if app_control_shortcut is not None:
            return app_control_shortcut

        desktop_automation_shortcut = await self._handle_desktop_automation_shortcut(
            request_id,
            session_key,
            message,
            lowered,
            metadata,
        )
        if desktop_automation_shortcut is not None:
            return desktop_automation_shortcut

        host_shortcut = await self._handle_host_shortcut(request_id, session_key, message, lowered, metadata)
        if host_shortcut is not None:
            return host_shortcut

        if self._looks_like_latest_email_request(lowered):
            try:
                result = await self.tool_registry.dispatch("gmail_latest_email", {"session_key": session_key})
            except Exception as exc:
                return ResponseFrame(id=request_id, ok=False, error=str(exc))
            response_text = self._format_latest_email_response(result)
            await self._persist_inline_exchange(session_key, message, response_text, metadata)
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={"queued": False, "session_key": session_key, "command_response": response_text},
            )

        if self._looks_like_repo_count_request(lowered):
            try:
                result = await self.tool_registry.dispatch("github_list_repos", {"limit": 50, "session_key": session_key})
            except Exception as exc:
                return ResponseFrame(id=request_id, ok=False, error=str(exc))
            repositories = result.get("repositories", []) if isinstance(result, dict) else []
            response_text = f"You currently have {len(repositories)} repositories visible through the connected GitHub account."
            await self._persist_inline_exchange(session_key, message, response_text, metadata)
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={"queued": False, "session_key": session_key, "command_response": response_text},
            )

        if self._looks_like_pull_request_check(lowered):
            repo_ref = await self._resolve_repo_reference(session_key, message)
            if repo_ref is None:
                response_text = (
                    "I need the repository name to check pull requests. Tell me the repo as owner/name, "
                    "for example Rishiraj-Yadav/Personal-AI-Assistant."
                )
                await self._persist_inline_exchange(session_key, message, response_text, metadata)
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={"queued": False, "session_key": session_key, "command_response": response_text},
                )
            owner, repo = repo_ref
            try:
                result = await self.tool_registry.dispatch(
                    "github_list_pull_requests",
                    {"owner": owner, "repo": repo, "limit": 20, "state": "open", "session_key": session_key},
                )
            except Exception as exc:
                return ResponseFrame(id=request_id, ok=False, error=str(exc))
            response_text = self._format_pull_request_response(owner, repo, result)
            await self._persist_inline_exchange(session_key, message, response_text, metadata)
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={"queued": False, "session_key": session_key, "command_response": response_text},
            )

        return None

    async def _handle_natural_language_cron_shortcut(
        self,
        request_id: str,
        session_key: str,
        original_message: str,
        lowered: str,
        metadata: dict[str, Any],
    ) -> ResponseFrame | None:
        parsed = self._parse_natural_language_cron_request(original_message, lowered)
        if parsed is None:
            return None
        schedule, cron_message = parsed
        user_id = str(metadata.get("user_id") or self.config.users.default_user_id)
        try:
            job = await self.automation_engine.create_dynamic_cron_job(user_id, schedule, cron_message)
        except ValueError as exc:
            return ResponseFrame(id=request_id, ok=False, error=str(exc))
        response_text = (
            f"Created cron job '{job['cron_id']}' on {job['schedule']}.\n"
            f"Message: {job['message']}"
        )
        await self._persist_inline_exchange(session_key, original_message, response_text, metadata)
        return ResponseFrame(
            id=request_id,
            ok=True,
            payload={
                "queued": False,
                "session_key": session_key,
                "command_response": response_text,
                "cron_job": job,
            },
        )

    async def _handle_natural_language_one_time_reminder_shortcut(
        self,
        request_id: str,
        session_key: str,
        original_message: str,
        lowered: str,
        metadata: dict[str, Any],
    ) -> ResponseFrame | None:
        parsed = self._parse_natural_language_one_time_reminder_request(lowered)
        if parsed is None:
            return None
        run_at, reminder_message = parsed
        user_id = str(metadata.get("user_id") or self.config.users.default_user_id)
        try:
            reminder = await self.automation_engine.create_one_time_reminder(user_id, run_at, reminder_message)
        except ValueError as exc:
            return ResponseFrame(id=request_id, ok=False, error=str(exc))
        local_time = run_at.astimezone().strftime("%Y-%m-%d %I:%M %p")
        response_text = (
            f"Created one-time reminder '{reminder['reminder_id']}' for {local_time}.\n"
            f"Message: {reminder['message']}"
        )
        await self._persist_inline_exchange(session_key, original_message, response_text, metadata)
        return ResponseFrame(
            id=request_id,
            ok=True,
            payload={
                "queued": False,
                "session_key": session_key,
                "command_response": response_text,
                "one_time_reminder": reminder,
            },
        )

    async def _handle_desktop_vision_shortcut(
        self,
        request_id: str,
        session_key: str,
        original_message: str,
        lowered: str,
        metadata: dict[str, Any],
    ) -> ResponseFrame | None:
        parsed = self._parse_desktop_vision_request(lowered)
        if parsed is None:
            return None
        if not getattr(self.config.desktop_vision, "enabled", False):
            response_text = "Desktop vision is not enabled."
            await self._persist_inline_exchange(session_key, original_message, response_text, metadata)
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={"queued": False, "session_key": session_key, "command_response": response_text},
            )
        tool_name = {
            "active": "desktop_active_window",
            "capture": "desktop_screenshot",
            "window": "desktop_window_screenshot",
            "read": "desktop_read_screen",
        }[parsed["action"]]
        tool_payload = {"target": parsed["target"]} if parsed["action"] == "read" else {}
        try:
            result = await self.tool_registry.dispatch(tool_name, tool_payload)
        except Exception as exc:
            return ResponseFrame(id=request_id, ok=False, error=str(exc))
        response_text = self._format_desktop_vision_response(parsed["action"], result)
        await self._persist_inline_exchange(session_key, original_message, response_text, metadata)
        return ResponseFrame(
            id=request_id,
            ok=True,
            payload={"queued": False, "session_key": session_key, "command_response": response_text},
        )

    async def _handle_desktop_input_shortcut(
        self,
        request_id: str,
        session_key: str,
        original_message: str,
        lowered: str,
        metadata: dict[str, Any],
    ) -> ResponseFrame | None:
        parsed = self._parse_desktop_input_request(lowered)
        if parsed is None:
            return None
        if not getattr(self.config.desktop_input, "enabled", False):
            response_text = "Desktop input is not enabled."
            await self._persist_inline_exchange(session_key, original_message, response_text, metadata)
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={"queued": False, "session_key": session_key, "command_response": response_text},
            )
        try:
            if parsed["action"] == "copy-selected":
                hotkey_result = await self.tool_registry.dispatch("desktop_keyboard_hotkey", {"hotkey": "ctrl+c"})
                hotkey_status = str(hotkey_result.get("status", "completed"))
                if hotkey_status.startswith("blocked") or hotkey_status in {"rejected", "expired", "failed"}:
                    response_text = self._format_desktop_input_response("hotkey", hotkey_result)
                else:
                    read_result = await self.tool_registry.dispatch("desktop_clipboard_read", {})
                    response_text = self._format_desktop_input_response("clipboard-read", read_result)
            elif parsed["action"] == "clipboard-read":
                result = await self.tool_registry.dispatch("desktop_clipboard_read", {})
                response_text = self._format_desktop_input_response("clipboard-read", result)
            elif parsed["action"] == "clipboard-write":
                result = await self.tool_registry.dispatch("desktop_clipboard_write", {"text": str(parsed["text"])})
                response_text = self._format_desktop_input_response("clipboard-write", result)
            elif parsed["action"] == "move":
                result = await self.tool_registry.dispatch("desktop_mouse_move", {"x": parsed["x"], "y": parsed["y"]})
                response_text = self._format_desktop_input_response("move", result)
            elif parsed["action"] in {"click", "right-click", "double-click"}:
                payload = {"x": parsed["x"], "y": parsed["y"]}
                if parsed["action"] == "right-click":
                    payload["button"] = "right"
                elif parsed["action"] == "double-click":
                    payload["count"] = 2
                result = await self.tool_registry.dispatch("desktop_mouse_click", payload)
                response_text = self._format_desktop_input_response(parsed["action"], result)
            elif parsed["action"] == "scroll":
                result = await self.tool_registry.dispatch(
                    "desktop_mouse_scroll",
                    {"direction": parsed["direction"], "amount": parsed["amount"]},
                )
                response_text = self._format_desktop_input_response("scroll", result)
            elif parsed["action"] == "type":
                result = await self.tool_registry.dispatch("desktop_keyboard_type", {"text": str(parsed["text"])})
                response_text = self._format_desktop_input_response("type", result)
            else:
                result = await self.tool_registry.dispatch("desktop_keyboard_hotkey", {"hotkey": str(parsed["hotkey"])})
                response_text = self._format_desktop_input_response("hotkey", result)
        except Exception as exc:
            return ResponseFrame(id=request_id, ok=False, error=str(exc))
        await self._persist_inline_exchange(session_key, original_message, response_text, metadata)
        return ResponseFrame(
            id=request_id,
            ok=True,
            payload={"queued": False, "session_key": session_key, "command_response": response_text},
        )

    async def _handle_desktop_coworker_shortcut(
        self,
        request_id: str,
        session_key: str,
        original_message: str,
        lowered: str,
        metadata: dict[str, Any],
    ) -> ResponseFrame | None:
        if self.coworker_service is None or not getattr(self.config.desktop_coworker, "enabled", False):
            return None
        parsed = await self._parse_desktop_coworker_request(original_message, lowered)
        if parsed is None:
            return None
        user_id = str(metadata.get("user_id") or self.config.users.default_user_id)
        try:
            task = await self.coworker_service.run_task_request(
                user_id=user_id,
                session_key=session_key,
                request_text=str(parsed["request"]),
                connection_id=str(metadata.get("connection_id", "")),
                channel_name=str(metadata.get("channel", "")),
            )
        except Exception as exc:
            return ResponseFrame(id=request_id, ok=False, error=str(exc))
        response_text = self._format_coworker_task(task)
        await self._persist_inline_exchange(session_key, original_message, response_text, metadata)
        return ResponseFrame(
            id=request_id,
            ok=True,
            payload={"queued": False, "session_key": session_key, "command_response": response_text},
        )

    async def _handle_app_skill_shortcut(
        self,
        request_id: str,
        session_key: str,
        original_message: str,
        lowered: str,
        metadata: dict[str, Any],
    ) -> ResponseFrame | None:
        parsed = self._parse_app_skill_request(original_message, lowered)
        if parsed is None:
            return None
        if not getattr(self.config.app_skills, "enabled", False):
            response_text = "App skills are not enabled."
            await self._persist_inline_exchange(session_key, original_message, response_text, metadata)
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={"queued": False, "session_key": session_key, "command_response": response_text},
            )
        user_id = str(metadata.get("user_id") or self.config.users.default_user_id)
        context = self._app_skill_context(
            session_key,
            user_id,
            connection_id=str(metadata.get("connection_id", "")),
            channel_name=str(metadata.get("channel", "")),
        )
        try:
            action = str(parsed["action"])
            if action == "task-open":
                result = await self.tool_registry.dispatch("task_manager_open", {})
                response_text = self._format_task_response("open", result)
            elif action == "task-summary":
                result = await self.tool_registry.dispatch("task_manager_summary", {})
                response_text = self._format_task_response("summary", result)
            elif action == "settings":
                result = await self.tool_registry.dispatch("system_open_settings", {"page": str(parsed["page"]), **context})
                response_text = self._format_system_response("settings", result)
            elif action == "volume":
                result = await self.tool_registry.dispatch("system_volume_status", {})
                response_text = self._format_system_response("volume", result)
            elif action == "volume-set":
                result = await self.tool_registry.dispatch("system_volume_set", {"percent": int(parsed["percent"]), **context})
                response_text = self._format_system_response("volume-set", result)
            elif action == "brightness":
                result = await self.tool_registry.dispatch("system_brightness_status", {})
                response_text = self._format_system_response("brightness", result)
            elif action == "brightness-set":
                result = await self.tool_registry.dispatch("system_brightness_set", {"percent": int(parsed["percent"]), **context})
                response_text = self._format_system_response("brightness-set", result)
            elif action == "bluetooth":
                result = await self.tool_registry.dispatch("system_bluetooth_status", {})
                response_text = self._format_system_response("bluetooth", result)
            elif action == "preset-run":
                result = await self.tool_registry.dispatch("preset_run", {"name": str(parsed["name"]), "user_id": user_id})
                response_text = self._format_preset_response("run", result)
            elif action == "vscode-open":
                result = await self.tool_registry.dispatch(
                    "vscode_open_target",
                    {"target": str(parsed["target"]), "prefer": str(parsed.get("prefer", "either")), **context},
                )
                response_text = self._format_vscode_response("open", result)
            elif action == "browser-workspace":
                result = await self.tool_registry.dispatch(
                    "browser_workspace_open",
                    {"workspace": str(parsed["workspace"]), "user_id": user_id},
                )
                response_text = self._format_browser_skill_response(result)
            else:
                return None
        except Exception as exc:
            return ResponseFrame(id=request_id, ok=False, error=str(exc))
        await self._persist_inline_exchange(session_key, original_message, response_text, metadata)
        return ResponseFrame(
            id=request_id,
            ok=True,
            payload={"queued": False, "session_key": session_key, "command_response": response_text},
        )

    async def _handle_app_control_shortcut(
        self,
        request_id: str,
        session_key: str,
        original_message: str,
        lowered: str,
        metadata: dict[str, Any],
    ) -> ResponseFrame | None:
        parsed = self._parse_app_control_request(lowered)
        if parsed is None:
            return None
        if not getattr(self.config.desktop_apps, "enabled", False):
            if parsed["action"] == "open" and parsed["target"] in {"notepad", "calculator", "paint", "explorer"}:
                return None
            response_text = "Desktop app control is not enabled."
            await self._persist_inline_exchange(session_key, original_message, response_text, metadata)
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={"queued": False, "session_key": session_key, "command_response": response_text},
            )
        tool_name = {
            "open": "apps_open",
            "focus": "apps_focus",
            "minimize": "apps_minimize",
            "maximize": "apps_maximize",
            "restore": "apps_restore",
            "snap": "apps_snap",
        }[parsed["action"]]
        tool_payload = {"target": parsed["target"]}
        if parsed["action"] == "snap":
            tool_payload["position"] = parsed["position"]
        try:
            result = await self.tool_registry.dispatch(tool_name, tool_payload)
        except Exception as exc:
            return ResponseFrame(id=request_id, ok=False, error=str(exc))
        response_text = self._format_app_control_response(parsed["action"], result)
        await self._persist_inline_exchange(session_key, original_message, response_text, metadata)
        return ResponseFrame(
            id=request_id,
            ok=True,
            payload={"queued": False, "session_key": session_key, "command_response": response_text},
        )

    async def _handle_desktop_automation_shortcut(
        self,
        request_id: str,
        session_key: str,
        original_message: str,
        lowered: str,
        metadata: dict[str, Any],
    ) -> ResponseFrame | None:
        if self.automation_engine is None or not getattr(self.config.automation.desktop, "enabled", False):
            return None
        management_response = await self._handle_desktop_automation_management_shortcut(
            session_key,
            original_message,
            lowered,
            metadata,
        )
        if management_response is not None:
            await self._persist_inline_exchange(session_key, original_message, management_response, metadata)
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={"queued": False, "session_key": session_key, "command_response": management_response},
            )
        parsed = await self._parse_desktop_automation_request(session_key, original_message, lowered, metadata)
        if parsed is None:
            return None
        if parsed.get("response_text"):
            response_text = str(parsed["response_text"])
        else:
            user_id = str(metadata.get("user_id") or self.config.users.default_user_id)
            rule = await self.automation_engine.create_desktop_automation_rule(user_id, **parsed)
            response_text = self._format_desktop_automation_creation_response(rule)
        await self._persist_inline_exchange(session_key, original_message, response_text, metadata)
        return ResponseFrame(
            id=request_id,
            ok=True,
            payload={"queued": False, "session_key": session_key, "command_response": response_text},
        )

    async def _handle_desktop_automation_management_shortcut(
        self,
        session_key: str,
        original_message: str,
        lowered: str,
        metadata: dict[str, Any],
    ) -> str | None:
        normalized = re.sub(r"\s+", " ", lowered).strip()
        if self.automation_engine is None or not getattr(self.config.automation.desktop, "enabled", False):
            return None
        user_id = str(metadata.get("user_id") or self.config.users.default_user_id)
        if normalized in {
            "list my desktop automations",
            "show my desktop automations",
            "list desktop automations",
            "show desktop automations",
            "what desktop automations do i have",
        }:
            rules = await self.automation_engine.list_rules(user_id)
            return self._format_desktop_rules_response(self._filter_desktop_rules(rules))
        action_match = re.match(
            r"^(?P<action>pause|resume|delete|remove)\s+(?:my\s+)?desktop\s+automation\s+(?P<name>.+)$",
            normalized,
            flags=re.IGNORECASE,
        )
        if action_match is None:
            return None
        rules = await self.automation_engine.list_rules(user_id)
        desktop_rules = self._filter_desktop_rules(rules)
        matched_rule, error_text = self._match_desktop_rule_reference(desktop_rules, str(action_match.group("name")))
        if error_text:
            return error_text
        assert matched_rule is not None
        rule_name = str(matched_rule.get("name", ""))
        display_name = str(matched_rule.get("display_name") or rule_name)
        action = str(action_match.group("action")).lower()
        if action == "pause":
            await self.automation_engine.pause_rule(user_id, rule_name)
            return f"Paused desktop automation '{display_name}'."
        if action == "resume":
            await self.automation_engine.resume_rule(user_id, rule_name)
            return f"Resumed desktop automation '{display_name}'."
        await self.automation_engine.delete_rule(user_id, rule_name)
        return f"Deleted desktop automation '{display_name}'."

    async def _handle_desktop_routine_shortcut(
        self,
        request_id: str,
        session_key: str,
        original_message: str,
        lowered: str,
        metadata: dict[str, Any],
    ) -> ResponseFrame | None:
        if self.automation_engine is None or not getattr(self.config.automation.desktop, "enabled", False):
            return None
        management_response = await self._handle_desktop_routine_management_shortcut(
            session_key,
            original_message,
            lowered,
            metadata,
        )
        if management_response is not None:
            await self._persist_inline_exchange(session_key, original_message, management_response, metadata)
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={"queued": False, "session_key": session_key, "command_response": management_response},
            )
        parsed = await self._parse_desktop_routine_request(session_key, original_message, lowered, metadata)
        if parsed is None:
            return None
        if parsed.get("response_text"):
            response_text = str(parsed["response_text"])
        else:
            user_id = str(metadata.get("user_id") or self.config.users.default_user_id)
            routine = await self.automation_engine.create_desktop_routine_rule(user_id, **parsed)
            response_text = self._format_desktop_routine_creation_response(routine)
        await self._persist_inline_exchange(session_key, original_message, response_text, metadata)
        return ResponseFrame(
            id=request_id,
            ok=True,
            payload={"queued": False, "session_key": session_key, "command_response": response_text},
        )

    async def _handle_desktop_routine_management_shortcut(
        self,
        session_key: str,
        original_message: str,
        lowered: str,
        metadata: dict[str, Any],
    ) -> str | None:
        normalized = re.sub(r"\s+", " ", lowered).strip()
        if self.automation_engine is None or not getattr(self.config.automation.desktop, "enabled", False):
            return None
        if "desktop automation" in normalized:
            return None
        user_id = str(metadata.get("user_id") or self.config.users.default_user_id)
        if normalized in {
            "list my routines",
            "show my routines",
            "list routines",
            "show routines",
            "what routines do i have",
            "what desktop routines do i have",
        }:
            rules = await self.automation_engine.list_rules(user_id)
            return self._format_desktop_routines_response(self._filter_desktop_routines(rules))
        action_match = re.match(
            r"^(?P<action>run|start|launch|pause|resume|delete|remove|show)\s+(?:(?:my\s+)?(?:desktop\s+)?routine\s+)?(?P<name>.+)$",
            normalized,
            flags=re.IGNORECASE,
        )
        if action_match is None:
            return None
        action = str(action_match.group("action")).lower()
        raw_name = str(action_match.group("name")).strip()
        rules = await self.automation_engine.list_rules(user_id)
        desktop_routines = self._filter_desktop_routines(rules)
        matched_rule, error_text = self._match_desktop_routine_reference(desktop_routines, raw_name)
        if error_text:
            explicit_reference = "routine" in normalized or raw_name.lower().endswith(" mode")
            if action in {"run", "start", "launch"} and not explicit_reference:
                return None
            return error_text
        assert matched_rule is not None
        rule_name = str(matched_rule.get("name", ""))
        display_name = str(matched_rule.get("display_name") or rule_name)
        if action in {"run", "start", "launch"}:
            result = await self.automation_engine.run_desktop_routine_now(
                user_id=user_id,
                routine_id=rule_name.removeprefix("routine:"),
                notify=False,
            )
            return str(result.get("message", f"Ran desktop routine '{display_name}'.")) or f"Ran desktop routine '{display_name}'."
        if action == "show":
            return self._format_desktop_routine_show_response(matched_rule)
        if action == "pause":
            await self.automation_engine.pause_rule(user_id, rule_name)
            return f"Paused desktop routine '{display_name}'."
        if action == "resume":
            await self.automation_engine.resume_rule(user_id, rule_name)
            return f"Resumed desktop routine '{display_name}'."
        await self.automation_engine.delete_rule(user_id, rule_name)
        return f"Deleted desktop routine '{display_name}'."

    async def _handle_host_shortcut(
        self,
        request_id: str,
        session_key: str,
        original_message: str,
        lowered: str,
        metadata: dict[str, Any],
    ) -> ResponseFrame | None:
        if not self._has_host_tools():
            return None

        explicit_path = self._extract_explicit_host_path(original_message)
        folder_name = self._match_known_host_folder(lowered)

        explicit_root = self._extract_host_search_root(lowered)
        if explicit_path is not None and self._looks_like_list_folder_request(lowered):
            try:
                result = await self.tool_registry.dispatch(
                    "list_host_dir",
                    {
                        "path": explicit_path,
                        "session_key": session_key,
                        "user_id": metadata.get("user_id", self.config.users.default_user_id),
                    },
                )
            except Exception as exc:
                return ResponseFrame(id=request_id, ok=False, error=str(exc))
            response_text = self._format_host_directory_response(explicit_path, result)
            await self._persist_inline_exchange(session_key, original_message, response_text, metadata)
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={"queued": False, "session_key": session_key, "command_response": response_text},
            )

        if folder_name and self._looks_like_list_folder_request(lowered):
            folder_path = self._resolve_known_host_folder_path(folder_name)
            try:
                result = await self.tool_registry.dispatch(
                    "list_host_dir",
                    {
                        "path": folder_path,
                        "session_key": session_key,
                        "user_id": metadata.get("user_id", self.config.users.default_user_id),
                    },
                )
            except Exception as exc:
                return ResponseFrame(id=request_id, ok=False, error=str(exc))
            response_text = self._format_host_directory_response(folder_name, result)
            await self._persist_inline_exchange(session_key, original_message, response_text, metadata)
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={"queued": False, "session_key": session_key, "command_response": response_text},
            )

        browse_term = self._extract_host_browse_folder_term(lowered)

        if explicit_root is not None and self._looks_like_list_folder_request(lowered) and folder_name is None and browse_term is None:
            label = self._describe_host_root(explicit_root)
            try:
                result = await self.tool_registry.dispatch(
                    "list_host_dir",
                    {
                        "path": explicit_root,
                        "session_key": session_key,
                        "user_id": metadata.get("user_id", self.config.users.default_user_id),
                    },
                )
            except Exception as exc:
                return ResponseFrame(id=request_id, ok=False, error=str(exc))
            response_text = self._format_host_directory_response(label, result)
            await self._persist_inline_exchange(session_key, original_message, response_text, metadata)
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={"queued": False, "session_key": session_key, "command_response": response_text},
            )

        contextual_host_write = await self._parse_contextual_host_file_creation_request(
            session_key,
            original_message,
            metadata,
        )
        if contextual_host_write is not None:
            if contextual_host_write.get("response_text"):
                response_text = str(contextual_host_write["response_text"])
                await self._persist_inline_exchange(session_key, original_message, response_text, metadata)
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={"queued": False, "session_key": session_key, "command_response": response_text},
                )
            target_dir = str(contextual_host_write["target_dir"])
            filename = str(contextual_host_write["filename"])
            content = str(contextual_host_write["content"])
            try:
                result = await self.tool_registry.dispatch(
                    "write_host_file",
                    {
                        "path": f"{target_dir.rstrip('/\\')}/{filename}",
                        "content": content,
                        "session_key": session_key,
                        "user_id": metadata.get("user_id", self.config.users.default_user_id),
                        "channel_name": metadata.get("channel", ""),
                    },
                )
            except Exception as exc:
                return ResponseFrame(id=request_id, ok=False, error=str(exc))
            response_text = self._format_host_write_response(filename, target_dir, result)
            await self._persist_inline_exchange(session_key, original_message, response_text, metadata)
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={"queued": False, "session_key": session_key, "command_response": response_text},
            )

        contextual_host_update = await self._parse_contextual_host_file_update_request(
            session_key,
            original_message,
            metadata,
        )
        if contextual_host_update is not None:
            if contextual_host_update.get("response_text"):
                response_text = str(contextual_host_update["response_text"])
                await self._persist_inline_exchange(session_key, original_message, response_text, metadata)
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={"queued": False, "session_key": session_key, "command_response": response_text},
                )
            try:
                result = await self.tool_registry.dispatch(
                    "write_host_file",
                    {
                        "path": str(contextual_host_update["path"]),
                        "content": str(contextual_host_update["content"]),
                        "session_key": session_key,
                        "user_id": metadata.get("user_id", self.config.users.default_user_id),
                        "channel_name": metadata.get("channel", ""),
                    },
                )
            except Exception as exc:
                return ResponseFrame(id=request_id, ok=False, error=str(exc))
            final_path = str(result.get("path", contextual_host_update["path"]))
            response_text = self._format_host_write_response(
                Path(final_path).name,
                Path(final_path).parent.as_posix(),
                result,
            )
            await self._persist_inline_exchange(session_key, original_message, response_text, metadata)
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={"queued": False, "session_key": session_key, "command_response": response_text},
            )

        contextual_host_read = await self._parse_contextual_host_file_read_request(
            session_key,
            original_message,
            metadata,
        )
        if contextual_host_read is not None:
            if contextual_host_read.get("response_text"):
                response_text = str(contextual_host_read["response_text"])
                await self._persist_inline_exchange(session_key, original_message, response_text, metadata)
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={"queued": False, "session_key": session_key, "command_response": response_text},
                )
            try:
                result = await self.tool_registry.dispatch(
                    "read_host_file",
                    {
                        "path": str(contextual_host_read["path"]),
                        "session_key": session_key,
                        "user_id": metadata.get("user_id", self.config.users.default_user_id),
                    },
                )
            except Exception as exc:
                return ResponseFrame(id=request_id, ok=False, error=str(exc))
            response_text = self._format_host_read_response(str(contextual_host_read["path"]), result)
            await self._persist_inline_exchange(session_key, original_message, response_text, metadata)
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={"queued": False, "session_key": session_key, "command_response": response_text},
            )

        if browse_term and folder_name is None:
            try:
                result = await self.tool_registry.dispatch(
                    "search_host_files",
                    {
                        "root": explicit_root or "@allowed",
                        "pattern": "*",
                        "name_query": browse_term,
                        "directories_only": True,
                        "limit": 20,
                        "session_key": session_key,
                        "user_id": metadata.get("user_id", self.config.users.default_user_id),
                    },
                )
            except Exception as exc:
                return ResponseFrame(id=request_id, ok=False, error=str(exc))
            ambiguous_response = self._format_host_disambiguation_response(browse_term, result)
            if ambiguous_response is not None:
                response_text = ambiguous_response
            else:
                folder_match = self._pick_folder_match_for_contents(browse_term, result)
                if folder_match is not None:
                    try:
                        listing = await self.tool_registry.dispatch(
                            "list_host_dir",
                            {
                                "path": folder_match["path"],
                                "session_key": session_key,
                                "user_id": metadata.get("user_id", self.config.users.default_user_id),
                            },
                        )
                    except Exception as exc:
                        return ResponseFrame(id=request_id, ok=False, error=str(exc))
                    response_text = self._format_host_folder_contents_response(folder_match, listing)
                else:
                    response_text = self._format_host_search_response(browse_term, result)
            await self._persist_inline_exchange(session_key, original_message, response_text, metadata)
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={"queued": False, "session_key": session_key, "command_response": response_text},
            )

        search_term = self._extract_host_search_term(lowered)
        if search_term:
            wants_folder = any(token in lowered for token in ("folder", "directory"))
            try:
                result = await self.tool_registry.dispatch(
                    "search_host_files",
                    {
                        "root": explicit_path or explicit_root or "@allowed",
                        "pattern": "*",
                        "name_query": search_term,
                        "directories_only": wants_folder,
                        "limit": 20,
                        "session_key": session_key,
                        "user_id": metadata.get("user_id", self.config.users.default_user_id),
                    },
                )
            except Exception as exc:
                return ResponseFrame(id=request_id, ok=False, error=str(exc))
            ambiguous_response = self._format_host_disambiguation_response(search_term, result) if wants_folder else None
            if ambiguous_response is not None:
                response_text = ambiguous_response
            elif wants_folder and self._wants_folder_contents(lowered):
                folder_match = self._pick_folder_match_for_contents(search_term, result)
                if folder_match is not None:
                    try:
                        listing = await self.tool_registry.dispatch(
                            "list_host_dir",
                            {
                                "path": folder_match["path"],
                                "session_key": session_key,
                                "user_id": metadata.get("user_id", self.config.users.default_user_id),
                            },
                        )
                    except Exception as exc:
                        return ResponseFrame(id=request_id, ok=False, error=str(exc))
                    response_text = self._format_host_folder_contents_response(folder_match, listing)
                else:
                    response_text = self._format_host_search_response(search_term, result)
            else:
                response_text = self._format_host_search_response(search_term, result)
            await self._persist_inline_exchange(session_key, original_message, response_text, metadata)
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={"queued": False, "session_key": session_key, "command_response": response_text},
            )

        app_name = self._match_app_open_request(lowered)
        if app_name:
            command = self._build_app_launch_command(app_name)
            try:
                result = await self.tool_registry.dispatch(
                    "exec_shell",
                    {
                        "command": command,
                        "host": True,
                        "session_key": session_key,
                        "user_id": metadata.get("user_id", self.config.users.default_user_id),
                        "channel_name": metadata.get("channel", ""),
                    },
                )
            except Exception as exc:
                return ResponseFrame(id=request_id, ok=False, error=str(exc))
            response_text = self._format_host_exec_response(app_name, result)
            await self._persist_inline_exchange(session_key, original_message, response_text, metadata)
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={"queued": False, "session_key": session_key, "command_response": response_text},
            )

        desktop_note = self._parse_desktop_note_creation_request(original_message)
        if desktop_note is not None:
            filename, content = desktop_note
            try:
                result = await self.tool_registry.dispatch(
                    "write_host_file",
                    {
                        "path": f"~/Desktop/{filename}",
                        "content": content,
                        "session_key": session_key,
                        "user_id": metadata.get("user_id", self.config.users.default_user_id),
                        "channel_name": metadata.get("channel", ""),
                    },
                )
            except Exception as exc:
                return ResponseFrame(id=request_id, ok=False, error=str(exc))
            response_text = self._format_host_write_response(filename, "Desktop", result)
            await self._persist_inline_exchange(session_key, original_message, response_text, metadata)
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={"queued": False, "session_key": session_key, "command_response": response_text},
            )

        if self._looks_like_desktop_note_request(lowered):
            response_text = (
                "I can create that directly on your Desktop now. "
                "Tell me the filename and content, for example: "
                "\"create a note called todo.txt on my Desktop with content Buy milk\"."
            )
            await self._persist_inline_exchange(session_key, original_message, response_text, metadata)
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={"queued": False, "session_key": session_key, "command_response": response_text},
            )

        return None

    def _augment_system_suffix_for_intent(self, message: str, existing: str | None) -> str | None:
        lowered = message.lower()
        hints: list[str] = []
        if (("last" in lowered or "latest" in lowered) and ("mail" in lowered or "email" in lowered)) or (
            "recent email" in lowered
        ):
            hints.append(
                "If Google OAuth is connected and the user wants the newest email, prefer gmail_latest_email. "
                "Do not ask for a search query unless the user explicitly asks for filtering."
            )
        if ("repo" in lowered or "repository" in lowered) and ("how many" in lowered or "count" in lowered):
            hints.append(
                "If GitHub OAuth is connected and the user asks for a repository count, use github_list_repos and "
                "count the returned repositories instead of asking a follow-up question."
            )
        if any(
            token in lowered
            for token in ("desktop", "downloads", "documents", "notepad", "folder", "drive", "r:", "c:")
        ) or self._extract_explicit_host_path(message) is not None:
            hints.append(
                "You have host-system access tools inside the configured allowed host roots: list_host_dir, search_host_files, "
                "read_host_file, write_host_file, and exec_shell with host=true. Do not claim you are limited to the workspace "
                "when the request is about Desktop, Downloads, Documents, explicit host paths such as C:/..., "
                "allowed drives such as R:/, or opening simple Windows apps."
            )
        if any(
            token in lowered
            for token in ("browser", "tab", "login", "log in", "open site", "website", "leetcode", "gmail.com", "github.com")
        ):
            hints.append(
                "Browser automation is available through browser_navigate, browser_click, browser_type, browser_login, "
                "browser_tabs_list, browser_tab_open, browser_tab_switch, browser_tab_close, browser_upload, "
                "browser_downloads_list, browser_logs, browser_extract_table, and browser_fill_form. "
                "Use these browser tools instead of saying browser features are only available in WebChat."
            )
        if not hints:
            return existing
        if existing:
            return f"{existing}\n\n## Intent Hint\n" + "\n".join(hints)
        return "## Intent Hint\n" + "\n".join(hints)

    async def _fire_message_received(self, session_key: str, message: str, metadata: dict[str, Any]) -> Any:
        return await self.hook_runner.fire_event(
            "message:received",
            context={
                "session_key": session_key,
                "message": message,
                "metadata": metadata,
                "preview": message[:120],
                "sender_id": metadata.get("sender_id"),
                "channel": metadata.get("channel"),
                "user_id": metadata.get("user_id"),
                "logs_dir": str(self.config.logs_dir),
            },
        )

    def _append_system_suffix(self, existing: str | None, heading: str, content: str) -> str:
        parts = []
        if existing:
            parts.append(existing)
        parts.append(f"{heading}\n{content}")
        return "\n\n".join(part for part in parts if part)

    def _split_command_arguments(self, arguments: str) -> tuple[str, str]:
        stripped = arguments.strip()
        if not stripped:
            return "", ""
        parts = stripped.split(maxsplit=1)
        command = parts[0]
        rest = parts[1] if len(parts) > 1 else ""
        return command, rest

    def _cron_help_text(self) -> str:
        return (
            "Cron commands:\n"
            "/cron add \"0 8 * * *\" \"Good morning briefing\"\n"
            "/cron add 0 21 * * * | Nightly review reminder\n"
            "/cron list\n"
            "/cron pause <cron_id>\n"
            "/cron resume <cron_id>\n"
            "/cron delete <cron_id>"
        )

    def _apps_help_text(self) -> str:
        return (
            "App control commands:\n"
            "/apps list\n"
            "/apps open <alias>\n"
            "/apps focus <window_or_alias>\n"
            "/apps minimize <window_or_alias>\n"
            "/apps maximize <window_or_alias>\n"
            "/apps restore <window_or_alias>\n"
            "/apps left <window_or_alias>\n"
            "/apps right <window_or_alias>"
        )

    def _screen_help_text(self) -> str:
        return (
            "Desktop vision commands:\n"
            "/screen active\n"
            "/screen capture\n"
            "/screen window\n"
            "/screen read\n"
            "/screen read window"
        )

    def _input_help_text(self) -> str:
        return (
            "Desktop input commands:\n"
            "/input position\n"
            "/input move <x> <y>\n"
            "/input click <x> <y>\n"
            "/input right-click <x> <y>\n"
            "/input double-click <x> <y>\n"
            "/input scroll up|down <amount>\n"
            "/input type <text>\n"
            "/input hotkey <keys>"
        )

    def _clipboard_help_text(self) -> str:
        return (
            "Clipboard commands:\n"
            "/clipboard get\n"
            "/clipboard set <text>"
        )

    def _desktop_help_text(self) -> str:
        return (
            "Desktop automation commands:\n"
            "/desktop list\n"
            "/desktop pause <rule_name>\n"
            "/desktop resume <rule_name>\n"
            "/desktop delete <rule_name>"
        )

    def _routine_help_text(self) -> str:
        return (
            "Desktop routine commands:\n"
            "/routine list\n"
            "/routine show <routine_name>\n"
            "/routine run <routine_name>\n"
            "/routine pause <routine_name>\n"
            "/routine resume <routine_name>\n"
            "/routine delete <routine_name>"
        )

    def _browser_help_text(self) -> str:
        return (
            "Browser commands:\n"
            "/browser profiles\n"
            "/browser state\n"
            "/browser tabs\n"
            "/browser open <url>\n"
            "/browser switch <tab_id>\n"
            "/browser close <tab_id>\n"
            "/browser logs [limit]\n"
            "/browser downloads [limit]\n"
            "/browser screenshot\n"
            "/browser login <site_name> [profile_name]"
        )

    def _vscode_help_text(self) -> str:
        return (
            "VS Code commands:\n"
            "/vscode open <project_or_folder>\n"
            "/vscode file <file_path_or_name>\n"
            "/vscode search <query>"
        )

    def _document_help_text(self) -> str:
        return (
            "Document commands:\n"
            "/doc read <path_or_name>\n"
            "/doc create <path> :: <content>\n"
            "/doc replace <path> :: <find> :: <replace>"
        )

    def _excel_help_text(self) -> str:
        return (
            "Excel commands:\n"
            "/excel create <path> :: <header1,header2,...>\n"
            "/excel append-row <path> :: <value1,value2,...>\n"
            "/excel preview <path> [:: limit]"
        )

    def _system_help_text(self) -> str:
        return (
            "System commands:\n"
            "/system status\n"
            "/system settings <sound|display|bluetooth|wifi|network|notifications>\n"
            "/system volume\n"
            "/system volume set <0-100>\n"
            "/system brightness\n"
            "/system brightness set <0-100>\n"
            "/system bluetooth"
        )

    def _task_help_text(self) -> str:
        return "Task commands:\n/task open\n/task summary"

    def _preset_help_text(self) -> str:
        return "Preset commands:\n/preset list\n/preset run <study-mode|work-mode|meeting-mode>"

    def _browser_skill_help_text(self) -> str:
        return "Browser skill commands:\n/browser-skill open <study|work|meeting>"

    def _app_skill_context(self, session_key: str, user_id: str, *, connection_id: str = "", channel_name: str = "") -> dict[str, Any]:
        return {
            "session_key": session_key,
            "session_id": f"app-skills:{session_key}",
            "user_id": user_id,
            "connection_id": connection_id,
            "channel_name": channel_name,
        }

    def _split_delimited_arguments(self, value: str, *, expected_parts: int) -> tuple[Any, ...]:
        parts = [part.strip() for part in re.split(r"\s+::\s+", value.strip())]
        if len(parts) < expected_parts:
            return tuple([None] * expected_parts)
        normalized = parts[:expected_parts]
        while len(normalized) < expected_parts:
            normalized.append(None)
        return tuple(normalized)

    def _parse_csv_values(self, value: str) -> list[str]:
        if not value.strip():
            return []
        return [item.strip() for item in value.split(",") if item.strip()]

    def _parse_app_skill_request(self, message: str, lowered: str) -> dict[str, Any] | None:
        normalized = re.sub(r"\s+", " ", lowered).strip()
        if normalized in {"study mode", "start study mode", "run study mode"}:
            return {"action": "preset-run", "name": "study-mode"}
        if normalized in {"work mode", "start work mode", "run work mode"}:
            return {"action": "preset-run", "name": "work-mode"}
        if normalized in {"meeting mode", "start meeting mode", "run meeting mode"}:
            return {"action": "preset-run", "name": "meeting-mode"}
        if normalized in {"open task manager", "start task manager", "launch task manager"}:
            return {"action": "task-open"}
        if normalized in {"task manager summary", "show task manager summary", "summarize task manager"}:
            return {"action": "task-summary"}
        if normalized in {"what is the volume", "what's the volume", "current volume", "show volume"}:
            return {"action": "volume"}
        volume_match = re.match(r"^(?:set|change)\s+volume\s+to\s+(\d{1,3})$", normalized)
        if volume_match is not None:
            return {"action": "volume-set", "percent": int(volume_match.group(1))}
        if normalized in {"what is the brightness", "what's the brightness", "current brightness", "show brightness"}:
            return {"action": "brightness"}
        brightness_match = re.match(r"^(?:set|change)\s+brightness\s+to\s+(\d{1,3})$", normalized)
        if brightness_match is not None:
            return {"action": "brightness-set", "percent": int(brightness_match.group(1))}
        if normalized in {"bluetooth status", "show bluetooth status", "what is the bluetooth status"}:
            return {"action": "bluetooth"}
        settings_match = re.match(r"^(?:open|show)\s+(sound|display|brightness|bluetooth|wifi|network|notifications)\s+settings$", normalized)
        if settings_match is not None:
            return {"action": "settings", "page": settings_match.group(1)}
        workspace_match = re.match(r"^(?:open|start)\s+(study|work|meeting)\s+browser\s+workspace$", normalized)
        if workspace_match is not None:
            return {"action": "browser-workspace", "workspace": workspace_match.group(1)}
        vscode_match = re.match(r"^(?:open|launch|start)\s+(.+?)\s+in\s+vscode$", message.strip(), flags=re.IGNORECASE)
        if vscode_match is not None:
            target = str(vscode_match.group(1)).strip().strip("\"'")
            prefer = "file" if re.search(r"\.[A-Za-z0-9]{1,6}$", target) else "either"
            if any(keyword in normalized for keyword in {" project in vscode", " folder in vscode", " workspace in vscode"}):
                prefer = "directory"
            return {"action": "vscode-open", "target": target, "prefer": prefer}
        return None

    def _known_app_aliases(self) -> set[str]:
        configured = getattr(self.config.desktop_apps, "known_apps", {})
        return {str(alias).strip().lower() for alias in configured.keys() if str(alias).strip()}

    def _parse_desktop_vision_request(self, lowered: str) -> dict[str, str] | None:
        normalized = re.sub(r"\s+", " ", lowered).strip()
        if normalized in {"what app is active", "which app is active", "what window is active"}:
            return {"action": "active"}
        if normalized in {
            "take a screenshot of my desktop",
            "take a screenshot of the desktop",
            "take a screenshot of my screen",
            "capture my desktop",
            "capture the desktop",
        }:
            return {"action": "capture"}
        if normalized in {
            "capture the active window",
            "take a screenshot of the active window",
            "screenshot the active window",
        }:
            return {"action": "window"}
        if normalized in {
            "read the text on my screen",
            "read my screen",
            "read the screen",
        }:
            return {"action": "read", "target": "desktop"}
        if normalized in {
            "read the active window",
            "read text from the active window",
            "read the text in the active window",
        }:
            return {"action": "read", "target": "window"}
        return None

    def _parse_desktop_input_request(self, lowered: str) -> dict[str, Any] | None:
        normalized = re.sub(r"\s+", " ", lowered).strip()
        if normalized == "copy selected text":
            return {"action": "copy-selected"}
        if normalized in {"what is on my clipboard", "what's on my clipboard", "read my clipboard", "get clipboard"}:
            return {"action": "clipboard-read"}
        clipboard_match = re.match(r"^(?:set|write)\s+clipboard\s+to\s+(.+)$", normalized)
        if clipboard_match is not None:
            return {"action": "clipboard-write", "text": clipboard_match.group(1).strip()}
        move_match = re.match(r"^(?:move|move mouse|move the mouse)\s+to\s+(-?\d+)[,\s]+(-?\d+)$", normalized)
        if move_match is not None:
            return {"action": "move", "x": int(move_match.group(1)), "y": int(move_match.group(2))}
        click_match = re.match(r"^click\s+(?:at\s+)?(-?\d+)[,\s]+(-?\d+)$", normalized)
        if click_match is not None:
            return {"action": "click", "x": int(click_match.group(1)), "y": int(click_match.group(2))}
        double_click_match = re.match(r"^(?:double click|double-click)\s+(?:at\s+)?(-?\d+)[,\s]+(-?\d+)$", normalized)
        if double_click_match is not None:
            return {"action": "double-click", "x": int(double_click_match.group(1)), "y": int(double_click_match.group(2))}
        right_click_match = re.match(r"^(?:right click|right-click)\s+(?:at\s+)?(-?\d+)[,\s]+(-?\d+)$", normalized)
        if right_click_match is not None:
            return {"action": "right-click", "x": int(right_click_match.group(1)), "y": int(right_click_match.group(2))}
        scroll_match = re.match(r"^scroll\s+(up|down)(?:\s+(\d+))?$", normalized)
        if scroll_match is not None:
            return {"action": "scroll", "direction": scroll_match.group(1), "amount": int(scroll_match.group(2) or "1")}
        type_match = re.match(r"^type\s+(.+)$", normalized)
        if type_match is not None:
            return {"action": "type", "text": type_match.group(1)}
        press_match = re.match(r"^(?:press|hit)\s+(.+)$", normalized)
        if press_match is not None:
            return {"action": "hotkey", "hotkey": press_match.group(1)}
        return None

    async def _parse_desktop_coworker_request(self, original_message: str, lowered: str) -> dict[str, Any] | None:
        normalized = re.sub(r"\s+", " ", lowered).strip()
        if normalized.startswith("help me "):
            return {"request": original_message.strip()}
        multi_step_markers = [
            " and verify",
            " and confirm",
            " and summarize",
            " and tell me whether",
        ]
        if any(marker in normalized for marker in multi_step_markers) and await self.coworker_service.can_handle_request(original_message):
            return {"request": original_message.strip()}
        bluetooth_toggle_patterns = (
            r"\bturn\s+off\s+(?:the\s+)?bluetooth\b",
            r"\bturn\s+(?:the\s+)?bluetooth\s+off\b",
            r"\bswitch\s+off\s+(?:the\s+)?bluetooth\b",
            r"\bswitch\s+(?:the\s+)?bluetooth\s+off\b",
            r"\bdisable\s+(?:the\s+)?bluetooth\b",
            r"\bturn\s+on\s+(?:the\s+)?bluetooth\b",
            r"\bturn\s+(?:the\s+)?bluetooth\s+on\b",
            r"\bswitch\s+on\s+(?:the\s+)?bluetooth\b",
            r"\bswitch\s+(?:the\s+)?bluetooth\s+on\b",
            r"\benable\s+(?:the\s+)?bluetooth\b",
        )
        if any(re.search(pattern, normalized) for pattern in bluetooth_toggle_patterns):
            if await self.coworker_service.can_handle_request(original_message):
                return {"request": original_message.strip()}
        if re.match(r"^(?:click(?:\s+on)?|select|double click|double-click)\s+(?!at\b)(?:the\s+)?[a-z0-9][\w\s._()&-]*$", normalized):
            if await self.coworker_service.can_handle_request(original_message):
                return {"request": original_message.strip()}
        visual_markers = [
            "see on screen",
            "see on the screen",
            "visible on screen",
            "visible on the screen",
            "on screen now",
            "on the screen now",
            "you are seeing on the screen",
            "shown on screen",
            "highlighted",
            "visible file",
            "visible item",
            "visible button",
            "visible tab",
        ]
        if (
            any(marker in normalized for marker in visual_markers)
            or re.search(r"\b(?:open|click|select|double click|double-click)\b.+\bvisible\b", normalized)
        ) and await self.coworker_service.can_handle_request(original_message):
            return {"request": original_message.strip()}
        return None

    def _parse_input_coordinates(self, value: str) -> tuple[int, int] | None:
        match = re.match(r"^\s*(-?\d+)[,\s]+(-?\d+)\s*$", value)
        if match is None:
            return None
        return int(match.group(1)), int(match.group(2))

    def _parse_input_scroll_arguments(self, value: str) -> tuple[str, int] | None:
        match = re.match(r"^\s*(up|down)(?:\s+(\d+))?\s*$", value.strip().lower())
        if match is None:
            return None
        return str(match.group(1)), int(match.group(2) or "1")

    def _parse_app_control_request(self, lowered: str) -> dict[str, str] | None:
        normalized = re.sub(r"\s+", " ", lowered).strip()
        aliases = self._known_app_aliases()
        if not aliases:
            return None

        def _clean_target(value: str) -> str:
            target = value.strip().strip("\"'")
            target = re.sub(r"^(?:the|my)\s+", "", target)
            target = re.sub(r"\s+app$", "", target)
            target = re.sub(r"\s+window$", "", target)
            return target.strip()

        open_match = re.match(r"^(?:can you\s+|please\s+)?(?:open|launch|start)\s+(?P<target>.+)$", normalized)
        if open_match is not None:
            target = _clean_target(str(open_match.group("target")))
            if target.lower() in aliases:
                return {"action": "open", "target": target.lower()}

        focus_match = re.match(r"^(?:switch to|focus(?: on)?)\s+(?P<target>.+)$", normalized)
        if focus_match is not None:
            target = _clean_target(str(focus_match.group("target")))
            if target.lower() in aliases:
                return {"action": "focus", "target": target.lower()}

        minimize_match = re.match(r"^minimize\s+(?P<target>.+)$", normalized)
        if minimize_match is not None:
            target = _clean_target(str(minimize_match.group("target")))
            if target.lower() in aliases:
                return {"action": "minimize", "target": target.lower()}

        maximize_match = re.match(r"^maximize\s+(?P<target>.+)$", normalized)
        if maximize_match is not None:
            target = _clean_target(str(maximize_match.group("target")))
            if target.lower() in aliases:
                return {"action": "maximize", "target": target.lower()}

        restore_match = re.match(r"^restore\s+(?P<target>.+)$", normalized)
        if restore_match is not None:
            target = _clean_target(str(restore_match.group("target")))
            if target.lower() in aliases:
                return {"action": "restore", "target": target.lower()}

        snap_match = re.match(
            r"^(?:put|move|snap)\s+(?P<target>.+?)\s+(?:on|to)\s+(?:the\s+)?(?P<position>left|right)$",
            normalized,
        )
        if snap_match is not None:
            target = _clean_target(str(snap_match.group("target")))
            if target.lower() in aliases:
                return {"action": "snap", "target": target.lower(), "position": str(snap_match.group("position")).lower()}
        return None

    def _format_app_control_response(self, action: str, result: dict[str, Any]) -> str:
        if action in {"list", "ls"}:
            windows = result.get("windows", [])
            if not windows:
                return "No visible app windows are open right now."
            lines = ["Visible app windows:"]
            for item in windows[:12]:
                flags = []
                if item.get("is_foreground"):
                    flags.append("active")
                if item.get("is_minimized"):
                    flags.append("minimized")
                flag_text = f" [{' / '.join(flags)}]" if flags else ""
                lines.append(
                    f"- {item.get('window_id', '?')}: {item.get('title', '(untitled)')} | {item.get('process_name', 'unknown')}{flag_text}"
                )
            return "\n".join(lines)
        if action == "open":
            return f"Launched {result.get('alias', 'app')} from {result.get('path', 'unknown')}."
        window = result.get("window", {})
        title = str(window.get("title") or result.get("target", "window"))
        if action == "focus":
            return f"Focused '{title}'."
        if action == "minimize":
            return f"Minimized '{title}'."
        if action == "maximize":
            return f"Maximized '{title}'."
        if action == "restore":
            return f"Restored '{title}'."
        if action == "snap":
            return f"Snapped '{title}' to the {result.get('position', 'left')} side."
        return "Completed the requested app action."

    def _format_desktop_vision_response(self, action: str, result: dict[str, Any]) -> str:
        if action == "active":
            window = result.get("active_window", {})
            title = str(window.get("title") or "(untitled)")
            process_name = str(window.get("process_name") or "unknown")
            return (
                f"Active window: {title}\n"
                f"Process: {process_name}\n"
                f"Window id: {window.get('window_id', 'unknown')}"
            )
        if action in {"capture", "window"}:
            article = "an" if action == "window" else "a"
            scope = "active window" if action == "window" else "desktop"
            window = result.get("active_window", {})
            return (
                f"Captured {article} {scope} screenshot.\n"
                f"Saved at: {result.get('path', 'unknown')}\n"
                f"Active window: {window.get('title', '(untitled)')} ({window.get('process_name', 'unknown')})"
            )
        if action == "read":
            target = str(result.get("target", "desktop"))
            content = str(result.get("content", "")).strip()
            if not content:
                return (
                    f"Captured the {target} but could not find readable text.\n"
                    f"Saved at: {result.get('path', 'unknown')}"
                )
            return (
                f"Read the {target}.\n"
                f"Saved at: {result.get('path', 'unknown')}\n\n"
                f"{content}"
            )
        return "Completed the desktop vision action."

    def _format_desktop_input_response(self, action: str, result: dict[str, Any]) -> str:
        status = str(result.get("status", "completed"))
        if status.startswith("blocked") or status in {"rejected", "expired"}:
            return (
                f"I didn't complete the {action} input action because it was {status}. "
                "Check /host-approvals if you want to review pending requests."
            )
        if status == "failed":
            return f"The {action} input action failed: {result.get('stderr', 'unknown error')}"
        if action == "position":
            return (
                f"Cursor position: ({result.get('x', '?')}, {result.get('y', '?')})\n"
                f"Active window: {result.get('active_window', {}).get('title', '(untitled)')}"
            )
        if action == "move":
            return f"Moved the mouse to ({result.get('x', '?')}, {result.get('y', '?')})."
        if action == "click":
            return f"Clicked at ({result.get('x', '?')}, {result.get('y', '?')})."
        if action == "right-click":
            return f"Right-clicked at ({result.get('x', '?')}, {result.get('y', '?')})."
        if action == "double-click":
            return f"Double-clicked at ({result.get('x', '?')}, {result.get('y', '?')})."
        if action == "scroll":
            return f"Scrolled {result.get('direction', 'down')} {result.get('amount', 1)} step(s)."
        if action == "type":
            return f"Typed {result.get('characters_typed', 0)} character(s) into the active window."
        if action == "hotkey":
            return f"Pressed {result.get('hotkey', 'the requested hotkey')}."
        if action == "clipboard-read":
            content = str(result.get("content", ""))
            if not content:
                return "Clipboard is empty."
            preview = content if len(content) <= 2000 else f"{content[:2000].rstrip()}\n..."
            return f"Clipboard text:\n\n{preview}"
        if action == "clipboard-write":
            return f"Updated the clipboard text ({result.get('char_count', 0)} character(s))."
        return "Completed the desktop input action."

    def _coworker_help_text(self) -> str:
        return (
            "Coworker commands:\n"
            "- /coworker plan <task>\n"
            "- /coworker run <task or task_id>\n"
            "- /coworker step <task_id>\n"
            "- /coworker status <task_id>\n"
            "- /coworker stop <task_id>\n"
            "- /coworker history\n\n"
            "Examples:\n"
            "- /coworker run open task manager and summarize system usage\n"
            "- /coworker run open bluetooth settings and tell me whether bluetooth is available\n"
            "- /coworker run open bluetooth settings and turn off the bluetooth\n"
            "- /coworker run open R:/6_semester/mini_project in vscode and confirm the window is focused\n"
            "- /coworker run open the file you see on screen now"
        )

    def _format_coworker_task(self, task: dict[str, Any], *, planned: bool = False) -> str:
        total_steps = int(task.get("total_steps", len(task.get("steps", []))))
        current_step = int(task.get("current_step_index", 0))
        lines = [
            f"Coworker task {task.get('task_id', 'unknown')}",
            f"Status: {task.get('status', 'unknown')}",
            f"Goal: {task.get('summary', task.get('request_text', 'desktop task'))}",
            f"Progress: {min(current_step, total_steps)} / {total_steps} step(s)",
        ]
        if planned and task.get("steps"):
            lines.append("Planned steps:")
            for index, step in enumerate(task.get("steps", []), start=1):
                verification = self._format_coworker_verification(dict(step))
                lines.append(f"{index}. {step.get('title', step.get('type', 'step'))}{verification}")
        transcript = list(task.get("transcript", []))
        if transcript:
            lines.append("Transcript:")
            for entry in transcript[-5:]:
                step_number = int(entry.get("step_index", 0)) + 1
                lines.append(
                    f"- Step {step_number} ({entry.get('step_type', 'step')}): {entry.get('summary', entry.get('status', 'completed'))}"
                )
        active_window = task.get("latest_state", {}).get("active_window", {}) if isinstance(task.get("latest_state"), dict) else {}
        if isinstance(active_window, dict) and (active_window.get("title") or active_window.get("process_name")):
            lines.append(
                f"Latest window: {active_window.get('title', '(untitled)')} ({active_window.get('process_name', 'unknown')})"
            )
        capture_path = task.get("latest_state", {}).get("capture_path", "") if isinstance(task.get("latest_state"), dict) else ""
        if capture_path:
            lines.append(f"Latest capture: {capture_path}")
        if task.get("last_backend"):
            lines.append(f"Targeting backend: {task.get('last_backend')}")
        if task.get("current_attempt"):
            lines.append(f"Current attempt: {task.get('current_attempt')}")
        if task.get("stop_reason"):
            lines.append(f"Stop reason: {task.get('stop_reason')}")
        if task.get("error"):
            lines.append(f"Error: {task['error']}")
        return "\n".join(lines)

    def _format_coworker_history(self, tasks: list[dict[str, Any]]) -> str:
        if not tasks:
            return "No coworker tasks have been created yet."
        lines = ["Recent coworker tasks:"]
        for task in tasks[:12]:
            lines.append(
                f"- {task.get('task_id', 'unknown')}: {task.get('summary', task.get('request_text', 'desktop task'))} "
                f"[{task.get('status', 'unknown')}, {task.get('current_step_index', 0)}/{task.get('total_steps', 0)}]"
            )
        return "\n".join(lines)

    def _format_coworker_verification(self, step: dict[str, Any]) -> str:
        verification = dict(step.get("verification", {}))
        kind = str(verification.get("kind", "tool_status"))
        if kind == "active_window_contains":
            return f" [verify window matches {', '.join(str(item) for item in verification.get('matches', []))}]"
        if kind == "document_contains":
            return " [verify updated document content]"
        if kind == "clipboard_nonempty":
            return " [verify clipboard has text]"
        if kind == "summary_has_keys":
            return " [verify summary data is present]"
        return ""

    def _format_vscode_response(self, action: str, result: dict[str, Any]) -> str:
        if action == "search":
            matches = list(result.get("matches", []))
            if not matches:
                return "I couldn't find a matching file or folder for that VS Code search."
            lines = ["VS Code search matches:"]
            for item in matches[:10]:
                suffix = "/" if item.get("is_dir") else ""
                lines.append(f"- {item.get('name', '(unknown)')}{suffix} -> {item.get('path', '')}")
            return "\n".join(lines)
        return f"Opened {result.get('path', 'the requested target')} in VS Code."

    def _format_document_response(self, action: str, result: dict[str, Any]) -> str:
        if action == "read":
            content = str(result.get("content", "")).strip()
            if not content:
                return f"The document at {result.get('path', 'that path')} is empty."
            preview = content if len(content) <= 3000 else f"{content[:3000].rstrip()}\n..."
            return f"Document content from {result.get('path', 'that path')}:\n\n{preview}"
        if action == "create":
            return f"Created or updated {result.get('path', 'the document')}."
        if action == "replace":
            status = str(result.get("status", "completed"))
            if status == "no_change":
                return f"No matching text was found in {result.get('path', 'the document')}."
            return f"Updated {result.get('path', 'the document')} with {result.get('replacements', 0)} replacement(s)."
        return "Completed the document action."

    def _format_excel_response(self, action: str, result: dict[str, Any]) -> str:
        if action in {"create", "append"}:
            preview = result.get("preview", {}) if isinstance(result.get("preview"), dict) else {}
            return (
                f"{'Created' if action == 'create' else 'Updated'} workbook {result.get('path', '')}.\n"
                f"Sheet: {preview.get('sheet_name', result.get('sheet_name', 'Sheet1'))}\n"
                f"Rows: {preview.get('row_count', 0)}"
            )
        if action == "preview":
            rows = result.get("rows", [])
            if not rows:
                return f"The workbook {result.get('path', '')} is empty."
            lines = [f"Workbook preview for {result.get('path', '')} ({result.get('sheet_name', 'Sheet1')}):"]
            for row in rows[:8]:
                lines.append(f"- {', '.join(str(item) for item in row)}")
            if int(result.get("row_count", len(rows))) > len(rows):
                lines.append(f"...and {int(result.get('row_count', len(rows))) - len(rows)} more row(s).")
            return "\n".join(lines)
        return "Completed the Excel action."

    def _format_system_response(self, action: str, result: dict[str, Any]) -> str:
        if action == "settings":
            return f"Opened {result.get('page', 'settings')} settings."
        if action in {"volume", "volume-set"}:
            return f"System volume is {result.get('volume_percent', 0)}%."
        if action in {"brightness", "brightness-set"}:
            if not result.get("supported", True):
                return str(result.get("message", "Direct brightness control is unavailable on this device."))
            return f"Brightness is {result.get('brightness_percent', 0)}%."
        if action == "bluetooth":
            availability = "available" if result.get("available") else "not available"
            return (
                f"Bluetooth is {availability}.\n"
                f"Service: {result.get('service_status', 'Unknown')}\n"
                f"Connected/ready devices: {result.get('device_count', 0)}"
            )
        if action == "status":
            memory = result.get("memory", {}) if isinstance(result.get("memory"), dict) else {}
            disk = result.get("disk", {}) if isinstance(result.get("disk"), dict) else {}
            return (
                f"CPU: {result.get('cpu_percent', 0)}%\n"
                f"Memory: {memory.get('used_percent', 0)}% ({memory.get('used_gb', 0)} / {memory.get('total_gb', 0)} GB)\n"
                f"Disk: {disk.get('used_percent', 0)}% used on {disk.get('drive', 'system drive')}\n"
                f"Volume: {result.get('volume', {}).get('volume_percent', 0) if isinstance(result.get('volume'), dict) else 0}%"
            )
        return "Completed the system action."

    def _format_task_response(self, action: str, result: dict[str, Any]) -> str:
        summary = result.get("summary", result) if isinstance(result, dict) else {}
        memory = summary.get("memory", {}) if isinstance(summary.get("memory"), dict) else {}
        disk = summary.get("disk", {}) if isinstance(summary.get("disk"), dict) else {}
        lines = []
        if action == "open":
            lines.append("Opened Task Manager.")
        lines.extend(
            [
                f"CPU: {summary.get('cpu_percent', 0)}%",
                f"Memory: {memory.get('used_percent', 0)}% ({memory.get('used_gb', 0)} / {memory.get('total_gb', 0)} GB)",
                f"Disk: {disk.get('used_percent', 0)}% used on {disk.get('drive', 'system drive')}",
            ]
        )
        top_processes = list(summary.get("top_processes", []))
        if top_processes:
            lines.append("Top processes:")
            for item in top_processes[:5]:
                lines.append(
                    f"- {item.get('name', 'unknown')}: CPU {item.get('cpu_seconds', 0)}s, RAM {item.get('memory_mb', 0)} MB"
                )
        return "\n".join(lines)

    def _format_preset_response(self, action: str, result: dict[str, Any]) -> str:
        if action == "list":
            presets = list(result.get("presets", []))
            lines = ["Available presets:"]
            for item in presets:
                lines.append(f"- {item.get('name', 'preset')}: {item.get('description', '')}")
            return "\n".join(lines)
        actions = list(result.get("actions", []))
        if not actions:
            return f"Ran {result.get('preset', 'the preset')}."
        return f"Ran {result.get('preset', 'the preset')}:\n" + "\n".join(f"- {item}" for item in actions)

    def _format_browser_skill_response(self, result: dict[str, Any]) -> str:
        opened = list(result.get("opened", []))
        if not opened:
            return f"No browser tabs were opened for the {result.get('workspace', 'requested')} workspace."
        lines = [f"Opened {len(opened)} browser tab(s) for the {result.get('workspace', 'requested')} workspace:"]
        for item in opened[:8]:
            lines.append(f"- {item.get('title') or item.get('url', '')} -> {item.get('url', '')}")
        return "\n".join(lines)

    def _parse_browser_limit(self, value: str, *, default: int = 8) -> int:
        stripped = value.strip()
        if not stripped:
            return default
        try:
            return max(1, min(int(stripped), 50))
        except ValueError:
            return default

    def _parse_browser_login_arguments(self, value: str) -> tuple[str, str | None]:
        parts = [item for item in value.split() if item]
        if not parts:
            return "", None
        if len(parts) == 1:
            return parts[0], None
        return parts[0], parts[1]

    def _parse_cron_add_arguments(self, arguments: str) -> tuple[str | None, str | None]:
        stripped = arguments.strip()
        quoted = re.match(r'^(?:"([^"]+)"|\'([^\']+)\')\s+(?:"([^"]+)"|\'([^\']+)\')$', stripped)
        if quoted:
            schedule = quoted.group(1) or quoted.group(2)
            message = quoted.group(3) or quoted.group(4)
            return self._normalize_cli_text(schedule), self._normalize_cli_text(message)
        if "|" in stripped:
            left, right = stripped.split("|", maxsplit=1)
            return self._normalize_cli_text(left), self._normalize_cli_text(right)
        parts = stripped.split()
        if len(parts) >= 6:
            schedule = " ".join(parts[:5])
            message = " ".join(parts[5:])
            return self._normalize_cli_text(schedule), self._normalize_cli_text(message)
        return None, None

    def _normalize_cli_text(self, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().strip("\"'")
        return normalized or None

    def _parse_natural_language_cron_request(self, message: str, lowered: str) -> tuple[str, str] | None:
        normalized = re.sub(r"\s+", " ", lowered).strip()
        normalized = re.sub(r"^(?:please\s+|can you\s+|could you\s+|would you\s+)", "", normalized)
        normalized = re.sub(r"^(?:set up|setup)\s+", "set ", normalized)
        frequency_pattern = (
            r"daily|day|weekdays|weekday|monday|tuesday|wednesday|thursday|friday|saturday|sunday|weekly"
        )
        patterns = (
            rf"^(?:create|set|make)\s+(?:a\s+)?(?:cron\s+job|reminder)\s+to\s+remind me every\s+(?P<frequency>{frequency_pattern})s?\s+at\s+(?P<time>.+?)\s+to\s+(?P<message>.+)$",
            rf"^remind me every\s+(?P<frequency>{frequency_pattern})s?\s+at\s+(?P<time>.+?)\s+to\s+(?P<message>.+)$",
            rf"^every\s+(?P<frequency>{frequency_pattern})s?\s+at\s+(?P<time>.+?)\s+remind me to\s+(?P<message>.+)$",
            rf"^(?:set|create|make)\s+(?:a\s+)?reminder\s+for\s+(?P<frequency>{frequency_pattern})s?\s+at\s+(?P<time>.+?)\s+to\s+(?P<message>.+)$",
            rf"^remind me at\s+(?P<time>.+?)\s+(?P<frequency>{frequency_pattern})s?\s+to\s+(?P<message>.+)$",
            rf"^(?:set|create|make)\s+(?:a\s+)?(?:daily|weekday|weekly)\s+reminder\s+at\s+(?P<time>.+?)\s+to\s+(?P<message>.+)$",
            rf"^every\s+(?P<time_of_day>morning|afternoon|evening|night)\s+remind me to\s+(?P<message>.+)$",
        )
        for pattern in patterns:
            match = re.match(pattern, normalized, flags=re.IGNORECASE)
            if not match:
                continue
            if match.groupdict().get("time_of_day") is not None:
                schedule = self._build_schedule_from_frequency_and_time("day", str(match.group("time_of_day")))
            else:
                frequency_value = str(match.group("frequency"))
                if frequency_value == "weekly":
                    frequency_value = "weekday"
                schedule = self._build_schedule_from_frequency_and_time(
                    frequency_value,
                    str(match.group("time")),
                )
            reminder_message = self._normalize_reminder_message(match.group("message"))
            if schedule and reminder_message:
                return schedule, reminder_message
        return None

    def _parse_natural_language_one_time_reminder_request(self, lowered: str) -> tuple[datetime, str] | None:
        normalized = re.sub(r"\s+", " ", lowered).strip()
        normalized = re.sub(r"^(?:please\s+|can you\s+|could you\s+|would you\s+)", "", normalized)
        patterns = (
            r"^remind me (?P<day>today|tomorrow) at (?P<time>.+?) to (?P<message>.+)$",
            r"^remind me at (?P<time>.+?) (?P<day>today|tomorrow) to (?P<message>.+)$",
            r"^(?:set|create|make)\s+(?:a\s+)?reminder for (?P<day>today|tomorrow) at (?P<time>.+?) to (?P<message>.+)$",
        )
        for pattern in patterns:
            match = re.match(pattern, normalized, flags=re.IGNORECASE)
            if not match:
                continue
            run_at = self._build_one_time_reminder_datetime(str(match.group("day")), str(match.group("time")))
            reminder_message = self._normalize_reminder_message(match.group("message"))
            if run_at is not None and reminder_message:
                return run_at, reminder_message
        return None

    def _build_one_time_reminder_datetime(self, day_text: str, time_text: str) -> datetime | None:
        parsed_time = self._parse_reminder_time(time_text)
        if parsed_time is None:
            return None
        hour, minute = parsed_time
        now = datetime.now().astimezone()
        day_offset = 1 if day_text.lower() == "tomorrow" else 0
        target_date = now.date() + timedelta(days=day_offset)
        target = datetime(
            target_date.year,
            target_date.month,
            target_date.day,
            hour,
            minute,
            tzinfo=now.tzinfo,
        )
        if day_offset == 0 and target <= now:
            return None
        return target

    def _build_schedule_from_frequency_and_time(self, frequency: str, time_text: str) -> str | None:
        parsed_time = self._parse_reminder_time(time_text)
        if parsed_time is None:
            return None
        hour, minute = parsed_time
        day_map = {
            "day": "*",
            "daily": "*",
            "weekday": "1-5",
            "weekdays": "1-5",
            "monday": "1",
            "tuesday": "2",
            "wednesday": "3",
            "thursday": "4",
            "friday": "5",
            "saturday": "6",
            "sunday": "0",
        }
        normalized_frequency = frequency.lower().rstrip("s")
        day_of_week = day_map.get(normalized_frequency)
        if day_of_week is None:
            return None
        return f"{minute} {hour} * * {day_of_week}"

    def _parse_reminder_time(self, value: str) -> tuple[int, int] | None:
        normalized = re.sub(r"\s+", " ", value.strip().lower())
        named_times = {
            "morning": (9, 0),
            "afternoon": (14, 0),
            "evening": (18, 0),
            "night": (21, 0),
            "noon": (12, 0),
            "midnight": (0, 0),
        }
        if normalized in named_times:
            return named_times[normalized]
        twelve_hour = re.match(r"^(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>am|pm)$", normalized)
        if twelve_hour is not None:
            hour = int(twelve_hour.group("hour"))
            minute = int(twelve_hour.group("minute") or "0")
            ampm = str(twelve_hour.group("ampm"))
            if hour < 1 or hour > 12 or minute > 59:
                return None
            if ampm == "am":
                hour = 0 if hour == 12 else hour
            else:
                hour = 12 if hour == 12 else hour + 12
            return hour, minute
        twenty_four_hour = re.match(r"^(?P<hour>\d{1,2}):(?P<minute>\d{2})$", normalized)
        if twenty_four_hour is not None:
            hour = int(twenty_four_hour.group("hour"))
            minute = int(twenty_four_hour.group("minute"))
            if hour > 23 or minute > 59:
                return None
            return hour, minute
        return None

    def _normalize_reminder_message(self, value: str) -> str | None:
        normalized = value.strip().strip("\"'")
        normalized = re.sub(r"[.?!]+$", "", normalized).strip()
        if not normalized:
            return None
        if not normalized.lower().startswith("reminder:"):
            normalized = f"Reminder: {normalized}"
        return normalized

    async def _parse_desktop_automation_request(
        self,
        session_key: str,
        message: str,
        lowered: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any] | None:
        normalized = re.sub(r"\s+", " ", lowered).strip()
        watch_notify_match = re.match(
            r"^watch\s+(?P<source>.+?)\s+and\s+notify\s+me\s+for\s+new\s+(?:(?P<ext>[a-z0-9]+)\s+)?files?$",
            normalized,
            flags=re.IGNORECASE,
        )
        if watch_notify_match:
            source_path = await self._resolve_desktop_automation_path(
                session_key,
                str(watch_notify_match.group("source")),
                metadata,
            )
            if source_path is None:
                return {"response_text": f"I couldn't resolve the watched folder '{watch_notify_match.group('source')}' inside your allowed host locations."}
            extension = (watch_notify_match.group("ext") or "").strip().lower()
            return {
                "name": f"Watch {Path(source_path).name}",
                "trigger_type": "file_watch",
                "watch_path": source_path,
                "event_types": ["file_created"],
                "file_extensions": [extension] if extension else [],
                "filename_pattern": "*",
                "action_type": "notify",
                "destination_path": "",
                "cooldown_seconds": 10,
                "dedupe_window_seconds": 10,
                "delivery_policy": "primary",
                "severity": "info",
            }
        watch_match = re.match(
            r"^when\s+(?:a|an)\s+(?:(?P<ext>[a-z0-9]+)\s+)?file\s+appears\s+in\s+(?P<source>.+?)\s*,?\s*(?P<action>move|copy|rename|delete|notify)(?:\s+it)?(?:\s+to\s+(?P<dest>.+))?$",
            normalized,
            flags=re.IGNORECASE,
        )
        if watch_match:
            source_path = await self._resolve_desktop_automation_path(
                session_key,
                str(watch_match.group("source")),
                metadata,
            )
            if source_path is None:
                return {"response_text": f"I couldn't resolve the watched folder '{watch_match.group('source')}' inside your allowed host locations."}
            action = str(watch_match.group("action")).lower()
            destination_path = ""
            if action in {"move", "copy"}:
                dest_value = str(watch_match.group("dest") or "").strip()
                if not dest_value:
                    return {"response_text": f"I need a destination folder for the '{action}' action."}
                resolved_destination = await self._resolve_desktop_automation_path(session_key, dest_value, metadata)
                if resolved_destination is None:
                    return {"response_text": f"I couldn't resolve the destination folder '{dest_value}' inside your allowed host locations."}
                destination_path = resolved_destination
            extension = (watch_match.group("ext") or "").strip().lower()
            return {
                "name": f"Desktop automation for {Path(source_path).name}",
                "trigger_type": "file_watch",
                "watch_path": source_path,
                "event_types": ["file_created"],
                "file_extensions": [extension] if extension and extension != "file" else [],
                "filename_pattern": "*",
                "action_type": "notify" if action == "notify" else action,
                "destination_path": destination_path,
                "cooldown_seconds": 10,
                "dedupe_window_seconds": 10,
                "delivery_policy": "primary",
                "severity": "info",
            }
        organize_named_time_match = re.match(
            r"^every\s+(?P<time>morning|afternoon|evening|night|noon|midnight)\s+organize\s+my\s+(?P<source>desktop|downloads|documents|download2|[a-z0-9._ /-]+)$",
            normalized,
            flags=re.IGNORECASE,
        )
        if organize_named_time_match:
            source_path = await self._resolve_desktop_automation_path(
                session_key,
                str(organize_named_time_match.group("source")),
                metadata,
            )
            if source_path is None:
                return {"response_text": f"I couldn't resolve the folder '{organize_named_time_match.group('source')}' inside your allowed host locations."}
            schedule = self._build_schedule_from_frequency_and_time("daily", str(organize_named_time_match.group("time")))
            if schedule is None:
                return {"response_text": "I couldn't understand that schedule. Try something like 'every night organize my Desktop'."}
            return {
                "name": f"Organize {Path(source_path).name}",
                "trigger_type": "schedule",
                "watch_path": source_path,
                "schedule": schedule,
                "event_types": ["scheduled"],
                "file_extensions": [],
                "filename_pattern": "*",
                "action_type": "organize",
                "destination_path": "",
                "cooldown_seconds": 0,
                "dedupe_window_seconds": 0,
                "delivery_policy": "primary",
                "severity": "info",
            }
        organize_match = re.match(
            r"^every\s+(?P<frequency>weekday|day|daily|monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?\s+at\s+(?P<time>.+?)\s+organize\s+my\s+(?P<source>desktop|downloads|documents|download2|[a-z0-9._ /-]+)$",
            normalized,
            flags=re.IGNORECASE,
        )
        if organize_match:
            source_path = await self._resolve_desktop_automation_path(
                session_key,
                str(organize_match.group("source")),
                metadata,
            )
            if source_path is None:
                return {"response_text": f"I couldn't resolve the folder '{organize_match.group('source')}' inside your allowed host locations."}
            schedule = self._build_schedule_from_frequency_and_time(
                str(organize_match.group("frequency")),
                str(organize_match.group("time")),
            )
            if schedule is None:
                return {"response_text": "I couldn't understand that schedule. Try something like 'every weekday at 9 am organize my Desktop'."}
            return {
                "name": f"Organize {Path(source_path).name}",
                "trigger_type": "schedule",
                "watch_path": source_path,
                "schedule": schedule,
                "event_types": ["scheduled"],
                "file_extensions": [],
                "filename_pattern": "*",
                "action_type": "organize",
                "destination_path": "",
                "cooldown_seconds": 0,
                "dedupe_window_seconds": 0,
                "delivery_policy": "primary",
                "severity": "info",
            }
        return None

    async def _resolve_desktop_automation_path(
        self,
        session_key: str,
        value: str,
        metadata: dict[str, Any],
    ) -> str | None:
        raw = value.strip().strip("\"'")
        explicit = self._extract_explicit_host_path(raw)
        if explicit is not None:
            return explicit
        raw_normalized = raw.replace("\\", "/").strip().strip("/")
        if "/" in raw_normalized:
            base_name, *rest = [segment for segment in raw_normalized.split("/") if segment]
            base_path = self._resolve_known_host_folder_reference(base_name)
            if base_path is not None:
                return str(Path(base_path, *rest)).replace("\\", "/")
        known = self._resolve_known_host_folder_reference(raw)
        if known is not None:
            return known
        resolution = await self._resolve_host_directory_reference(session_key, raw, None, metadata)
        if resolution is not None and resolution.get("path"):
            return str(resolution["path"])
        return None

    def _format_desktop_automation_creation_response(self, rule: dict[str, Any]) -> str:
        trigger_type = str(rule.get("trigger_type", "file_watch"))
        display_name = str(rule.get("name") or rule.get("display_name") or rule.get("rule_id", "desktop rule"))
        if trigger_type == "schedule":
            return f"Created desktop automation '{display_name}' on schedule {rule.get('schedule', '')}."
        extension_summary = ""
        if rule.get("file_extensions"):
            extension_summary = f" for .{rule['file_extensions'][0]} files"
        return (
            f"Created desktop automation '{display_name}' watching {rule.get('watch_path', '')}{extension_summary} "
            f"with action {rule.get('action_type', 'notify')}."
        )

    def _filter_desktop_rules(self, rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            rule
            for rule in rules
            if str(rule.get("trigger", "")).lower() == "desktop"
            or str(rule.get("name", "")).lower().startswith("desktop:")
            or "trigger_type" in rule
        ]

    def _format_desktop_rules_response(self, rules: list[dict[str, Any]]) -> str:
        if not rules:
            return "No desktop automations configured."
        lines = ["Desktop automations:"]
        for rule in rules:
            display_name = str(rule.get("display_name") or rule.get("name", "desktop rule"))
            state = "paused" if bool(rule.get("paused")) else "active"
            trigger_type = str(rule.get("trigger_type", "file_watch"))
            if trigger_type == "schedule":
                summary = str(rule.get("schedule", ""))
            else:
                summary = str(rule.get("watch_path", ""))
                extensions = rule.get("file_extensions") or []
                if extensions:
                    summary = f"{summary} | .{extensions[0]}"
            lines.append(f"- {display_name}: {state} | {trigger_type} | {summary} | {rule.get('action_type', 'notify')}")
        return "\n".join(lines)

    def _match_desktop_rule_reference(
        self,
        rules: list[dict[str, Any]],
        raw_reference: str,
    ) -> tuple[dict[str, Any] | None, str | None]:
        reference = raw_reference.strip().strip("\"'")
        if not reference:
            return None, "Please provide a desktop automation name."
        compact_reference = self._compact_search_name(reference.removeprefix("desktop automation").strip())
        exact_matches: list[dict[str, Any]] = []
        partial_matches: list[dict[str, Any]] = []
        for rule in rules:
            candidates = {
                str(rule.get("name", "")),
                str(rule.get("display_name", "")),
                str(rule.get("rule_id", "")),
            }
            compact_candidates = {self._compact_search_name(candidate) for candidate in candidates if candidate}
            if compact_reference in compact_candidates:
                exact_matches.append(rule)
                continue
            if any(compact_reference and compact_reference in candidate for candidate in compact_candidates):
                partial_matches.append(rule)
        if len(exact_matches) == 1:
            return exact_matches[0], None
        if len(exact_matches) > 1:
            names = ", ".join(str(rule.get("display_name") or rule.get("name", "desktop rule")) for rule in exact_matches[:5])
            return None, f"I found multiple desktop automations matching that name: {names}."
        if len(partial_matches) == 1:
            return partial_matches[0], None
        if len(partial_matches) > 1:
            names = ", ".join(str(rule.get("display_name") or rule.get("name", "desktop rule")) for rule in partial_matches[:5])
            return None, f"I found multiple desktop automations matching that name: {names}."
        return None, f"I couldn't find a desktop automation named '{reference}'."

    async def _parse_desktop_routine_request(
        self,
        session_key: str,
        message: str,
        lowered: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any] | None:
        normalized = re.sub(r"\s+", " ", lowered).strip()
        watch_match = re.match(
            r"^when\s+(?:a|an)\s+(?:(?P<ext>[a-z0-9]+)\s+)?file\s+appears\s+in\s+(?P<source>.+?)\s*,?\s*(?P<action>move|copy)\s+it\s+to\s+(?P<dest>.+?)\s+and\s+notify\s+me$",
            normalized,
            flags=re.IGNORECASE,
        )
        if watch_match is not None:
            source_value = str(watch_match.group("source"))
            source_path = await self._resolve_desktop_automation_path(session_key, source_value, metadata)
            if source_path is None:
                return {"response_text": f"I couldn't resolve the watched folder '{source_value}' inside your allowed host locations."}
            dest_value = str(watch_match.group("dest")).strip()
            destination_path = await self._resolve_desktop_automation_path(session_key, dest_value, metadata)
            if destination_path is None:
                return {"response_text": f"I couldn't resolve the destination '{dest_value}' inside your allowed host locations."}
            action = str(watch_match.group("action")).lower()
            step_type = "move_host_file" if action == "move" else "copy_host_file"
            extension = (watch_match.group("ext") or "").strip().lower()
            steps: list[dict[str, Any]] = [
                {
                    "type": step_type,
                    "source": "{event_path}",
                    "destination": f"{destination_path.rstrip('/\\')}/{{event_name}}",
                }
            ]
            if "notify me" in normalized:
                past_tense = "Moved" if action == "move" else "Copied"
                steps.append({"type": "notify", "text": f"{past_tense} {{event_name}} to {destination_path}."})
            return {
                "name": f"Process {Path(source_path).name}",
                "trigger_type": "file_watch",
                "steps": steps,
                "summary": "",
                "watch_path": source_path,
                "event_types": ["file_created"],
                "file_extensions": [extension] if extension else [],
                "filename_pattern": "*",
                "cooldown_seconds": 10,
                "dedupe_window_seconds": 10,
                "delivery_policy": "primary",
                "severity": "info",
                "approval_mode": "ask_on_risky_step",
            }

        reminder_patterns = (
            r"^remind me(?:\s+(?P<day>today|tomorrow))?\s+at\s+(?P<time>.+?)\s+to\s+(?P<message>.+?)\s+and\s+open\s+(?P<targets>.+)$",
            r"^remind me at\s+(?P<time>.+?)\s+(?P<day>today|tomorrow)\s+to\s+(?P<message>.+?)\s+and\s+open\s+(?P<targets>.+)$",
        )
        for pattern in reminder_patterns:
            match = re.match(pattern, normalized, flags=re.IGNORECASE)
            if match is None:
                continue
            targets = str(match.group("targets"))
            steps, error_text = await self._parse_desktop_routine_targets(session_key, targets, metadata)
            if error_text:
                return {"response_text": error_text}
            assert steps is not None
            reminder_text = self._normalize_reminder_message(match.group("message"))
            if reminder_text is None:
                return {"response_text": "I need a reminder message before I can create that desktop routine."}
            run_at = (
                self._build_one_time_reminder_datetime(str(match.group("day")), str(match.group("time")))
                if match.groupdict().get("day")
                else self._build_next_desktop_routine_run_at(str(match.group("time")))
            )
            if run_at is None:
                return {"response_text": "I couldn't understand that reminder time. Try something like 'remind me tomorrow at 8 pm to study and open 6_semester folder'."}
            return {
                "name": reminder_text.removeprefix("Reminder: ").strip() or "Desktop routine reminder",
                "trigger_type": "reminder",
                "steps": [{"type": "notify", "text": reminder_text}, *steps],
                "summary": "",
                "run_at": run_at.isoformat(),
                "event_types": ["reminder"],
                "file_extensions": [],
                "filename_pattern": "*",
                "cooldown_seconds": 0,
                "dedupe_window_seconds": 0,
                "delivery_policy": "primary",
                "severity": "info",
                "approval_mode": "ask_on_risky_step",
            }

        named_schedule_match = re.match(
            r"^every\s+(?P<time>morning|afternoon|evening|night|noon|midnight)\s+open\s+(?P<targets>.+)$",
            normalized,
            flags=re.IGNORECASE,
        )
        if named_schedule_match is not None:
            steps, error_text = await self._parse_desktop_routine_targets(
                session_key,
                str(named_schedule_match.group("targets")),
                metadata,
            )
            if error_text:
                return {"response_text": error_text}
            assert steps is not None
            schedule = self._build_schedule_from_frequency_and_time("day", str(named_schedule_match.group("time")))
            if schedule is None:
                return {"response_text": "I couldn't understand that schedule. Try something like 'every evening open chrome and vscode'."}
            return {
                "name": "Daily desktop routine",
                "trigger_type": "schedule",
                "steps": steps,
                "summary": "",
                "schedule": schedule,
                "event_types": ["scheduled"],
                "file_extensions": [],
                "filename_pattern": "*",
                "cooldown_seconds": 0,
                "dedupe_window_seconds": 0,
                "delivery_policy": "primary",
                "severity": "info",
                "approval_mode": "ask_on_risky_step",
            }

        schedule_match = re.match(
            r"^every\s+(?P<frequency>weekday|day|daily|monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?\s+at\s+(?P<time>.+?)\s+open\s+(?P<targets>.+)$",
            normalized,
            flags=re.IGNORECASE,
        )
        if schedule_match is not None:
            steps, error_text = await self._parse_desktop_routine_targets(
                session_key,
                str(schedule_match.group("targets")),
                metadata,
            )
            if error_text:
                return {"response_text": error_text}
            assert steps is not None
            schedule = self._build_schedule_from_frequency_and_time(
                str(schedule_match.group("frequency")),
                str(schedule_match.group("time")),
            )
            if schedule is None:
                return {"response_text": "I couldn't understand that schedule. Try something like 'every weekday at 9 am open chrome and vscode'."}
            return {
                "name": "Scheduled desktop routine",
                "trigger_type": "schedule",
                "steps": steps,
                "summary": "",
                "schedule": schedule,
                "event_types": ["scheduled"],
                "file_extensions": [],
                "filename_pattern": "*",
                "cooldown_seconds": 0,
                "dedupe_window_seconds": 0,
                "delivery_policy": "primary",
                "severity": "info",
                "approval_mode": "ask_on_risky_step",
            }

        mode_match = re.match(
            r"^(?:create|make)\s+(?:a\s+)?(?P<name>.+?)\s+that\s+opens\s+(?P<targets>.+)$",
            normalized,
            flags=re.IGNORECASE,
        )
        if mode_match is not None:
            steps, error_text = await self._parse_desktop_routine_targets(
                session_key,
                str(mode_match.group("targets")),
                metadata,
            )
            if error_text:
                return {"response_text": error_text}
            assert steps is not None
            raw_name = str(mode_match.group("name")).strip().strip("\"'")
            cleaned_name = re.sub(r"^(?:my|the)\s+", "", raw_name, flags=re.IGNORECASE).strip()
            return {
                "name": cleaned_name or "Desktop routine",
                "trigger_type": "manual",
                "steps": steps,
                "summary": "",
                "event_types": ["manual"],
                "file_extensions": [],
                "filename_pattern": "*",
                "cooldown_seconds": 0,
                "dedupe_window_seconds": 0,
                "delivery_policy": "primary",
                "severity": "info",
                "approval_mode": "ask_on_risky_step",
            }
        return None

    async def _parse_desktop_routine_targets(
        self,
        session_key: str,
        targets_text: str,
        metadata: dict[str, Any],
    ) -> tuple[list[dict[str, Any]] | None, str | None]:
        aliases = self._known_app_aliases()
        tokens = [
            item.strip()
            for item in re.split(r"\s*,\s*|\s+and\s+", targets_text)
            if item.strip()
        ]
        if not tokens:
            return None, "I need at least one app or allowed host path to build that routine."
        steps: list[dict[str, Any]] = []
        unresolved: list[str] = []
        for token in tokens:
            cleaned = token.strip().strip("\"'").strip().rstrip(".")
            cleaned = re.sub(r"^(?:my|the)\s+", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"\s+(?:folder|directory|file|app|window)$", "", cleaned, flags=re.IGNORECASE)
            if not cleaned:
                continue
            if cleaned.lower() in aliases:
                steps.append({"type": "open_app", "target": cleaned.lower()})
                continue
            resolved_path, error_text = await self._resolve_desktop_routine_target(session_key, cleaned, metadata)
            if error_text:
                return None, error_text
            if resolved_path is None:
                unresolved.append(token.strip())
                continue
            steps.append({"type": "open_host_path", "path": resolved_path})
        if not steps:
            return None, "I couldn't resolve any apps or allowed host paths for that routine."
        if unresolved:
            joined = ", ".join(unresolved)
            return None, (
                f"I couldn't resolve these routine targets: {joined}. "
                "Use a configured app alias like chrome/vscode or an allowed host folder or file."
            )
        return steps, None

    async def _resolve_desktop_routine_target(
        self,
        session_key: str,
        raw_target: str,
        metadata: dict[str, Any],
    ) -> tuple[str | None, str | None]:
        explicit_path = self._extract_explicit_host_path(raw_target)
        if explicit_path is not None:
            return explicit_path, None
        resolved_path = await self._resolve_desktop_automation_path(session_key, raw_target, metadata)
        if resolved_path is not None:
            return resolved_path, None
        try:
            result = await self.tool_registry.dispatch(
                "search_host_files",
                {
                    "root": "@allowed",
                    "pattern": "*",
                    "name_query": raw_target,
                    "limit": 20,
                    "session_key": session_key,
                    "user_id": metadata.get("user_id", self.config.users.default_user_id),
                },
            )
        except Exception:
            return None, None
        ambiguous_folder_response = self._format_host_disambiguation_response(raw_target, result)
        if ambiguous_folder_response is not None:
            return None, ambiguous_folder_response
        preferred_file = self._pick_file_match(raw_target, result)
        preferred_folder = self._pick_folder_match_for_contents(raw_target, result)
        if Path(raw_target).suffix:
            if preferred_file is not None:
                return str(preferred_file.get("path", "")) or None, None
        else:
            if preferred_folder is not None:
                return str(preferred_folder.get("path", "")) or None, None
        if preferred_folder is not None and preferred_file is None:
            return str(preferred_folder.get("path", "")) or None, None
        if preferred_file is not None and preferred_folder is None:
            return str(preferred_file.get("path", "")) or None, None
        matches = list(result.get("matches", []))
        if len(matches) == 1:
            return str(matches[0].get("path", "")) or None, None
        files = [match for match in matches if not match.get("is_dir")]
        if len(files) > 1:
            return None, self._format_host_file_disambiguation_response(raw_target, result)
        return None, None

    def _build_next_desktop_routine_run_at(self, time_text: str) -> datetime | None:
        parsed_time = self._parse_reminder_time(time_text)
        if parsed_time is None:
            return None
        hour, minute = parsed_time
        now = datetime.now().astimezone()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target = target + timedelta(days=1)
        return target

    def _format_desktop_routine_creation_response(self, rule: dict[str, Any]) -> str:
        display_name = str(rule.get("name") or rule.get("display_name") or rule.get("routine_id", "desktop routine"))
        trigger_type = str(rule.get("trigger_type", "manual"))
        summary = str(rule.get("summary", "")).strip() or "desktop routine"
        lines = [f"Created desktop routine '{display_name}'."]
        if trigger_type == "schedule":
            lines.append(f"Trigger: schedule {rule.get('schedule', '')}")
        elif trigger_type == "reminder":
            lines.append(f"Trigger: reminder at {rule.get('run_at', '')}")
        elif trigger_type == "file_watch":
            watch_path = str(rule.get("watch_path", "")).strip()
            lines.append(f"Trigger: watch {watch_path}")
        else:
            lines.append("Trigger: manual")
        lines.append(f"Summary: {summary}")
        step_count = len(rule.get("steps", [])) if isinstance(rule.get("steps"), list) else 0
        if step_count:
            lines.append(f"Steps: {step_count}")
        risky_steps = self._count_desktop_routine_risky_steps(rule.get("steps", []))
        if risky_steps:
            lines.append(f"Risky steps: {risky_steps} (approvals will be requested at run time)")
        return "\n".join(lines)

    def _filter_desktop_routines(self, rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            rule
            for rule in rules
            if str(rule.get("trigger", "")).lower() == "desktop_routine"
            or str(rule.get("name", "")).lower().startswith("routine:")
            or bool(rule.get("routine"))
        ]

    def _format_desktop_routines_response(self, rules: list[dict[str, Any]]) -> str:
        if not rules:
            return "No desktop routines configured."
        lines = ["Desktop routines:"]
        for rule in rules:
            display_name = str(rule.get("display_name") or rule.get("name", "desktop routine"))
            state = "paused" if bool(rule.get("paused")) else "active"
            trigger_type = str(rule.get("trigger_type", "manual"))
            if trigger_type == "schedule":
                trigger_summary = str(rule.get("schedule", ""))
            elif trigger_type == "reminder":
                trigger_summary = str(rule.get("run_at", ""))
            elif trigger_type == "file_watch":
                trigger_summary = str(rule.get("watch_path", ""))
            else:
                trigger_summary = "manual"
            risky_text = ""
            risky_steps = int(rule.get("risky_step_count", 0) or 0)
            if risky_steps:
                risky_text = f" | risky steps: {risky_steps}"
            lines.append(
                f"- {display_name}: {state} | {trigger_type} | {trigger_summary} | {rule.get('summary', 'desktop routine')}{risky_text}"
            )
        return "\n".join(lines)

    def _format_desktop_routine_show_response(self, rule: dict[str, Any]) -> str:
        display_name = str(rule.get("display_name") or rule.get("name", "desktop routine"))
        lines = [f"Desktop routine: {display_name}"]
        lines.append(f"Trigger: {rule.get('trigger_type', 'manual')}")
        if rule.get("schedule"):
            lines.append(f"Schedule: {rule.get('schedule')}")
        if rule.get("run_at"):
            lines.append(f"Run at: {rule.get('run_at')}")
        if rule.get("watch_path"):
            lines.append(f"Watch path: {rule.get('watch_path')}")
        lines.append(f"Summary: {rule.get('summary', 'desktop routine')}")
        steps = list(rule.get("steps", []))
        if steps:
            lines.append("Steps:")
            for index, step in enumerate(steps, start=1):
                lines.append(f"{index}. {self._describe_desktop_routine_step(step)}")
        risky_steps = int(rule.get("risky_step_count", self._count_desktop_routine_risky_steps(steps)) or 0)
        if risky_steps:
            lines.append(f"Risky steps: {risky_steps}")
        return "\n".join(lines)

    def _match_desktop_routine_reference(
        self,
        rules: list[dict[str, Any]],
        raw_reference: str,
    ) -> tuple[dict[str, Any] | None, str | None]:
        reference = raw_reference.strip().strip("\"'")
        if not reference:
            return None, "Please provide a desktop routine name."
        compact_reference = self._compact_search_name(reference.removeprefix("desktop routine").strip())
        exact_matches: list[dict[str, Any]] = []
        partial_matches: list[dict[str, Any]] = []
        for rule in rules:
            candidates = {
                str(rule.get("name", "")),
                str(rule.get("display_name", "")),
                str(rule.get("routine_id", "")),
            }
            compact_candidates = {self._compact_search_name(candidate) for candidate in candidates if candidate}
            if compact_reference in compact_candidates:
                exact_matches.append(rule)
                continue
            if any(compact_reference and compact_reference in candidate for candidate in compact_candidates):
                partial_matches.append(rule)
        if len(exact_matches) == 1:
            return exact_matches[0], None
        if len(exact_matches) > 1:
            names = ", ".join(str(rule.get("display_name") or rule.get("name", "desktop routine")) for rule in exact_matches[:5])
            return None, f"I found multiple desktop routines matching that name: {names}."
        if len(partial_matches) == 1:
            return partial_matches[0], None
        if len(partial_matches) > 1:
            names = ", ".join(str(rule.get("display_name") or rule.get("name", "desktop routine")) for rule in partial_matches[:5])
            return None, f"I found multiple desktop routines matching that name: {names}."
        return None, f"I couldn't find a desktop routine named '{reference}'."

    def _describe_desktop_routine_step(self, step: dict[str, Any]) -> str:
        step_type = str(step.get("type", "")).strip().lower()
        if step_type == "open_app":
            return f"open app {step.get('target', 'app')}"
        if step_type == "open_host_path":
            return f"open {step.get('path', 'path')}"
        if step_type == "move_host_file":
            return f"move {step.get('source', '{event_path}')} to {step.get('destination', '')}"
        if step_type == "copy_host_file":
            return f"copy {step.get('source', '{event_path}')} to {step.get('destination', '')}"
        if step_type == "notify":
            return f"notify: {step.get('text', '')}"
        if step_type == "desktop_keyboard_hotkey":
            return f"press {step.get('hotkey', 'hotkey')}"
        if step_type == "desktop_keyboard_type":
            return "type text"
        if step_type == "desktop_mouse_click":
            return f"click at ({step.get('x', '?')}, {step.get('y', '?')})"
        return step_type.replace("_", " ")

    def _count_desktop_routine_risky_steps(self, steps: Any) -> int:
        if not isinstance(steps, list):
            return 0
        safe_hotkeys = {str(item).strip().lower() for item in getattr(self.config.desktop_input, "safe_hotkeys", [])}
        risky = 0
        for step in steps:
            if not isinstance(step, dict):
                continue
            step_type = str(step.get("type", "")).strip().lower()
            if step_type in {
                "desktop_mouse_click",
                "desktop_keyboard_type",
                "desktop_clipboard_write",
                "write_host_file",
                "delete_host_file",
                "move_host_file",
            }:
                risky += 1
                continue
            if step_type == "desktop_keyboard_hotkey":
                hotkey = "+".join(part.strip().lower() for part in str(step.get("hotkey", "")).split("+") if part.strip())
                if hotkey not in safe_hotkeys:
                    risky += 1
        return risky

    def _skill_payload(self, skill: Any) -> dict[str, Any]:
        return {
            "name": skill.name,
            "description": skill.description,
            "user_invocable": skill.user_invocable,
            "natural_language_enabled": skill.natural_language_enabled,
            "aliases": skill.aliases,
        }

    def _format_skill_summary(self, skill: Any) -> str:
        support = [
            "slash" if skill.user_invocable else None,
            "natural" if skill.natural_language_enabled else None,
        ]
        aliases = f" aliases: {', '.join(skill.aliases)}" if skill.aliases else ""
        mode_text = ", ".join(item for item in support if item) or "context-only"
        return f"- {skill.name}: {skill.description} [{mode_text}]{aliases}"

    def _log_skill_activation(self, skill_name: str, source: str, trace_id: str) -> None:
        try:
            LOGGER.info("skill_activated", skill=skill_name, source=source, trace_id=trace_id)
        except TypeError:
            LOGGER.info("skill_activated skill=%s source=%s trace_id=%s", skill_name, source, trace_id)

    async def _resolve_default_host_approval(self, user_id: str) -> tuple[str | None, str | None]:
        if self.system_access_manager is None:
            return None, "System access is not configured."
        approvals = await self.system_access_manager.list_approvals(user_id)
        pending = [
            approval
            for approval in approvals
            if str(approval.get("status", "")).lower() == "pending" and not bool(approval.get("expired"))
        ]
        if len(pending) == 1:
            return str(pending[0].get("approval_id", "")), None
        if len(pending) > 1:
            return None, "You have multiple pending host approvals. Run /host-approvals and then use /host-approve <approval_id>."
        if approvals:
            latest = approvals[0]
            latest_id = str(latest.get("approval_id", "unknown"))
            latest_status = str(latest.get("status", "unknown"))
            return None, f"The most recent host approval '{latest_id}' is already {latest_status}."
        return None, "No host approvals found."

    def _looks_like_latest_email_request(self, lowered: str) -> bool:
        has_email_word = "mail" in lowered or "email" in lowered
        has_latest_word = "last" in lowered or "latest" in lowered or "newest" in lowered
        received_hint = "received" in lowered or "got" in lowered or "inbox" in lowered
        return has_email_word and has_latest_word and received_hint

    def _looks_like_repo_count_request(self, lowered: str) -> bool:
        has_repo_word = "repo" in lowered or "repository" in lowered
        has_count_word = "how many" in lowered or "count" in lowered
        return has_repo_word and has_count_word

    def _looks_like_pull_request_check(self, lowered: str) -> bool:
        pr_hint = "pull request" in lowered or "pull requests" in lowered or re.search(r"\bpr\b", lowered) is not None
        status_hint = any(token in lowered for token in ("is there", "are there", "list", "show", "open"))
        repo_hint = "repo" in lowered or "repository" in lowered or self._extract_repo_from_text(lowered) is not None
        return bool(pr_hint and (status_hint or repo_hint))

    def _has_host_tools(self) -> bool:
        has_tool = getattr(self.tool_registry, "has", None)
        return callable(has_tool) and all(
            has_tool(name) for name in ("list_host_dir", "search_host_files", "read_host_file", "write_host_file", "exec_shell")
        )

    def _looks_like_list_folder_request(self, lowered: str) -> bool:
        return any(
            phrase in lowered
            for phrase in (
                "show me files",
                "show me the files",
                "show files",
                "list files",
                "list the files",
                "files in",
                "what is in",
                "what's in",
                "content of",
                "content if",
                "contents of",
                "contents if",
                "what is the content of",
                "what is the content if",
                "what are the contents of",
                "what are the contents if",
                "open folder",
                "show contents",
                "show the contents",
                "browse",
            )
        )

    def _match_known_host_folder(self, lowered: str) -> str | None:
        configured = getattr(self.config.system_access, "path_rules", [])
        configured_names: list[str] = []
        for rule in configured:
            raw_path = getattr(rule, "path", None)
            if raw_path is None and isinstance(rule, dict):
                raw_path = rule.get("path")
            if raw_path is None:
                continue
            candidate_name = Path(str(raw_path)).expanduser().resolve().name.strip()
            if candidate_name:
                configured_names.append(candidate_name)
        for candidate_name in sorted(configured_names, key=len, reverse=True):
            if candidate_name.lower() in lowered:
                return candidate_name

        mapping = {
            "downloads": "Downloads",
            "download": "Downloads",
            "desktop": "Desktop",
            "deskotp": "Desktop",
            "documents": "Documents",
            "document": "Documents",
            "docs": "Documents",
            "pictures": "Pictures",
            "picture": "Pictures",
            "music": "Music",
            "videos": "Videos",
            "video": "Videos",
        }
        for token, folder in mapping.items():
            if token in lowered:
                return folder
        return None

    def _resolve_known_host_folder_path(self, folder_name: str) -> str:
        configured = getattr(self.config.system_access, "path_rules", [])
        normalized_folder = folder_name.strip().lower()
        for rule in configured:
            raw_path = getattr(rule, "path", None)
            if raw_path is None and isinstance(rule, dict):
                raw_path = rule.get("path")
            if raw_path is None:
                continue
            candidate = Path(str(raw_path)).expanduser().resolve()
            if candidate.name.strip().lower() == normalized_folder:
                return str(candidate).replace("\\", "/")
        return f"~/{folder_name}"

    def _extract_host_search_term(self, lowered: str) -> str | None:
        patterns = (
            r"\b(?:search|find)(?:\s+for)?\s+(.+?)\s+(?:folder|file|directory)\b",
            r"\b(.+?)\s+(?:folder|file|directory)\s+(?:in|on)\s+(?:the\s+)?(?:r\s*:|r\s*drive|r\s*dive)\b",
            r"\b(?:search|find|look\s+for|look\s+up)\s+(.+?)\s+(?:in|inside|on)\s+(?:[a-z]:[\\/][^\n]+|[a-z]\s*:?\s*drive)\b",
            r"\b(?:search|find|look\s+for|look\s+up)\s+(.+)$",
        )
        for pattern in patterns:
            match = re.search(pattern, lowered)
            if not match:
                continue
            term = self._clean_host_search_term(match.group(1))
            if term:
                return term
        return None

    def _clean_host_search_term(self, value: str) -> str | None:
        cleaned = re.sub(r"[^a-z0-9._ -]", " ", value.lower())
        cleaned = re.sub(r"\b(?:called|named)\b", " ", cleaned)
        cleaned = re.sub(r"^(?:the|a|an)\s+", "", cleaned.strip())
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned or None

    def _is_contextual_folder_reference(self, value: str) -> bool:
        compact = self._compact_search_name(value)
        return compact in {
            "that",
            "this",
            "there",
            "here",
            "thatfolder",
            "thisfolder",
            "therefolder",
            "herefolder",
            "thatdirectory",
            "thisdirectory",
            "theredirectory",
            "heredirectory",
        }

    def _extract_host_search_root(self, lowered: str) -> str | None:
        path_match = re.search(r"\b([a-z]):[\\/]", lowered)
        if path_match:
            return f"{path_match.group(1).upper()}:/"
        drive_match = re.search(r"\b([a-z])\s*:?\s*(?:drive|dive)\b", lowered)
        if drive_match:
            return f"{drive_match.group(1).upper()}:/"
        return None

    def _extract_host_browse_folder_term(self, lowered: str) -> str | None:
        patterns = (
            r"\b(?:open|oepn|show|list|browse)\s+(?:the\s+)?(.+?)\s+(?:folder|directory)\b",
            r"\b(?:open|oepn|show|list|browse)\s+(?:the\s+)?folder\s+(.+)\b",
            r"\b(?:open|oepn|show|list|browse)\s+(?:the\s+)?files?\s+in\s+(.+)$",
            r"\b(?:give|show|tell)\s+(?:me\s+)?(?:the\s+)?(?:content|contents)\s+of\s+(?:the\s+)?(.+?)\s+(?:folder|directory)\b",
            r"\bwhat(?:'s|\s+is)\s+(?:inside|in)\s+(?:the\s+)?(.+?)\s+(?:folder|directory)\b",
        )
        for pattern in patterns:
            match = re.search(pattern, lowered)
            if not match:
                continue
            term = self._clean_host_search_term(match.group(1))
            if term and term not in {"downloads", "desktop", "documents", "pictures", "music", "videos"}:
                return term
        return None

    def _describe_host_root(self, root: str) -> str:
        normalized = root.strip().replace("\\", "/").lower()
        if normalized == "r:/":
            return "R drive"
        return root

    def _wants_folder_contents(self, lowered: str) -> bool:
        return any(
            phrase in lowered
            for phrase in (
                "inside it",
                "inside that",
                "inside the folder",
                "what is there inside",
                "what's inside",
                "tell me what is there inside",
                "tell me what's inside",
                "tell me what is inside",
                "tell me whats inside",
            )
        )

    def _compact_search_name(self, value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", value.lower())

    def _pick_folder_match_for_contents(self, search_term: str, result: dict[str, Any]) -> dict[str, Any] | None:
        matches = [match for match in result.get("matches", []) if match.get("is_dir")]
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]
        compact_term = self._compact_search_name(search_term)
        exactish = [
            match
            for match in matches
            if self._compact_search_name(str(match.get("name", ""))) == compact_term
        ]
        if len(exactish) == 1:
            return exactish[0]
        return None

    def _match_app_open_request(self, lowered: str) -> str | None:
        if not any(token in lowered for token in ("open", "launch", "start")):
            return None
        app_aliases = {
            "notepad": "notepad",
            "calculator": "calculator",
            "calc": "calculator",
            "paint": "paint",
            "file explorer": "explorer",
            "explorer": "explorer",
        }
        for alias, app_name in app_aliases.items():
            if alias in lowered:
                return app_name
        return None

    def _build_app_launch_command(self, app_name: str) -> str:
        command_map = {
            "notepad": "Start-Process -FilePath 'notepad.exe'",
            "calculator": "Start-Process -FilePath 'calc.exe'",
            "paint": "Start-Process -FilePath 'mspaint.exe'",
            "explorer": "Start-Process -FilePath 'explorer.exe'",
        }
        return command_map.get(app_name, f"Start-Process -FilePath '{app_name}'")

    def _looks_like_desktop_note_request(self, lowered: str) -> bool:
        return "desktop" in lowered and "note" in lowered and any(token in lowered for token in ("create", "make", "write"))

    def _parse_desktop_note_creation_request(self, message: str) -> tuple[str, str] | None:
        match = re.search(
            r"(?:create|make|write)\s+(?:a\s+)?note(?:\s+called|\s+named)?\s+([^\s]+)\s+(?:on\s+my\s+desktop|to\s+my\s+desktop)\s+with\s+content\s+(.+)",
            message,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        filename = match.group(1).strip().strip("\"'")
        content = match.group(2).strip().strip("\"'")
        if not filename.lower().endswith(".txt"):
            filename = f"{filename}.txt"
        return (filename, content) if filename and content else None

    async def _parse_contextual_host_file_creation_request(
        self,
        session_key: str,
        message: str,
        metadata: dict[str, Any],
    ) -> dict[str, str] | None:
        lowered = message.lower()
        filename, content = await self._parse_host_file_request_details(session_key, message)
        if not filename or not content:
            return None
        explicit_root = self._extract_host_search_root(lowered)
        explicit_path = self._extract_explicit_host_path(message)
        if explicit_path is not None:
            explicit_path_obj = Path(explicit_path)
            if explicit_path_obj.suffix and explicit_path_obj.name.lower() == filename.lower():
                return {
                    "target_dir": str(explicit_path_obj.parent).replace("\\", "/"),
                    "filename": explicit_path_obj.name,
                    "content": content,
                }
        target_dir = self._extract_explicit_host_directory(message)
        if target_dir is None:
            folder_reference = self._extract_named_folder_reference_for_write(lowered)
            if folder_reference is not None:
                resolution = await self._resolve_host_directory_reference(
                    session_key,
                    folder_reference,
                    explicit_root,
                    metadata,
                )
                if resolution is not None and resolution.get("response_text"):
                    return {"response_text": str(resolution["response_text"])}
                if resolution is not None:
                    target_dir = resolution.get("path")
            if target_dir is None and any(
                token in lowered
                for token in (" there", " here", "that folder", "this folder", "inside this", "inside that")
            ):
                target_dir = await self._resolve_recent_host_directory(
                    session_key,
                    explicit_root,
                    metadata,
                )
        if target_dir is None:
            return None
        return {"target_dir": target_dir, "filename": filename, "content": content}

    async def _parse_host_file_request_details(self, session_key: str, message: str) -> tuple[str | None, str | None]:
        lowered = message.lower()
        content = self._extract_host_file_content(message)
        filename = self._extract_explicit_filename(message)
        extension_hint = self._extract_file_extension_hint(lowered)
        if filename is None and extension_hint is not None and " file" in lowered:
            filename = f"untitled.{extension_hint}"
        if filename is None:
            return None, None
        filename = filename.strip().strip("\"'")
        if "." not in Path(filename).name and extension_hint is not None:
            filename = f"{filename}.{extension_hint}"
        elif "." not in Path(filename).name:
            filename = f"{filename}.txt"
        if content is None and self._looks_like_save_here_request(lowered):
            content = await self._resolve_recent_assistant_text(session_key)
        return (filename or None, content or None)

    def _clean_contextual_file_content(self, value: str) -> str:
        content = value.strip().strip("\"'")
        content = re.sub(
            r"^(?:it|this|there)\s+(?:(?:is|was|should\s+be|will\s+be)\s+)?(?:written|saved|stored)\s+",
            "",
            content,
            flags=re.IGNORECASE,
        )
        content = re.sub(r"^(?:it|this)\s+contains?\s+", "", content, flags=re.IGNORECASE)
        content = re.sub(r"\s+(?:is|be|should\s+be)\s+written\.?$", "", content, flags=re.IGNORECASE)
        return content.strip()

    def _extract_explicit_host_directory(self, message: str) -> str | None:
        match = re.search(r"\b(?:in|on)\s+([A-Za-z]:[\\/][^\n]+?)(?:\s+(?:with|containing|in\s+which)\b|$)", message)
        if not match:
            return None
        path = match.group(1).strip().rstrip(".")
        path = path.rstrip("\\/")
        return path or None

    def _extract_explicit_host_path(self, message: str) -> str | None:
        match = re.search(r"\b([A-Za-z]:[\\/][^\n\"']+)", message)
        if match is None:
            return None
        path = match.group(1).strip().rstrip(".")
        return path.rstrip("\\/") or None

    def _extract_named_folder_reference_for_write(self, lowered: str) -> str | None:
        patterns = (
            r"\b(?:in|inside|into|to)\s+(?:the\s+)?(.+?)\s+(?:folder|directory)\b",
            r"\b(?:in|inside|into)\s+(?!which\b)(?:the\s+)?([a-z0-9._ -]+)$",
            r"\b(?:save|create|make|write)\s+(?:a\s+)?file\s+(?:in|inside)\s+(?:the\s+)?(.+?)\s+(?:folder|directory)\b",
        )
        for pattern in patterns:
            match = re.search(pattern, lowered)
            if not match:
                continue
            term = self._clean_host_search_term(match.group(1))
            if term and not self._is_contextual_folder_reference(term):
                return term
        return None

    def _extract_explicit_filename(self, message: str) -> str | None:
        quoted = re.search(r"[\"']([^\"']+\.[A-Za-z0-9]{1,8})[\"']", message)
        if quoted:
            return quoted.group(1)
        named = re.search(r"\b(?:called|named|as)\s+([A-Za-z0-9_.-]+(?:\.[A-Za-z0-9]{1,8})?)", message, flags=re.IGNORECASE)
        if named:
            return named.group(1)
        generic = re.search(r"\b([A-Za-z0-9_.-]+\.[A-Za-z0-9]{1,8})\b", message)
        if generic:
            return generic.group(1)
        return None

    def _extract_file_extension_hint(self, lowered: str) -> str | None:
        extension_map = {
            "txt": "txt",
            "text": "txt",
            "py": "py",
            "python": "py",
            "ts": "ts",
            "typescript": "ts",
            "js": "js",
            "javascript": "js",
            "java": "java",
            "cpp": "cpp",
            "c++": "cpp",
            "c": "c",
            "md": "md",
            "markdown": "md",
            "json": "json",
            "html": "html",
            "css": "css",
            "xml": "xml",
            "csv": "csv",
            "pdf": "pdf",
            "doc": "doc",
            "docs": "doc",
            "docx": "docx",
        }
        for token, extension in extension_map.items():
            if re.search(rf"\b{re.escape(token)}\s+file\b", lowered):
                return extension
        return None

    def _extract_host_file_content(self, message: str) -> str | None:
        patterns = (
            r"\bwith\s+the\s+content\s+(.+)$",
            r"\bwith\s+content\s+(.+)$",
            r"\bcontaining\s+(.+)$",
            r"\breplace\s+(?:the\s+)?content\s+with\s+(.+)$",
            r"\bset\s+(?:the\s+)?content\s+to\s+(.+)$",
            r"\bupdate\s+(?:the\s+)?content\s+to\s+(.+)$",
            r"\bin\s+which\s+(.+)$",
            r"\bwhere\s+(.+)$",
        )
        for pattern in patterns:
            match = re.search(pattern, message, flags=re.IGNORECASE)
            if not match:
                continue
            content = self._clean_contextual_file_content(match.group(1))
            if content:
                return content
        return None

    def _looks_like_save_here_request(self, lowered: str) -> bool:
        return any(phrase in lowered for phrase in ("save it here", "save this here", "save it there", "save this there"))

    async def _resolve_recent_host_directory(
        self,
        session_key: str,
        explicit_root: str | None,
        metadata: dict[str, Any],
    ) -> str | None:
        recent_direct_path = await self._resolve_recent_host_directory_direct_path(session_key)
        if recent_direct_path is not None:
            if explicit_root is None:
                return recent_direct_path
            normalized_root = explicit_root.replace("\\", "/").rstrip("/").lower()
            normalized_path = recent_direct_path.replace("\\", "/").lower()
            if normalized_path.startswith(f"{normalized_root}/") or normalized_path == normalized_root:
                return recent_direct_path
        history = await self.session_manager.session_history(session_key, limit=12)
        patterns = (
            r"inside the\s+([A-Za-z]:[\\/][^\n:]+?)\s+folder:",
            r"folder at\s+([A-Za-z]:[\\/][^\n.]+)",
            r"->\s*([A-Za-z]:[\\/][^\n]+)",
        )
        for item in reversed(history):
            content = str(item.get("content", ""))
            for pattern in patterns:
                match = re.search(pattern, content, flags=re.IGNORECASE)
                if not match:
                    continue
                path = match.group(1).strip().rstrip(".")
                if re.search(r"\.[A-Za-z0-9]{1,6}$", path):
                    continue
                return path.rstrip("\\/")
            named_folder = re.search(r"inside the\s+(.+?)\s+(?:folder|directory):", content, flags=re.IGNORECASE)
            if named_folder:
                term = self._clean_host_search_term(named_folder.group(1))
                if term:
                    resolved = await self._resolve_host_directory_reference(session_key, term, explicit_root, metadata)
                    if resolved is not None and resolved.get("path"):
                        return str(resolved["path"])
        return None

    async def _parse_contextual_host_file_read_request(
        self,
        session_key: str,
        message: str,
        metadata: dict[str, Any],
    ) -> dict[str, str] | None:
        lowered = message.lower()
        if not self._looks_like_host_file_read_request(lowered):
            return None
        explicit_path = self._extract_explicit_host_path(message)
        if explicit_path is not None and Path(explicit_path).suffix:
            return {"path": explicit_path}

        filename = self._extract_explicit_filename(message)
        if filename is None:
            filename = self._extract_file_reference_from_content_request(lowered)
        if filename is None:
            return None

        explicit_root = self._extract_host_search_root(lowered)
        target_dir = self._extract_explicit_host_directory(message)
        if target_dir is None:
            folder_reference = self._extract_named_folder_reference_for_read(lowered)
            if folder_reference is not None:
                resolution = await self._resolve_host_directory_reference(
                    session_key,
                    folder_reference,
                    explicit_root,
                    metadata,
                )
                if resolution and resolution.get("response_text"):
                    return {"response_text": str(resolution["response_text"])}
                if resolution:
                    target_dir = resolution.get("path")
            elif any(
                token in lowered
                for token in (" there", " here", "that folder", "this folder", "inside this", "inside that")
            ):
                target_dir = await self._resolve_recent_host_directory(
                    session_key,
                    explicit_root,
                    metadata,
                )
            else:
                recent_dir = await self._resolve_recent_host_directory_direct_path(session_key)
                if recent_dir is not None:
                    target_dir = recent_dir
        if target_dir is not None:
            return {"path": f"{str(target_dir).rstrip('/\\')}/{filename}"}

        try:
            result = await self.tool_registry.dispatch(
                "search_host_files",
                {
                    "root": explicit_root or "@allowed",
                    "pattern": "*",
                    "name_query": filename,
                    "files_only": True,
                    "limit": 20,
                    "session_key": session_key,
                    "user_id": metadata.get("user_id", self.config.users.default_user_id),
                },
            )
        except Exception:
            return None
        matches = [match for match in result.get("matches", []) if not match.get("is_dir")]
        if not matches:
            return {"response_text": self._format_host_search_response(filename, result)}
        exact_match = self._pick_file_match(filename, result)
        if exact_match is not None:
            return {"path": str(exact_match.get("path", ""))}
        if len(matches) == 1:
            return {"path": str(matches[0].get("path", ""))}
        return {"response_text": self._format_host_file_disambiguation_response(filename, result)}

    async def _parse_contextual_host_file_update_request(
        self,
        session_key: str,
        message: str,
        metadata: dict[str, Any],
    ) -> dict[str, str] | None:
        lowered = message.lower().strip()
        if not self._looks_like_host_file_update_request(lowered):
            return None
        content = self._extract_host_file_update_content(message)
        if not content:
            return None

        explicit_path = self._extract_explicit_host_path(message)
        if explicit_path is not None and Path(explicit_path).suffix:
            return {"path": explicit_path, "content": content}

        read_resolution = await self._parse_contextual_host_file_read_request(session_key, message, metadata)
        if read_resolution is not None and read_resolution.get("path"):
            return {"path": str(read_resolution["path"]), "content": content}

        recent_path = await self._resolve_recent_host_file_path(session_key, metadata)
        if recent_path is not None:
            return {"path": recent_path, "content": content}
        return {
            "response_text": (
                "I need to know which file to update. Tell me the filename or path, "
                "for example: overwrite testing123.docx in the C practice folder with content xyz."
            )
        }

    async def _resolve_host_directory_reference(
        self,
        session_key: str,
        folder_reference: str,
        explicit_root: str | None,
        metadata: dict[str, Any],
    ) -> dict[str, str] | None:
        known_folder_path = self._resolve_known_host_folder_reference(folder_reference)
        if known_folder_path is not None:
            return {"path": known_folder_path}
        recent_path = await self._resolve_recent_host_directory_direct_path(session_key)
        if recent_path and self._compact_search_name(Path(recent_path).name) == self._compact_search_name(folder_reference):
            return {"path": recent_path}
        try:
            result = await self.tool_registry.dispatch(
                "search_host_files",
                {
                    "root": explicit_root or "@allowed",
                    "pattern": "*",
                    "name_query": folder_reference,
                    "directories_only": True,
                    "limit": 20,
                    "session_key": session_key,
                    "user_id": metadata.get("user_id", self.config.users.default_user_id),
                },
            )
        except Exception:
            return None
        ambiguous_response = self._format_host_disambiguation_response(folder_reference, result)
        if ambiguous_response is not None:
            return {"response_text": ambiguous_response}
        folder_match = self._pick_folder_match_for_contents(folder_reference, result)
        if folder_match is not None:
            return {"path": str(folder_match.get("path", "")) or ""}
        matches = [match for match in result.get("matches", []) if match.get("is_dir")]
        if len(matches) == 1:
            return {"path": str(matches[0].get("path", "")) or ""}
        return None

    def _resolve_known_host_folder_reference(self, folder_reference: str) -> str | None:
        configured = getattr(self.config.system_access, "path_rules", [])
        normalized_reference = self._compact_search_name(folder_reference)
        for rule in configured:
            raw_path = getattr(rule, "path", None)
            if raw_path is None and isinstance(rule, dict):
                raw_path = rule.get("path")
            if raw_path is None:
                continue
            candidate = Path(str(raw_path)).expanduser().resolve()
            if self._compact_search_name(candidate.name) == normalized_reference:
                return str(candidate).replace("\\", "/")
        return None

    def _looks_like_host_file_read_request(self, lowered: str) -> bool:
        if not (self._extract_explicit_filename(lowered) or self._extract_file_reference_from_content_request(lowered)):
            return False
        return any(
            phrase in lowered
            for phrase in (
                "open ",
                "read ",
                "show ",
                "what is the content of",
                "what's the content of",
                "content of",
                "contents of",
                "what is in",
                "what's in",
            )
        )

    def _extract_file_reference_from_content_request(self, lowered: str) -> str | None:
        patterns = (
            r"\b(?:content|contents)\s+of\s+(?:the\s+)?([a-z0-9_.-]+\.[a-z0-9]{1,8})\b",
            r"\bwhat(?:'s|\s+is)\s+(?:in|the\s+content\s+of)\s+(?:the\s+)?([a-z0-9_.-]+\.[a-z0-9]{1,8})\b",
            r"\bopen\s+(?:the\s+)?([a-z0-9_.-]+\.[a-z0-9]{1,8})\b",
            r"\bread\s+(?:the\s+)?([a-z0-9_.-]+\.[a-z0-9]{1,8})\b",
            r"^\s*([a-z0-9_.-]+\.[a-z0-9]{1,8})\s+(?:in|inside|from)\b",
        )
        for pattern in patterns:
            match = re.search(pattern, lowered, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None

    def _looks_like_host_file_update_request(self, lowered: str) -> bool:
        return any(
            phrase in lowered
            for phrase in (
                "change it to ",
                "change this to ",
                "replace it with ",
                "replace this with ",
                "update it to ",
                "update this to ",
                "overwrite ",
                "replace the content with ",
                "set the content to ",
                "update the content to ",
            )
        )

    def _extract_host_file_update_content(self, message: str) -> str | None:
        patterns = (
            r"\bchange\s+(?:it|this)\s+to\s+(.+)$",
            r"\breplace\s+(?:it|this)\s+with\s+(.+)$",
            r"\bupdate\s+(?:it|this)\s+to\s+(.+)$",
            r"\boverwrite\b(?:.+?)\bwith\s+content\s+(.+)$",
            r"\breplace\s+(?:the\s+)?content\s+with\s+(.+)$",
            r"\bset\s+(?:the\s+)?content\s+to\s+(.+)$",
            r"\bupdate\s+(?:the\s+)?content\s+to\s+(.+)$",
        )
        for pattern in patterns:
            match = re.search(pattern, message, flags=re.IGNORECASE)
            if not match:
                continue
            content = self._clean_contextual_file_content(match.group(1))
            if content:
                return content
        return None

    def _extract_named_folder_reference_for_read(self, lowered: str) -> str | None:
        patterns = (
            r"\b(?:in|inside|from)\s+(?:the\s+)?(.+?)\s+(?:folder|directory)\b",
            r"\b(?:file|content|contents)\s+in\s+(?:the\s+)?(.+?)\s+(?:folder|directory)\b",
        )
        for pattern in patterns:
            match = re.search(pattern, lowered)
            if not match:
                continue
            term = self._clean_host_search_term(match.group(1))
            if term and not self._is_contextual_folder_reference(term):
                return term
        return None

    def _pick_file_match(self, filename: str, result: dict[str, Any]) -> dict[str, Any] | None:
        matches = [match for match in result.get("matches", []) if not match.get("is_dir")]
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]
        compact_term = self._compact_search_name(filename)
        exactish = [
            match
            for match in matches
            if self._compact_search_name(str(match.get("name", ""))) == compact_term
        ]
        if len(exactish) == 1:
            return exactish[0]
        return None

    async def _resolve_recent_host_directory_direct_path(self, session_key: str) -> str | None:
        history = await self.session_manager.session_history(session_key, limit=12)
        patterns = (
            r"inside the\s+([A-Za-z]:[\\/][^\n:]+?)\s+folder:",
            r"folder at\s+([A-Za-z]:[\\/][^\n.]+)",
            r"->\s*([A-Za-z]:[\\/][^\n]+)",
        )
        for item in reversed(history):
            content = str(item.get("content", ""))
            for pattern in patterns:
                match = re.search(pattern, content, flags=re.IGNORECASE)
                if not match:
                    continue
                path = match.group(1).strip().rstrip(".")
                if re.search(r"\.[A-Za-z0-9]{1,6}$", path):
                    continue
                return path.rstrip("\\/")
            named_r_drive_folder = re.search(
                r"content of the\s+[`'\"]?(.+?)[`'\"]?\s+folder\s+on your r drive",
                content,
                flags=re.IGNORECASE,
            )
            if named_r_drive_folder:
                term = self._clean_host_search_term(named_r_drive_folder.group(1))
                if term:
                    return await self._resolve_recent_named_folder_on_r_drive(session_key, term)
        return None

    async def _resolve_recent_named_folder_on_r_drive(self, session_key: str, folder_name: str) -> str | None:
        try:
            result = await self.tool_registry.dispatch(
                "search_host_files",
                {
                    "root": "R:/",
                    "pattern": "*",
                    "name_query": folder_name,
                    "directories_only": True,
                    "limit": 20,
                    "session_key": session_key,
                    "user_id": self.config.users.default_user_id,
                },
            )
        except Exception:
            return None
        folder_match = self._pick_folder_match_for_contents(folder_name, result)
        if folder_match is not None:
            return str(folder_match.get("path", "")) or None
        matches = [match for match in result.get("matches", []) if match.get("is_dir")]
        if len(matches) == 1:
            return str(matches[0].get("path", "")) or None
        return None

    async def _resolve_recent_assistant_text(self, session_key: str) -> str | None:
        history = await self.session_manager.session_history(session_key, limit=12)
        for item in reversed(history):
            if str(item.get("role", "")) != "assistant":
                continue
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            if content.lower().startswith("host action approval required"):
                continue
            return content
        return None

    async def _resolve_recent_host_file_path(self, session_key: str, metadata: dict[str, Any]) -> str | None:
        history = await self.session_manager.session_history(session_key, limit=12)
        for item in reversed(history):
            if str(item.get("role", "")) != "assistant":
                continue
            content = str(item.get("content", ""))
            direct_path_match = re.search(r"([A-Za-z]:[\\/][^\n`'\"]+\.[A-Za-z0-9]{1,8})", content)
            if direct_path_match:
                return direct_path_match.group(1).strip().rstrip(".")
            file_match = re.search(r"content of\s+[`'\"]([^`'\"]+\.[A-Za-z0-9]{1,8})[`'\"]", content, flags=re.IGNORECASE)
            if not file_match:
                continue
            filename = file_match.group(1).strip()
            recent_dir = await self._resolve_recent_host_directory_direct_path(session_key)
            if recent_dir is not None:
                return f"{recent_dir.rstrip('/\\')}/{filename}"
            try:
                result = await self.tool_registry.dispatch(
                    "search_host_files",
                    {
                        "root": "@allowed",
                        "pattern": "*",
                        "name_query": filename,
                        "files_only": True,
                        "limit": 20,
                        "session_key": session_key,
                        "user_id": metadata.get("user_id", self.config.users.default_user_id),
                    },
                )
            except Exception:
                return None
            exact_match = self._pick_file_match(filename, result)
            if exact_match is not None:
                return str(exact_match.get("path", "")) or None
            matches = [match for match in result.get("matches", []) if not match.get("is_dir")]
            if len(matches) == 1:
                return str(matches[0].get("path", "")) or None
        return None

    def _format_host_read_response(self, path: str, result: dict[str, Any]) -> str:
        content = str(result.get("content", ""))
        filename = Path(path).name or path
        if not content:
            return f"The file `{filename}` is empty."
        preview = content if len(content) <= 4000 else f"{content[:4000].rstrip()}\n..."
        return f"Here is the content of `{filename}`:\n\n{preview}"

    def _format_host_directory_response(self, folder_name: str, result: dict[str, Any]) -> str:
        entries = result.get("entries", []) if isinstance(result, dict) else []
        if not entries:
            return f"The {folder_name} folder is empty or I could not find any visible items there."
        lines = [f"Here are the first {min(len(entries), 12)} item(s) in your {folder_name} folder:"]
        for entry in entries[:12]:
            suffix = "/" if entry.get("is_dir") else ""
            lines.append(f"- {entry.get('name', 'unknown')}{suffix}")
        if len(entries) > 12:
            lines.append(f"...and {len(entries) - 12} more.")
        return "\n".join(lines)

    def _format_host_search_response(self, search_term: str, result: dict[str, Any]) -> str:
        matches = result.get("matches", []) if isinstance(result, dict) else []
        directories_only = bool(result.get("directories_only")) if isinstance(result, dict) else False
        scope = self._describe_host_search_scope(result if isinstance(result, dict) else {})
        if not matches:
            noun = "folder" if directories_only else "file or folder"
            return f"I couldn't find any {noun} matching '{search_term}' in {scope}."
        noun = "folder(s)" if directories_only else "match(es)"
        lines = [f"I found {len(matches)} {noun} for '{search_term}' in {scope}:"]
        for match in matches[:10]:
            label = f"{match.get('name', 'unknown')}{'/' if match.get('is_dir') else ''}"
            lines.append(f"- {label} -> {match.get('path', '')}")
        if len(matches) > 10:
            lines.append(f"...and {len(matches) - 10} more.")
        return "\n".join(lines)

    def _format_host_disambiguation_response(self, search_term: str, result: dict[str, Any]) -> str | None:
        matches = [match for match in result.get("matches", []) if match.get("is_dir")]
        if len(matches) <= 1:
            return None
        compact_term = self._compact_search_name(search_term)
        exactish = [
            match for match in matches if self._compact_search_name(str(match.get("name", ""))) == compact_term
        ]
        if len(exactish) <= 1:
            return None
        lines = [f"I found multiple folders matching '{search_term}'. Please tell me which path you want:"]
        for match in exactish[:8]:
            lines.append(f"- {match.get('path', '')}")
        return "\n".join(lines)

    def _format_host_file_disambiguation_response(self, filename: str, result: dict[str, Any]) -> str:
        matches = [match for match in result.get("matches", []) if not match.get("is_dir")]
        lines = [f"I found multiple files matching '{filename}'. Please tell me which path you want:"]
        for match in matches[:8]:
            lines.append(f"- {match.get('path', '')}")
        return "\n".join(lines)

    def _describe_host_search_scope(self, result: dict[str, Any]) -> str:
        root = str(result.get("root", "")).strip()
        searched_roots = [str(item) for item in result.get("searched_roots", [])]
        if root and root not in {"", "@allowed"}:
            return root
        if any(str(item).replace("\\", "/").lower() == "r:/" for item in searched_roots):
            return "your allowed host locations, including R:/"
        return "your allowed host locations"

    def _format_host_folder_contents_response(self, folder_match: dict[str, Any], listing: dict[str, Any]) -> str:
        folder_path = str(folder_match.get("path", ""))
        entries = listing.get("entries", []) if isinstance(listing, dict) else []
        if not entries:
            return f"I found the folder at {folder_path}, but it looks empty."
        lines = [f"I found the folder at {folder_path}. Here are the first {min(len(entries), 12)} item(s) inside it:"]
        for entry in entries[:12]:
            suffix = "/" if entry.get("is_dir") else ""
            lines.append(f"- {entry.get('name', 'unknown')}{suffix}")
        if len(entries) > 12:
            lines.append(f"...and {len(entries) - 12} more.")
        return "\n".join(lines)

    def _format_host_exec_response(self, app_name: str, result: dict[str, Any]) -> str:
        status = str(result.get("status", "completed"))
        if status.startswith("blocked") or status in {"rejected", "expired"}:
            return (
                f"I didn't open {app_name} because the host action was {status}. "
                "Check /host-approvals if you want to review pending requests."
            )
        if int(result.get("exit_code", 1)) == 0:
            return f"I launched {app_name} on your computer."
        return f"I tried to launch {app_name}, but it failed: {result.get('stderr', 'unknown error')}"

    def _format_host_write_response(self, filename: str, folder_name: str, result: dict[str, Any]) -> str:
        status = str(result.get("status", "completed"))
        if status.startswith("blocked") or status in {"rejected", "expired"}:
            return (
                f"I didn't create {filename} in your {folder_name} because the host action was {status}. "
                "Check /host-approvals if you want to review pending requests."
            )
        fallback_from = result.get("fallback_from")
        if status == "completed:fallback_new_file" and fallback_from:
            original_name = Path(str(fallback_from)).name
            return (
                f"I couldn't overwrite {original_name} because Windows appears to have it open, "
                f"so I saved the updated content as {filename} in your {folder_name}."
            )
        return f"I created {filename} in your {folder_name}."

    def _format_browser_profiles_response(self, sessions: list[dict[str, Any]]) -> str:
        if not sessions:
            return "No saved browser profiles yet."
        lines = ["Saved browser profiles:"]
        for session in sessions[:10]:
            lines.append(
                f"- {session.get('site_name', 'site')}/{session.get('profile_name', 'default')} "
                f"({session.get('status', 'unknown')})"
            )
        if len(sessions) > 10:
            lines.append(f"...and {len(sessions) - 10} more.")
        return "\n".join(lines)

    def _format_browser_state_response(self, state: dict[str, Any]) -> str:
        active_profile = state.get("active_profile")
        active_tab = state.get("active_tab") or {}
        status = "Browser idle" if not active_tab else "Browser active"
        lines = [
            status,
            f"Headless: {'yes' if state.get('headless') else 'no'}",
            f"Open tabs: {len(state.get('tabs', []))}",
        ]
        if active_profile:
            lines.append(
                f"Profile: {active_profile.get('site_name', 'site')}/{active_profile.get('profile_name', 'default')} "
                f"({active_profile.get('status', 'unknown')})"
            )
        if active_tab:
            lines.append(f"Current tab: {active_tab.get('title', '(untitled)')}")
            lines.append(f"URL: {active_tab.get('url', 'unknown')}")
            lines.append(f"Tab id: {active_tab.get('tab_id', 'unknown')}")
        return "\n".join(lines)

    def _format_browser_tabs_response(self, result: dict[str, Any]) -> str:
        tabs = result.get("tabs", []) if isinstance(result, dict) else []
        current_tab_id = result.get("current_tab_id") if isinstance(result, dict) else None
        if not tabs:
            return "No browser tabs are open right now."
        lines = ["Open browser tabs:"]
        for tab in tabs[:10]:
            active = " [active]" if tab.get("tab_id") == current_tab_id else ""
            lines.append(
                f"- {tab.get('tab_id', 'unknown')}{active}: {tab.get('title', '(untitled)')} -> {tab.get('url', 'unknown')}"
            )
        if len(tabs) > 10:
            lines.append(f"...and {len(tabs) - 10} more.")
        return "\n".join(lines)

    def _format_browser_logs_response(self, logs: list[dict[str, Any]]) -> str:
        if not logs:
            return "No recent browser logs yet."
        lines = ["Recent browser logs:"]
        for entry in logs[:10]:
            category = entry.get("kind", entry.get("type", "log"))
            message = str(entry.get("message", entry.get("url", ""))).strip() or "(no message)"
            lines.append(f"- {category}: {message[:180]}")
        if len(logs) > 10:
            lines.append(f"...and {len(logs) - 10} more.")
        return "\n".join(lines)

    def _format_browser_downloads_response(self, downloads: list[dict[str, Any]]) -> str:
        if not downloads:
            return "No recent browser downloads yet."
        lines = ["Recent browser downloads:"]
        for item in downloads[:10]:
            filename = item.get("filename") or Path(str(item.get("path", ""))).name or "download"
            lines.append(f"- {filename} -> {item.get('path', 'unknown')}")
        if len(downloads) > 10:
            lines.append(f"...and {len(downloads) - 10} more.")
        return "\n".join(lines)

    def _format_latest_email_response(self, result: dict[str, Any]) -> str:
        if not result.get("found"):
            return str(result.get("message", "No matching Gmail threads were found."))
        snippet = str(result.get("snippet", "")).strip()
        body = str(result.get("body", "")).strip()
        preview = body or snippet or "No preview was available."
        preview = preview[:600].strip()
        return (
            "Latest email in your inbox:\n"
            f"From: {result.get('from', 'Unknown sender')}\n"
            f"Subject: {result.get('subject', '(no subject)')}\n"
            f"Date: {result.get('date', 'Unknown date')}\n\n"
            f"Preview:\n{preview}"
        )

    def _format_pull_request_response(self, owner: str, repo: str, result: dict[str, Any]) -> str:
        pull_requests = result.get("pull_requests", []) if isinstance(result, dict) else []
        if not pull_requests:
            return f"There are no open pull requests in {owner}/{repo}."
        lines = [f"There are {len(pull_requests)} open pull request(s) in {owner}/{repo}:"]
        for pull in pull_requests[:5]:
            lines.append(
                f"- #{pull.get('number')}: {pull.get('title', '(no title)')} by {pull.get('user', 'unknown')} "
                f"({pull.get('html_url', 'no url')})"
            )
        if len(pull_requests) > 5:
            lines.append(f"...and {len(pull_requests) - 5} more.")
        return "\n".join(lines)

    async def _resolve_repo_reference(self, session_key: str, message: str) -> tuple[str, str] | None:
        direct = self._extract_repo_from_text(message)
        if direct is not None:
            return direct
        history = await self.session_manager.session_history(session_key, limit=20)
        for item in reversed(history):
            content = str(item.get("content", ""))
            extracted = self._extract_repo_from_text(content)
            if extracted is not None:
                return extracted
        return None

    def _extract_repo_from_text(self, text: str) -> tuple[str, str] | None:
        match = re.search(r"\b([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.-]+)\b", text)
        if not match:
            return None
        return match.group(1), match.group(2)

    async def _persist_inline_exchange(
        self,
        session_key: str,
        user_message: str,
        assistant_message: str,
        metadata: dict[str, Any],
    ) -> None:
        session = await self.session_manager.load_or_create(session_key)
        await self.session_manager.append_message(
            session,
            create_message("user", user_message, **metadata),
        )
        await self.session_manager.append_message(
            session,
            create_message("assistant", assistant_message),
        )

    async def _resolve_user_id(self, connection_id: str, session_key: str) -> str:
        connection = self.connection_manager.get_connection(connection_id)
        if connection is not None and connection.user_id:
            return connection.user_id
        if session_key.startswith("telegram:"):
            sender_id = session_key.partition(":")[2]
            return await self.user_profiles.resolve_user_id("telegram", sender_id, {"channel": "telegram", "chat_id": sender_id})
        if session_key.startswith("webchat"):
            return await self.user_profiles.resolve_user_id("webchat", "webchat-default", {"channel": "webchat"})
        return await self.user_profiles.resolve_user_id("cli", "cli-local", {"channel": "cli"})
