"""Route validated protocol requests into the agent runtime."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
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
