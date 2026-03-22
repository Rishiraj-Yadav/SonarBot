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
    started_at: datetime

    async def handle_request(self, connection_id: str, request: RequestFrame) -> ResponseFrame:
        if request.method == "health":
            return ResponseFrame(id=request.id, ok=True, payload=self.health_payload())

        if request.method == "agent.send":
            params = AgentSendParams.model_validate(request.params)
            return await self.route_user_message(
                connection_id=connection_id,
                request_id=request.id,
                session_key=params.session_key,
                message=params.message,
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

        hook_event = await self.hook_runner.fire_event(
            "message:received",
            context={
                "session_key": session_key,
                "message": message,
                "metadata": metadata,
                "preview": message[:120],
                "sender_id": metadata.get("sender_id"),
                "channel": metadata.get("channel"),
                "logs_dir": str(self.config.logs_dir),
            },
        )

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
        command_name = parts[0].lower()
        arguments = parts[1] if len(parts) > 1 else ""

        if command_name in {"new", "reset"}:
            previous = await self.session_manager.load_or_create(session_key)
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
            hook_event = await self.hook_runner.fire_event(
                "command:stop",
                context={
                    "session_key": session_key,
                    "command": command_name,
                    "arguments": arguments,
                    "logs_dir": str(self.config.logs_dir),
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
            hook_event = await self.hook_runner.fire_event(
                "command:memory",
                context={
                    "session_key": session_key,
                    "command": command_name,
                    "arguments": arguments,
                    "logs_dir": str(self.config.logs_dir),
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
            hook_event = await self.hook_runner.fire_event(
                "command:status",
                context={
                    "session_key": session_key,
                    "command": command_name,
                    "arguments": arguments,
                    "logs_dir": str(self.config.logs_dir),
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
            hook_event = await self.hook_runner.fire_event(
                "command:skills",
                context={
                    "session_key": session_key,
                    "command": command_name,
                    "arguments": arguments,
                    "logs_dir": str(self.config.logs_dir),
                },
            )
            enabled_skills = self.skill_registry.list_enabled()
            response_lines = [
                f"- {skill.name}: {skill.description}" for skill in enabled_skills
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
            hook_event = await self.hook_runner.fire_event(
                "command:oauth-status",
                context={
                    "session_key": session_key,
                    "command": command_name,
                    "arguments": arguments,
                    "logs_dir": str(self.config.logs_dir),
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
                    metadata={"skill_command": skill.name},
                    system_suffix=f"## Active Skill\n{skill_prompt}",
                )
            )
            return ResponseFrame(id=request_id, ok=True, payload={"queued": True, "session_key": session_key, "skill": skill.name})

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

    async def _handle_tool_shortcut(
        self,
        request_id: str,
        session_key: str,
        message: str,
        metadata: dict[str, Any],
    ) -> ResponseFrame | None:
        lowered = message.lower().strip()
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
        if not hints:
            return existing
        if existing:
            return f"{existing}\n\n## Intent Hint\n" + "\n".join(hints)
        return "## Intent Hint\n" + "\n".join(hints)

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
