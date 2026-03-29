"""Route validated protocol requests into the agent runtime."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from assistant.agent.session import create_message
from assistant.browser_workflows.state import (
    BROWSER_TASK_STATE_KEY,
    LEGACY_BROWSER_WORKFLOW_STATE_KEY,
    active_browser_task,
    browser_task_state_clear_keys,
    browser_task_state_update,
    normalize_browser_task_state,
)
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
    browser_workflow_engine: Any = None
    browser_monitor_service: Any = None
    _browser_session_locks: dict[str, asyncio.Lock] = field(default_factory=dict)

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

        if request.method == "agent.listen":
            from assistant.gateway.protocol import AgentListenParams
            from assistant.utils.mic import listen_to_system_mic
            
            params = AgentListenParams.model_validate(request.params)
            connection = self.connection_manager.get_connection(connection_id)
            metadata = {
                "user_id": getattr(connection, "user_id", "") or self.config.users.default_user_id,
                "channel": getattr(connection, "channel_name", "ws"),
                "device_id": getattr(connection, "device_id", ""),
            }
            
            # Notify frontend that we are now actively capturing
            await self.connection_manager.send_event(connection_id, "agent.mic_active", {})
            
            message = await listen_to_system_mic(timeout=7)
            
            await self.connection_manager.send_event(connection_id, "agent.mic_inactive", {"text": message})
            
            if not message:
                return ResponseFrame(id=request.id, ok=True, payload={"queued": False, "session_key": params.session_key, "error": "No speech detected"})
                
            return await self.route_user_message(
                connection_id=connection_id,
                request_id=request.id,
                session_key=params.session_key,
                message=message,
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

        browser_workflow = await self._handle_browser_workflow(
            connection_id=connection_id,
            request_id=request_id,
            session_key=session_key,
            message=message,
            metadata=metadata,
        )
        if browser_workflow is not None:
            return browser_workflow

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
                            f"Mode: {job.get('mode', 'direct')}\n"
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
                        f"- {item['cron_id']}: {'paused' if item['paused'] else 'active'} | {item.get('mode', 'direct')} | {item['schedule']} | {item['message']}"
                        for item in dynamic_jobs
                    )
                else:
                    lines.append("Chat-created cron jobs: none")
                if self.config.automation.cron_jobs:
                    lines.append("")
                    lines.append("Config cron jobs:")
                    lines.extend(
                        f"- config:{index}: active | {getattr(job, 'mode', 'direct')} | {job.schedule} | {job.message}"
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
            if subcommand in {"workflows", "recipes"}:
                workflows = []
                if self.browser_workflow_engine is not None:
                    workflows = self.browser_workflow_engine.available_workflows()
                if not workflows:
                    response_text = "No browser autonomous workflows are available right now."
                else:
                    lines = ["Browser autonomous workflows:"]
                    for item in workflows:
                        lines.append(f"- {item.get('name')}: {item.get('description')}")
                    response_text = "\n".join(lines)
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={"queued": False, "session_key": session_key, "command_response": response_text, "workflows": workflows},
                )
            if subcommand == "task":
                instruction = subargs.strip()
                if not instruction:
                    return ResponseFrame(id=request_id, ok=False, error="Use /browser task <instruction>.")
                workflow_response = await self._handle_browser_workflow(
                    connection_id=connection_id,
                    request_id=request_id,
                    session_key=session_key,
                    message=instruction,
                    metadata={"user_id": user_id, "channel": "slash", "trace_id": uuid4().hex},
                    force=True,
                )
                if workflow_response is not None:
                    return workflow_response
                return ResponseFrame(
                    id=request_id,
                    ok=False,
                    error="I couldn't map that instruction to a supported browser workflow yet. Use /browser workflows to see supported tasks.",
                )
            if subcommand in {"profile", "profiles", "sessions"}:
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
                    f"URL: {self._redact_browser_url(str(result.get('url', url)))}\n"
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
                    f"URL: {self._redact_browser_url(str(result.get('url', 'unknown')))}"
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
                    f"URL: {self._redact_browser_url(str(result.get('url', 'unknown')))}\n"
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
                    f"URL: {self._redact_browser_url(str(result.get('url', 'unknown')))}"
                )
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={"queued": False, "session_key": session_key, "command_response": response_text},
                )
            # ── Macro subcommands ─────────────────────────────────────────────────
            if subcommand == "watch":
                if self.browser_monitor_service is None:
                    return ResponseFrame(id=request_id, ok=False, error="Browser watches are not available right now.")
                url, condition = self._parse_browser_watch_arguments(subargs)
                if not url:
                    return ResponseFrame(id=request_id, ok=False, error="Use /browser watch <url> <condition>.")
                watch = await self.browser_monitor_service.create_watch(
                    user_id,
                    url,
                    condition or "Notify me when this page changes.",
                )
                preview = str(watch.get("baseline_preview", "")).strip()
                preview_text = f"\nBaseline preview: {preview[:200]}" if preview else ""
                response_text = (
                    f"Created browser watch '{watch.get('watch_id')}'.\n"
                    f"URL: {self._redact_browser_url(str(watch.get('url', url)))}\n"
                    f"Condition: {watch.get('condition', condition or '(none specified)')}"
                    f"{preview_text}"
                )
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={"queued": False, "session_key": session_key, "command_response": response_text},
                )
            if subcommand in {"watches", "list-watches", "list_watches"}:
                if self.browser_monitor_service is None:
                    return ResponseFrame(id=request_id, ok=False, error="Browser watches are not available right now.")
                watches = await self.browser_monitor_service.list_watches(user_id)
                if not watches:
                    response_text = "No browser watches saved yet.\nUse /browser watch <url> <condition> to create one."
                else:
                    lines = ["Saved browser watches:"]
                    for item in watches:
                        lines.append(
                            f"- {item.get('watch_id')}: {self._redact_browser_url(str(item.get('url', '')))}"
                            f" ({item.get('condition', '(no condition)')})"
                        )
                    response_text = "\n".join(lines)
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={"queued": False, "session_key": session_key, "command_response": response_text},
                )
            if subcommand in {"unwatch", "delete-watch", "delete_watch"}:
                if self.browser_monitor_service is None:
                    return ResponseFrame(id=request_id, ok=False, error="Browser watches are not available right now.")
                watch_id = subargs.strip()
                if not watch_id:
                    return ResponseFrame(id=request_id, ok=False, error="Use /browser unwatch <watch_id>.")
                deleted = await self.browser_monitor_service.delete_watch(user_id, watch_id)
                response_text = (
                    f"Removed browser watch '{watch_id}'."
                    if deleted
                    else f"No browser watch named '{watch_id}' was found."
                )
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={"queued": False, "session_key": session_key, "command_response": response_text},
                )
            if subcommand == "macros":
                if not getattr(self.config.browser_workflows, "macro_shortcuts_enabled", True):
                    return ResponseFrame(id=request_id, ok=False, error="Browser macros are disabled in the current configuration.")
                try:
                    from assistant.browser_workflows.browser_macros import BrowserMacroStore
                    store = BrowserMacroStore(self.config.agent.workspace_dir)
                    macros = store.list_macros()
                    if not macros:
                        response_text = "No browser macros saved yet.\nUse /browser save <alias> <command> to create one."
                    else:
                        lines = ["Saved browser macros:"]
                        for alias, cmd in macros.items():
                            lines.append(f"  /browser run {alias}  →  {cmd}")
                        response_text = "\n".join(lines)
                except Exception as exc:
                    response_text = f"Could not load macros: {exc}"
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={"queued": False, "session_key": session_key, "command_response": response_text},
                )
            if subcommand == "save":
                if not getattr(self.config.browser_workflows, "macro_shortcuts_enabled", True):
                    return ResponseFrame(id=request_id, ok=False, error="Browser macros are disabled in the current configuration.")
                # /browser save <alias> <command>
                save_parts = subargs.split(maxsplit=1)
                if len(save_parts) < 2:
                    return ResponseFrame(id=request_id, ok=False, error="Use /browser save <alias> <command>.")
                macro_alias = save_parts[0].strip().lower()
                macro_cmd = save_parts[1].strip()
                if not macro_alias or not macro_cmd:
                    return ResponseFrame(id=request_id, ok=False, error="Alias and command must not be empty.")
                try:
                    from assistant.browser_workflows.browser_macros import BrowserMacroStore
                    store = BrowserMacroStore(self.config.agent.workspace_dir)
                    store.save_macro(macro_alias, macro_cmd)
                    response_text = f"✅ Saved macro '{macro_alias}'.\nRun it with: /browser run {macro_alias}"
                except Exception as exc:
                    response_text = f"Could not save macro: {exc}"
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={"queued": False, "session_key": session_key, "command_response": response_text},
                )
            if subcommand == "run":
                if not getattr(self.config.browser_workflows, "macro_shortcuts_enabled", True):
                    return ResponseFrame(id=request_id, ok=False, error="Browser macros are disabled in the current configuration.")
                # /browser run <alias>
                alias = subargs.strip().lower()
                if not alias:
                    return ResponseFrame(id=request_id, ok=False, error="Use /browser run <alias>.")
                try:
                    from assistant.browser_workflows.browser_macros import BrowserMacroStore
                    store = BrowserMacroStore(self.config.agent.workspace_dir)
                    macro_cmd = store.get_macro(alias)
                except Exception as exc:
                    return ResponseFrame(id=request_id, ok=False, error=f"Could not load macro: {exc}")
                if macro_cmd is None:
                    return ResponseFrame(id=request_id, ok=False, error=f"No macro named '{alias}'. Use /browser macros to list all.")
                workflow_response = await self._handle_browser_workflow(
                    connection_id=connection_id,
                    request_id=request_id,
                    session_key=session_key,
                    message=macro_cmd,
                    metadata={"user_id": user_id, "channel": "slash", "trace_id": uuid4().hex},
                    force=True,
                )
                if workflow_response is not None:
                    return workflow_response
                return ResponseFrame(
                    id=request_id,
                    ok=False,
                    error=f"Macro '{alias}' ran ('{macro_cmd}') but no matching browser workflow was found.",
                )
            if subcommand in {"delete-macro", "delete_macro", "remove-macro"}:
                if not getattr(self.config.browser_workflows, "macro_shortcuts_enabled", True):
                    return ResponseFrame(id=request_id, ok=False, error="Browser macros are disabled in the current configuration.")
                alias = subargs.strip().lower()
                if not alias:
                    return ResponseFrame(id=request_id, ok=False, error="Use /browser delete-macro <alias>.")
                try:
                    from assistant.browser_workflows.browser_macros import BrowserMacroStore
                    store = BrowserMacroStore(self.config.agent.workspace_dir)
                    deleted = store.delete_macro(alias)
                except Exception as exc:
                    return ResponseFrame(id=request_id, ok=False, error=f"Could not delete macro: {exc}")
                response_text = f"Deleted macro '{alias}'." if deleted else f"No macro named '{alias}' found."
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

    def _browser_lock(self, session_key: str) -> asyncio.Lock:
        lock = self._browser_session_locks.get(session_key)
        if lock is None:
            lock = asyncio.Lock()
            self._browser_session_locks[session_key] = lock
        return lock

    def _looks_like_otp_reply(self, message: str) -> bool:
        return re.fullmatch(r"\s*\d{4,8}\s*", message or "") is not None

    def _looks_like_captcha_reply(self, message: str) -> bool:
        compact = message.strip()
        if not compact or len(compact) > 32:
            return False
        if "\n" in compact:
            return False
        return re.fullmatch(r"[A-Za-z0-9 -]{3,32}", compact) is not None

    async def _resume_after_pending_browser_input(
        self,
        *,
        connection_id: str,
        request_id: str,
        session_key: str,
        message: str,
        metadata: dict[str, Any],
        task_state: dict[str, Any],
        challenge_kind: str,
    ) -> ResponseFrame:
        user_id = str(metadata.get("user_id") or await self._resolve_user_id(connection_id, session_key))
        runtime = getattr(self.tool_registry, "browser_runtime", None)
        if runtime is None:
            raise RuntimeError("Browser runtime is unavailable.")
        if challenge_kind == "otp":
            await runtime.submit_pending_otp(message.strip(), user_id=user_id)
        else:
            await runtime.submit_pending_captcha(message.strip(), user_id=user_id)

        active_task = active_browser_task(task_state)
        updated_state = browser_task_state_update(
            active_task={
                **active_task,
                "blocked_reason": "",
                "awaiting_followup": "continue",
            } if active_task else {},
            pending_confirmation=dict(task_state.get("pending_confirmation") or {}),
            pending_login=dict(task_state.get("pending_login") or {}),
            pending_disambiguation=dict(task_state.get("pending_disambiguation") or {}),
            next_task_mode_override=str(task_state.get("next_task_mode_override", "") or ""),
        )
        await self._update_session_metadata(session_key, updates=updated_state)
        refreshed_state = await self._get_browser_task_state(session_key)
        result = await self.browser_workflow_engine.maybe_run(
            "continue",
            user_id=user_id,
            session_key=session_key,
            channel=str(metadata.get("channel", "ws")),
            previous_state=refreshed_state,
            force=True,
            connection_id=connection_id,
        )
        if result is None:
            response_text = "Filled the pending browser challenge and resumed the page."
            await self._persist_inline_exchange(session_key, message, response_text, metadata)
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={"queued": False, "session_key": session_key, "command_response": response_text},
            )
        await self._apply_browser_task_state(session_key, result)
        response_text = self._compose_browser_workflow_response(result)
        await self._persist_inline_exchange(session_key, message, response_text, metadata)
        return ResponseFrame(
            id=request_id,
            ok=True,
            payload={
                "queued": False,
                "session_key": session_key,
                "command_response": response_text,
                "browser_workflow": {
                    "recipe_name": result.recipe_name,
                    "status": result.status,
                    "payload": result.payload,
                },
            },
        )

    async def _handle_browser_workflow(
        self,
        connection_id: str,
        request_id: str,
        session_key: str,
        message: str,
        metadata: dict[str, Any],
        *,
        force: bool = False,
    ) -> ResponseFrame | None:
        if self.browser_workflow_engine is None or not self.config.browser_workflows.enabled:
            return None
        async with self._browser_lock(session_key):
            user_id = str(metadata.get("user_id") or await self._resolve_user_id(connection_id, session_key))
            channel = str(metadata.get("channel", "ws"))
            task_state = await self._get_browser_task_state(session_key)
            pending_disambiguation = dict(task_state.get("pending_disambiguation") or {})
            pending_otp = dict(task_state.get("pending_otp") or {})
            pending_captcha = dict(task_state.get("pending_captcha") or {})
            if pending_otp and self._looks_like_otp_reply(message):
                return await self._resume_after_pending_browser_input(
                    connection_id=connection_id,
                    request_id=request_id,
                    session_key=session_key,
                    message=message,
                    metadata=metadata,
                    task_state=task_state,
                    challenge_kind="otp",
                )
            if pending_captcha and self._looks_like_captcha_reply(message):
                return await self._resume_after_pending_browser_input(
                    connection_id=connection_id,
                    request_id=request_id,
                    session_key=session_key,
                    message=message,
                    metadata=metadata,
                    task_state=task_state,
                    challenge_kind="captcha",
                )
            standalone_override = self.browser_workflow_engine.nlp.standalone_execution_override(message)
            if standalone_override is not None:
                await self._update_session_metadata(
                    session_key,
                    updates=browser_task_state_update(
                        active_task=active_browser_task(task_state),
                        pending_confirmation=dict(task_state.get("pending_confirmation") or {}),
                        pending_login=dict(task_state.get("pending_login") or {}),
                        pending_otp=pending_otp,
                        pending_captcha=pending_captcha,
                        pending_disambiguation=pending_disambiguation,
                        next_task_mode_override=standalone_override,
                    ),
                )
                response_text = (
                    "I'll show the next browser task in a visible window on the host machine."
                    if standalone_override == "headed"
                    else "I'll run the next browser task silently in the background."
                )
                await self._persist_inline_exchange(session_key, message, response_text, metadata)
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={"queued": False, "session_key": session_key, "command_response": response_text},
                )
            if pending_disambiguation and self._looks_like_disambiguation_cancel(message):
                await self._update_session_metadata(
                    session_key,
                    updates=browser_task_state_update(
                        active_task=active_browser_task(task_state),
                        pending_confirmation=dict(task_state.get("pending_confirmation") or {}),
                        pending_login=dict(task_state.get("pending_login") or {}),
                        pending_otp=pending_otp,
                        pending_captcha=pending_captcha,
                        next_task_mode_override=str(task_state.get("next_task_mode_override", "") or ""),
                    ),
                )
                response_text = "Okay, I cleared that inferred browser task. Tell me what you want me to do instead."
                await self._persist_inline_exchange(session_key, message, response_text, metadata)
                return ResponseFrame(
                    id=request_id,
                    ok=True,
                    payload={"queued": False, "session_key": session_key, "command_response": response_text},
                )
            result = await self.browser_workflow_engine.maybe_run(
                message,
                user_id=user_id,
                session_key=session_key,
                channel=channel,
                previous_state=task_state,
                force=force,
                connection_id=connection_id,
            )
            if result is None:
                return None
            await self._apply_browser_task_state(session_key, result)
            response_text = self._compose_browser_workflow_response(result)
            await self._persist_inline_exchange(session_key, message, response_text, metadata)
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={
                    "queued": False,
                    "session_key": session_key,
                    "command_response": response_text,
                    "browser_workflow": {
                        "recipe_name": result.recipe_name,
                        "status": result.status,
                        "payload": result.payload,
                    },
                },
            )

    async def _get_browser_task_state(self, session_key: str) -> dict[str, Any]:
        session = await self.session_manager.load_or_create(session_key)
        metadata = getattr(session, "metadata", {}) or {}
        raw = metadata.get(BROWSER_TASK_STATE_KEY)
        if isinstance(raw, dict):
            return normalize_browser_task_state(raw)
        legacy = metadata.get(LEGACY_BROWSER_WORKFLOW_STATE_KEY, {})
        return normalize_browser_task_state(legacy if isinstance(legacy, dict) else {})

    def _looks_like_disambiguation_cancel(self, message: str) -> bool:
        normalized = message.strip().lower()
        return normalized in {
            "no",
            "nope",
            "not that",
            "not this",
            "cancel",
            "never mind",
            "stop",
            "don't do that",
        }

    async def _apply_browser_task_state(self, session_key: str, result: Any) -> None:
        if getattr(result, "clear_state", False):
            await self._update_session_metadata(session_key, remove_keys=browser_task_state_clear_keys())
            return
        state_update = getattr(result, "state_update", None) or {}
        if not isinstance(state_update, dict) or not state_update:
            return
        await self._update_session_metadata(session_key, updates=state_update)

    async def _update_session_metadata(
        self,
        session_key: str,
        *,
        updates: dict[str, Any] | None = None,
        remove_keys: list[str] | None = None,
    ) -> None:
        updater = getattr(self.session_manager, "update_metadata", None)
        if callable(updater):
            await updater(session_key, updates or {}, remove_keys=remove_keys)
            return
        session = await self.session_manager.load_or_create(session_key)
        metadata = getattr(session, "metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
            session.metadata = metadata
        if updates:
            metadata.update(updates)
        if remove_keys:
            for key in remove_keys:
                metadata.pop(key, None)

    def _compose_browser_workflow_response(self, result: Any) -> str:
        def _compact(value: str) -> str:
            return re.sub(r"[^a-z0-9]+", "", value.lower())

        progress = [str(item).strip() for item in getattr(result, "progress_lines", []) if str(item).strip()]
        response = str(getattr(result, "response_text", "")).strip()
        if response and progress:
            compact_response = _compact(response)
            progress = [item for item in progress if _compact(item) and _compact(item) not in compact_response]
        if response and response not in progress:
            progress.append(response)
        return "\n".join(progress) if progress else response

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

        brightness_shortcut = await self._handle_brightness_shortcut(
            request_id, session_key, message, lowered, metadata
        )
        if brightness_shortcut is not None:
            return brightness_shortcut

        default_browser_shortcut = await self._handle_default_browser_settings_shortcut(
            request_id, session_key, message, lowered, metadata
        )
        if default_browser_shortcut is not None:
            return default_browser_shortcut

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

    async def _handle_brightness_shortcut(
        self,
        request_id: str,
        session_key: str,
        original_message: str,
        lowered: str,
        metadata: dict[str, Any],
    ) -> ResponseFrame | None:
        if not self.tool_registry.has("set_windows_brightness"):
            return None
        pct = self._parse_brightness_percent_from_message(lowered)
        if pct is None:
            return None
        session = await self.session_manager.load_or_create(session_key)
        session_id = getattr(session, "session_id", session_key)
        try:
            result = await self.tool_registry.dispatch(
                "set_windows_brightness",
                {
                    "percent": pct,
                    "session_id": session_id,
                    "user_id": metadata.get("user_id", self.config.users.default_user_id),
                },
            )
        except Exception as exc:
            return ResponseFrame(id=request_id, ok=False, error=str(exc))
        response_text = self._format_brightness_shortcut_response(result)
        await self._persist_inline_exchange(session_key, original_message, response_text, metadata)
        return ResponseFrame(
            id=request_id,
            ok=True,
            payload={"queued": False, "session_key": session_key, "command_response": response_text},
        )

    def _parse_brightness_percent_from_message(self, lowered: str) -> int | None:
        has_topic = "brightness" in lowered or ("screen" in lowered and "dim" in lowered)
        if not has_topic:
            return None
        change_hints = (
            "set ",
            "change ",
            "adjust ",
            "lower ",
            "raise ",
            "decrease ",
            "increase ",
            "dim ",
            "brighten ",
            "turn down",
            "turn up",
            "make ",
            "put ",
        )
        if not any(h in lowered for h in change_hints) and " to " not in lowered:
            return None
        patterns = (
            r"\b(?:to|at)\s+(\d{1,3})\s*(?:percent|%)?\b",
            r"\bbrightness\s*(?:to|at|=|:)\s*(\d{1,3})\b",
            r"\b(\d{1,3})\s*%\s*(?:for\s+)?(?:screen\s+)?brightness\b",
        )
        for pattern in patterns:
            match = re.search(pattern, lowered)
            if not match:
                continue
            value = int(match.group(1))
            if 0 <= value <= 100:
                return value
            return None
        return None

    def _format_brightness_shortcut_response(self, result: dict[str, Any]) -> str:
        status = str(result.get("status", "completed"))
        if status.startswith("blocked") or status in {"rejected", "expired"}:
            return (
                "I couldn't change brightness because that host action was blocked or timed out. "
                "Enable system access and check the Host access page for pending approvals."
            )
        stderr = str(result.get("stderr", "")).strip()
        exit_code = int(result.get("exit_code", 1))
        if exit_code != 0:
            lowered_err = stderr.lower()
            if "disabled" in lowered_err:
                return (
                    "Brightness control needs host system access. Set system_access.enabled = true "
                    "(or SYSTEM_ACCESS_ENABLED=true), then restart the gateway."
                )
            return f"I couldn't change the brightness: {stderr or 'unknown error'}"
        pct = result.get("brightness_percent")
        return f"Set display brightness to {pct}%."

    async def _handle_default_browser_settings_shortcut(
        self,
        request_id: str,
        session_key: str,
        original_message: str,
        lowered: str,
        metadata: dict[str, Any],
    ) -> ResponseFrame | None:
        if not self._looks_like_default_browser_change_request(lowered):
            return None
        if self.system_access_manager is None:
            response_text = (
                "I can open Windows Default apps for you once host system access is wired up on this gateway."
            )
            await self._persist_inline_exchange(session_key, original_message, response_text, metadata)
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={"queued": False, "session_key": session_key, "command_response": response_text},
            )
        if not self.config.system_access.enabled:
            response_text = (
                "To open Windows default-app settings from chat, enable host system access "
                "(system_access.enabled or SYSTEM_ACCESS_ENABLED=true), then restart the gateway."
            )
            await self._persist_inline_exchange(session_key, original_message, response_text, metadata)
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={"queued": False, "session_key": session_key, "command_response": response_text},
            )
        session = await self.session_manager.load_or_create(session_key)
        session_id = getattr(session, "session_id", session_key)
        user_id = str(metadata.get("user_id", self.config.users.default_user_id))
        try:
            result = await self.system_access_manager.open_ms_settings_default_apps(
                session_id=session_id,
                user_id=user_id,
            )
        except RuntimeError as exc:
            response_text = str(exc)
            await self._persist_inline_exchange(session_key, original_message, response_text, metadata)
            return ResponseFrame(
                id=request_id,
                ok=True,
                payload={"queued": False, "session_key": session_key, "command_response": response_text},
            )
        except Exception as exc:
            return ResponseFrame(id=request_id, ok=False, error=str(exc))
        hint = self._extract_preferred_browser_name(lowered)
        response_text = self._format_open_default_apps_response(result, hint)
        await self._persist_inline_exchange(session_key, original_message, response_text, metadata)
        return ResponseFrame(
            id=request_id,
            ok=True,
            payload={"queued": False, "session_key": session_key, "command_response": response_text},
        )

    def _looks_like_default_browser_change_request(self, lowered: str) -> bool:
        if "browser" not in lowered:
            return False
        if "default" not in lowered:
            return False
        if lowered.lstrip().startswith(("what ", "how ", "why ", "which ", "when ", "who ")):
            return False
        return any(
            phrase in lowered
            for phrase in (
                "change ",
                " set ",
                "switch ",
                "make ",
                "turn ",
                "use ",
                "pick ",
                "choose ",
                " put ",
                " as default",
                "open ",
                "show ",
                "launch ",
            )
        ) or re.search(r"\bto\s+(brave|chrome|edge|firefox|opera|vivaldi)\b", lowered) is not None

    def _extract_preferred_browser_name(self, lowered: str) -> str | None:
        known = {
            "brave": "Brave",
            "chrome": "Chrome",
            "edge": "Edge",
            "firefox": "Firefox",
            "opera": "Opera",
            "vivaldi": "Vivaldi",
        }
        for key, label in known.items():
            if re.search(rf"\b{re.escape(key)}\b", lowered):
                return label
        return None

    def _format_open_default_apps_response(self, result: dict[str, Any], browser_hint: str | None) -> str:
        status = str(result.get("status", "completed"))
        if status.startswith("blocked") or status in {"rejected", "expired"}:
            return "Could not open Default apps settings (host action blocked). Check Host access for pending approvals."
        if int(result.get("exit_code", 1)) != 0:
            err = str(result.get("stderr", "") or result.get("stdout", "")).strip()
            return f"Could not open Default apps settings: {err or 'unknown error'}"
        pick = (
            f" Then under Web browser, choose {browser_hint}."
            if browser_hint
            else " Then under Web browser, pick the browser you want."
        )
        return (
            "Opened Settings (Default apps). "
            f"{pick} "
            "Windows still needs you to confirm the default; I cannot set it silently."
        )

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
            f"Mode: {job.get('mode', 'direct')}\n"
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

        folder_name = self._match_known_host_folder(lowered)
        if folder_name and self._looks_like_list_folder_request(lowered):
            try:
                result = await self.tool_registry.dispatch(
                    "list_host_dir",
                    {
                        "path": f"~/{folder_name}",
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

        explicit_root = self._extract_host_search_root(lowered)
        if explicit_root is not None and self._looks_like_list_folder_request(lowered) and folder_name is None:
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

        browse_term = self._extract_host_browse_folder_term(lowered)
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
                        "root": explicit_root or "@allowed",
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
            if wants_folder and self._wants_folder_contents(lowered):
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

        contextual_host_write = await self._parse_contextual_host_file_creation_request(
            session_key,
            original_message,
            metadata,
        )
        if contextual_host_write is not None:
            target_dir, filename, content = contextual_host_write
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
        if any(token in lowered for token in ("desktop", "downloads", "documents", "notepad", "folder", "drive", "r:")):
            hints.append(
                "You have host-system access tools inside the configured allowed host roots: list_host_dir, search_host_files, "
                "read_host_file, write_host_file, and exec_shell with host=true. Do not claim you are limited to the workspace "
                "when the request is about Desktop, Downloads, Documents, allowed drives such as R:/, or opening simple Windows apps."
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

    def _browser_help_text(self) -> str:
        return (
            "Browser commands:\n"
            "/browser workflows\n"
            "/browser task <instruction>\n"
            "/browser profiles\n"
            "/browser state\n"
            "/browser tabs\n"
            "/browser open <url>\n"
            "/browser switch <tab_id>\n"
            "/browser close <tab_id>\n"
            "/browser logs [limit]\n"
            "/browser downloads [limit]\n"
            "/browser screenshot\n"
            "/browser login <site_name> [profile_name]\n"
            "/browser watch <url> <condition>\n"
            "/browser watches\n"
            "/browser unwatch <watch_id>"
        )

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

    def _parse_browser_watch_arguments(self, value: str) -> tuple[str, str]:
        stripped = value.strip()
        if not stripped:
            return "", ""
        if "|" in stripped:
            left, right = stripped.split("|", maxsplit=1)
            return self._normalize_browser_watch_url(left), self._normalize_cli_text(right)
        parts = stripped.split(maxsplit=1)
        url = self._normalize_browser_watch_url(parts[0])
        condition = self._normalize_cli_text(parts[1]) if len(parts) > 1 else ""
        return url, condition

    def _normalize_browser_watch_url(self, value: str) -> str:
        candidate = self._normalize_cli_text(value)
        if not candidate:
            return ""
        parsed = urlparse(candidate)
        if parsed.scheme and parsed.netloc:
            return candidate
        if candidate.startswith("www.") or "." in candidate:
            return f"https://{candidate.lstrip('/')}"
        return ""

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
        patterns = (
            r"^(?:create|set|make)\s+(?:a\s+)?(?:cron\s+job|reminder)\s+to\s+remind me every\s+(?P<frequency>day|weekday|monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?\s+at\s+(?P<time>.+?)\s+to\s+(?P<message>.+)$",
            r"^remind me every\s+(?P<frequency>day|weekday|monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?\s+at\s+(?P<time>.+?)\s+to\s+(?P<message>.+)$",
            r"^every\s+(?P<frequency>day|weekday|monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?\s+at\s+(?P<time>.+?)\s+remind me to\s+(?P<message>.+)$",
        )
        for pattern in patterns:
            match = re.match(pattern, normalized, flags=re.IGNORECASE)
            if not match:
                continue
            schedule = self._build_schedule_from_frequency_and_time(
                str(match.group("frequency")),
                str(match.group("time")),
            )
            reminder_message = self._normalize_reminder_message(match.group("message"))
            if schedule and reminder_message:
                return schedule, reminder_message
        return None

    def _build_schedule_from_frequency_and_time(self, frequency: str, time_text: str) -> str | None:
        parsed_time = self._parse_reminder_time(time_text)
        if parsed_time is None:
            return None
        hour, minute = parsed_time
        day_map = {
            "day": "*",
            "weekday": "1-5",
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
            has_tool(name) for name in ("list_host_dir", "search_host_files", "exec_shell")
        )

    def _looks_like_list_folder_request(self, lowered: str) -> bool:
        return any(
            phrase in lowered
            for phrase in (
                "show me files",
                "show files",
                "list files",
                "list the files",
                "what is in",
                "what's in",
                "open folder",
                "show contents",
                "show the contents",
                "browse",
            )
        )

    def _match_known_host_folder(self, lowered: str) -> str | None:
        mapping = {
            "downloads": "Downloads",
            "desktop": "Desktop",
            "documents": "Documents",
            "pictures": "Pictures",
            "music": "Music",
            "videos": "Videos",
        }
        for token, folder in mapping.items():
            if token in lowered:
                return folder
        return None

    def _extract_host_search_term(self, lowered: str) -> str | None:
        patterns = (
            r"\b(?:search|find)(?:\s+for)?\s+(.+?)\s+(?:folder|file|directory)\b",
            r"\b(.+?)\s+(?:folder|file|directory)\s+(?:in|on)\s+(?:the\s+)?(?:r\s*:|r\s*drive|r\s*dive)\b",
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

    def _extract_host_search_root(self, lowered: str) -> str | None:
        if re.search(r"\br\s*:?\s*(?:drive|dive)?\b", lowered) or "r:\\" in lowered or "r:/" in lowered:
            return "R:/"
        return None

    def _extract_host_browse_folder_term(self, lowered: str) -> str | None:
        patterns = (
            r"\b(?:open|oepn|show|list|browse)\s+(?:the\s+)?(.+?)\s+(?:folder|directory)\b",
            r"\b(?:open|oepn|show|list|browse)\s+(?:the\s+)?folder\s+(.+)\b",
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
    ) -> tuple[str, str, str] | None:
        lowered = message.lower()
        filename, content = await self._parse_host_file_request_details(session_key, message)
        if not filename or not content:
            return None
        explicit_root = self._extract_host_search_root(lowered)
        target_dir = self._extract_explicit_host_directory(message)
        if target_dir is None:
            folder_reference = self._extract_named_folder_reference_for_write(lowered)
            if folder_reference is not None:
                target_dir = await self._resolve_host_directory_reference(
                    session_key,
                    folder_reference,
                    explicit_root,
                    metadata,
                )
            elif any(token in lowered for token in (" there", " here", "that folder", "this folder")):
                target_dir = await self._resolve_recent_host_directory(
                    session_key,
                    explicit_root,
                    metadata,
                )
        if target_dir is None:
            return None
        return target_dir, filename, content

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

    def _extract_named_folder_reference_for_write(self, lowered: str) -> str | None:
        patterns = (
            r"\b(?:in|inside|into|to)\s+(?:the\s+)?(.+?)\s+(?:folder|directory)\b",
            r"\b(?:save|create|make|write)\s+(?:a\s+)?file\s+(?:in|inside)\s+(?:the\s+)?(.+?)\s+(?:folder|directory)\b",
        )
        for pattern in patterns:
            match = re.search(pattern, lowered)
            if not match:
                continue
            term = self._clean_host_search_term(match.group(1))
            if term and term not in {"that", "this", "there", "here"}:
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
            r"\bwith\s+content\s+(.+)$",
            r"\bcontaining\s+(.+)$",
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
                    if resolved is not None:
                        return resolved
        return None

    async def _resolve_host_directory_reference(
        self,
        session_key: str,
        folder_reference: str,
        explicit_root: str | None,
        metadata: dict[str, Any],
    ) -> str | None:
        recent_path = await self._resolve_recent_host_directory_direct_path(session_key)
        if recent_path and self._compact_search_name(Path(recent_path).name) == self._compact_search_name(folder_reference):
            return recent_path
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
        folder_match = self._pick_folder_match_for_contents(folder_reference, result)
        if folder_match is not None:
            return str(folder_match.get("path", "")) or None
        matches = [match for match in result.get("matches", []) if match.get("is_dir")]
        if len(matches) == 1:
            return str(matches[0].get("path", "")) or None
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
        pending_login = state.get("pending_login") or {}
        pending_action = state.get("pending_protected_action") or {}
        pending_otp = state.get("pending_otp") or {}
        pending_captcha = state.get("pending_captcha") or {}
        status = "Browser idle" if not active_tab else "Browser active"
        lines = [
            status,
            f"Mode: {state.get('current_mode', 'headless')}",
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
            lines.append(f"URL: {self._redact_browser_url(str(active_tab.get('url', 'unknown')))}")
            lines.append(f"Tab id: {active_tab.get('tab_id', 'unknown')}")
            if active_tab.get("mode"):
                lines.append(f"Tab mode: {active_tab.get('mode')}")
        if pending_login:
            lines.append(
                f"Pending login: {pending_login.get('site_name', 'site')} "
                f"at {self._redact_browser_url(str(pending_login.get('target_url', active_tab.get('url', 'unknown'))))}"
            )
            lines.append('A visible browser window should be open on the host machine. Reply with "continue" after login.')
        if pending_action:
            lines.append(
                f"Pending protected action: {pending_action.get('action_type', 'action')} "
                f"on {self._redact_browser_text(str(pending_action.get('selector') or pending_action.get('target') or 'current page'))}"
            )
            lines.append('Reply with "confirm" or "cancel".')
        if pending_otp:
            lines.append(
                f"Pending OTP: {pending_otp.get('site_name', 'site')} "
                f"at {self._redact_browser_url(str(pending_otp.get('target_url', active_tab.get('url', 'unknown'))))}"
            )
            lines.append("Reply with the OTP digits to continue.")
        if pending_captcha:
            lines.append(
                f"Pending CAPTCHA: {pending_captcha.get('site_name', 'site')} "
                f"at {self._redact_browser_url(str(pending_captcha.get('target_url', active_tab.get('url', 'unknown'))))}"
            )
            screenshot_path = str(pending_captcha.get("screenshot_path", "")).strip()
            if screenshot_path:
                lines.append(f"CAPTCHA screenshot: {screenshot_path}")
            lines.append("Reply with the CAPTCHA answer to continue.")
        return "\n".join(lines)

    def _format_browser_tabs_response(self, result: dict[str, Any]) -> str:
        tabs = result.get("tabs", []) if isinstance(result, dict) else []
        current_tab_id = result.get("current_tab_id") if isinstance(result, dict) else None
        if not tabs:
            return "No browser tabs are open right now."
        lines = ["Open browser tabs:"]
        for tab in tabs[:10]:
            active = " [active]" if tab.get("tab_id") == current_tab_id else ""
            mode = f" [{tab.get('mode')}]" if tab.get("mode") else ""
            lines.append(
                f"- {tab.get('tab_id', 'unknown')}{active}{mode}: {tab.get('title', '(untitled)')} -> {self._redact_browser_url(str(tab.get('url', 'unknown')))}"
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
            message = self._redact_browser_text(str(entry.get("message", entry.get("url", ""))).strip()) or "(no message)"
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

    def _redact_browser_url(self, url: str) -> str:
        if not url:
            return url
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return re.sub(r";jsessionid=[^/?#]+", "", url, flags=re.IGNORECASE)
        path = re.sub(r";jsessionid=[^/?#]+", "", parsed.path or "", flags=re.IGNORECASE)
        return f"{parsed.scheme}://{parsed.netloc}{path}"

    def _redact_browser_text(self, text: str) -> str:
        if not text:
            return text
        return re.sub(
            r"https?://[^\s]+",
            lambda match: self._redact_browser_url(match.group(0)),
            text,
        )

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
        current_repo = self._current_github_repo_reference()
        repo_hint = self._extract_repo_name_hint(message)
        if current_repo is not None and repo_hint in {None, "", "this repo"}:
            return current_repo
        history = await self.session_manager.session_history(session_key, limit=20)
        if repo_hint:
            compact_hint = self._compact_repo_name(repo_hint)
            for item in reversed(history):
                content = str(item.get("content", ""))
                for owner, repo in re.findall(r"\b([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.-]+)\b", content):
                    if self._compact_repo_name(repo) == compact_hint or self._compact_repo_name(f"{owner}/{repo}") == compact_hint:
                        return owner, repo
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

    def _extract_repo_name_hint(self, text: str) -> str | None:
        patterns = (
            r"\bthe\s+(.+?)\s+repo\b",
            r"\babout\s+(.+?)\s+repo\b",
            r"\brepo\s+(.+)$",
        )
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if not match:
                continue
            candidate = match.group(1).strip().strip("\"'")
            if candidate and candidate.lower() not in {"this", "that"}:
                return candidate
        if re.search(r"\bthis repo\b", text, flags=re.IGNORECASE):
            return "this repo"
        return None

    def _compact_repo_name(self, value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", value.lower())

    def _current_github_repo_reference(self) -> tuple[str, str] | None:
        runtime = getattr(self.tool_registry, "browser_runtime", None)
        if runtime is None or not hasattr(runtime, "current_state"):
            return None
        try:
            state = runtime.current_state()
        except Exception:
            return None
        active_tab = state.get("active_tab") or {}
        url = str(active_tab.get("url", "") or "")
        parsed = urlparse(url)
        if "github.com" not in parsed.netloc.lower():
            return None
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 2:
            return None
        return parts[0], parts[1]

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
