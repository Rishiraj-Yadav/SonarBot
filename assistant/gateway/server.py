"""FastAPI server and WebSocket gateway."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

from assistant.agent.loop import AgentLoop
from assistant.agent.queue import QueueMode
from assistant.agent.session_manager import SessionManager
from assistant.agent.system_prompt import SystemPromptBuilder
from assistant.automation import (
    AutomationEngine,
    AutomationScheduler,
    AutomationStore,
    DesktopAutomationWatcher,
    HeartbeatService,
    NotificationDispatcher,
    StandingOrdersManager,
    render_webhook_message,
    verify_webhook_signature,
)
from assistant.channels.base import ChannelMessage
from assistant.channels.telegram.adapter import TelegramChannel
from assistant.channels.webchat import get_webchat_device_id
from assistant.config import AppConfig, load_config
from assistant.context_engine import ContextEngine
from assistant.gateway.auth import authenticate_token
from assistant.gateway.connection_manager import ConnectionManager
from assistant.gateway.device_registry import DeviceRegistry
from assistant.gateway.protocol import ConnectFrame, HelloOkFrame, RequestFrame, ResponseFrame
from assistant.gateway.router import GatewayRouter
from assistant.hooks.runner import HookRunner
from assistant.memory import MemoryAutoCaptureRunner, MemoryManager
from assistant.models import get_provider
from assistant.multi_agent import ACPClient, PresenceRegistry, SubAgentManager
from assistant.oauth import OAuthFlowManager, OAuthTokenManager
from assistant.sandbox import SandboxRuntime
from assistant.skills.registry import SkillRegistry
from assistant.skills.watcher import SkillWatcher
from assistant.system_access import SystemAccessManager
from assistant.tools.agent_send_tool import build_agent_send_tool
from assistant.tools import create_default_tool_registry
from assistant.utils import configure_logging, get_logger
from assistant.utils.user_facing_errors import sanitize_error_text
from assistant.users import UserProfileStore


@dataclass(slots=True)
class GatewayServices:
    config: AppConfig
    connection_manager: ConnectionManager
    device_registry: DeviceRegistry
    session_manager: SessionManager
    memory_manager: MemoryManager
    prompt_builder: SystemPromptBuilder
    tool_registry: Any
    model_provider: Any
    agent_loop: AgentLoop
    router: GatewayRouter
    started_at: datetime
    channels: list[Any]
    skill_registry: SkillRegistry
    skill_watcher: SkillWatcher
    hook_runner: HookRunner
    standing_orders: StandingOrdersManager
    automation_scheduler: AutomationScheduler | None
    heartbeat_service: HeartbeatService
    oauth_token_manager: OAuthTokenManager
    oauth_flow_manager: OAuthFlowManager
    presence_registry: PresenceRegistry
    sub_agent_manager: SubAgentManager
    acp_client: ACPClient
    sandbox_runtime: SandboxRuntime
    logger: Any
    user_profiles: UserProfileStore
    automation_store: AutomationStore
    automation_engine: AutomationEngine
    desktop_watcher: DesktopAutomationWatcher
    context_engine: ContextEngine
    system_access_manager: SystemAccessManager
    browser_runtime: Any


def create_app(config: AppConfig | None = None, model_provider=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runtime_config = config or load_config()
        runtime_config.ensure_runtime_dirs()
        configure_logging(runtime_config.logs_dir / "gateway.log")
        logger = get_logger("gateway")
        connection_manager = ConnectionManager(rate_limit_per_minute=runtime_config.gateway.rate_limit_per_minute)
        device_registry = DeviceRegistry(runtime_config)
        await device_registry.initialize()
        session_manager = SessionManager(runtime_config)
        memory_manager = MemoryManager(runtime_config)
        user_profiles = UserProfileStore(runtime_config)
        await user_profiles.initialize()
        system_access_manager = SystemAccessManager(
            runtime_config,
            connection_manager=connection_manager,
            user_profiles=user_profiles,
        )
        await system_access_manager.initialize()
        skill_registry = SkillRegistry(runtime_config)
        skill_registry.start()
        skill_watcher = SkillWatcher(skill_registry)
        hook_runner = HookRunner(runtime_config)
        hook_runner.load_hooks()
        oauth_token_manager = OAuthTokenManager(runtime_config)
        await oauth_token_manager.initialize()
        oauth_flow_manager = OAuthFlowManager(runtime_config, oauth_token_manager)
        sandbox_runtime = SandboxRuntime(runtime_config)
        acp_client = ACPClient(runtime_config)
        prompt_builder = SystemPromptBuilder(
            runtime_config.agent.workspace_dir,
            memory_manager=memory_manager,
            skill_registry=skill_registry,
        )
        provider = model_provider or get_provider(runtime_config)
        tool_registry = create_default_tool_registry(
            runtime_config,
            memory_manager=memory_manager,
            model_provider=provider,
            oauth_flow_manager=oauth_flow_manager,
            oauth_token_manager=oauth_token_manager,
            sandbox_runtime=sandbox_runtime,
            acp_client=acp_client,
            system_access_manager=system_access_manager,
            browser_event_emitter=lambda user_id, event_name, payload: connection_manager.send_user_event(
                user_id,
                event_name,
                payload,
                channel_name="webchat",
            ),
            browser_viewer_checker=lambda user_id: bool(connection_manager.active_user_connections(user_id, "webchat")),
        )
        browser_runtime = getattr(tool_registry, "browser_runtime", None)
        memory_capture_runner = MemoryAutoCaptureRunner(runtime_config, provider, tool_registry)
        presence_registry = PresenceRegistry()
        agent_loop = AgentLoop(
            config=runtime_config,
            model_provider=provider,
            tool_registry=tool_registry,
            session_manager=session_manager,
            system_prompt_builder=prompt_builder,
            event_emitter=connection_manager.send_event,
            typing_emitter=connection_manager.send_typing,
            memory_capture_runner=memory_capture_runner,
        )
        presence_registry.register("main", "main", capabilities=tool_registry.names())
        sub_agent_manager = SubAgentManager(
            config=runtime_config,
            model_provider=provider,
            session_manager=session_manager,
            base_tool_registry=tool_registry,
            presence_registry=presence_registry,
        )
        tool_registry.register(build_agent_send_tool(sub_agent_manager))
        started_at = datetime.now(timezone.utc)
        router = GatewayRouter(
            config=runtime_config,
            agent_loop=agent_loop,
            connection_manager=connection_manager,
            session_manager=session_manager,
            memory_manager=memory_manager,
            skill_registry=skill_registry,
            hook_runner=hook_runner,
            presence_registry=presence_registry,
            oauth_flow_manager=oauth_flow_manager,
            tool_registry=tool_registry,
            automation_engine=None,
            system_access_manager=system_access_manager,
            user_profiles=user_profiles,
            started_at=started_at,
        )
        channels = _build_channels(runtime_config, connection_manager, router, user_profiles, system_access_manager)
        standing_orders = StandingOrdersManager(runtime_config.agent.workspace_dir)
        automation_store = AutomationStore(runtime_config)
        await automation_store.initialize()
        notification_dispatcher = NotificationDispatcher(runtime_config, automation_store, user_profiles, connection_manager)
        automation_engine = AutomationEngine(
            runtime_config,
            agent_loop,
            session_manager,
            standing_orders,
            user_profiles,
            automation_store,
            notification_dispatcher,
            system_access_manager=system_access_manager,
        )
        await automation_engine.initialize()
        desktop_watcher = DesktopAutomationWatcher(runtime_config, automation_engine)
        context_engine = ContextEngine(
            runtime_config,
            model_provider=provider,
            memory_manager=memory_manager,
            session_manager=session_manager,
            oauth_token_manager=oauth_token_manager,
            automation_store=automation_store,
            notification_dispatcher=notification_dispatcher,
            user_profiles=user_profiles,
        )
        router.automation_engine = automation_engine
        automation_scheduler = AutomationScheduler(runtime_config, automation_engine)
        automation_engine.set_scheduler(automation_scheduler)
        heartbeat_service = HeartbeatService(runtime_config, agent_loop, automation_engine)
        app.state.services = GatewayServices(
            config=runtime_config,
            connection_manager=connection_manager,
            device_registry=device_registry,
            session_manager=session_manager,
            memory_manager=memory_manager,
            prompt_builder=prompt_builder,
            tool_registry=tool_registry,
            model_provider=provider,
            agent_loop=agent_loop,
            router=router,
            started_at=started_at,
            channels=channels,
            skill_registry=skill_registry,
            skill_watcher=skill_watcher,
            hook_runner=hook_runner,
            standing_orders=standing_orders,
            automation_scheduler=automation_scheduler,
            heartbeat_service=heartbeat_service,
            oauth_token_manager=oauth_token_manager,
            oauth_flow_manager=oauth_flow_manager,
            presence_registry=presence_registry,
            sub_agent_manager=sub_agent_manager,
            acp_client=acp_client,
            sandbox_runtime=sandbox_runtime,
            logger=logger,
            user_profiles=user_profiles,
            automation_store=automation_store,
            automation_engine=automation_engine,
            desktop_watcher=desktop_watcher,
            context_engine=context_engine,
            system_access_manager=system_access_manager,
            browser_runtime=browser_runtime,
        )
        await session_manager.start_pruning_task()
        await prompt_builder.start()
        await skill_watcher.start()
        await agent_loop.start()
        if automation_scheduler is not None:
            await automation_scheduler.start()
        await desktop_watcher.start()
        await heartbeat_service.start()
        for channel in channels:
            connection_manager.register_channel(channel)
            await channel.start()
        await context_engine.start()
        await _run_startup_hooks(app.state.services)
        try:
            yield
        finally:
            await agent_loop.wait_for_idle(timeout=30)
            await connection_manager.close_all()
            for channel in channels:
                await channel.stop()
            await heartbeat_service.stop()
            await desktop_watcher.stop()
            if automation_scheduler is not None:
                await automation_scheduler.stop()
            await context_engine.stop()
            await agent_loop.stop()
            await tool_registry.close()
            await prompt_builder.stop()
            await skill_watcher.stop()
            await session_manager.stop_pruning_task()

    app = FastAPI(title="SonarBot Gateway", lifespan=lifespan)

    @app.get("/__health")
    async def health() -> dict[str, object]:
        services: GatewayServices = app.state.services
        return services.router.health_payload()

    @app.get("/oauth/callback/{provider_name}")
    async def oauth_callback(provider_name: str, code: str = "", state: str = "", error: str = ""):
        services: GatewayServices = app.state.services
        pretty_provider = {"github": "GitHub", "google": "Google"}.get(provider_name.lower(), provider_name.title())
        if error:
            return HTMLResponse(
                f"<html><body><h2>OAuth failed for {pretty_provider}</h2><p>{error}</p></body></html>",
                status_code=400,
            )
        if not code or not state:
            return JSONResponse({"ok": False, "error": "Missing code or state."}, status_code=400)
        try:
            saved = await services.oauth_flow_manager.handle_callback(provider_name, code, state)
        except Exception as exc:
            return HTMLResponse(
                f"<html><body><h2>OAuth callback error</h2><p>{exc}</p></body></html>",
                status_code=400,
            )
        return HTMLResponse(
            (
                "<html><body>"
                f"<h2>{pretty_provider} connected</h2>"
                "<p>You can close this tab and return to SonarBot.</p>"
                f"<pre>{json.dumps({'provider': saved.get('provider'), 'user_id': saved.get('user_id')}, indent=2)}</pre>"
                "</body></html>"
            ),
            status_code=200,
        )

    @app.get("/webchat/history")
    async def webchat_history(session_key: str = "main", limit: int = 50) -> dict[str, Any]:
        services: GatewayServices = app.state.services
        resolved = _resolve_webchat_session_key(session_key)
        history = await services.session_manager.session_history(resolved, limit=min(max(limit, 1), 200))
        return {"session_key": resolved, "messages": _format_webchat_history(history)}

    @app.get("/api/dashboard")
    async def dashboard(session_key: str = "webchat_main") -> dict[str, Any]:
        services: GatewayServices = app.state.services
        return await services.router.dashboard_payload(session_key)

    @app.get("/api/notifications")
    async def notifications(request: Request, limit: int = 20) -> dict[str, Any]:
        services: GatewayServices = app.state.services
        device_id = request.cookies.get("sonarbot_webchat") or request.query_params.get("device_id") or "webchat-default"
        user_id = await services.user_profiles.resolve_user_id("webchat", device_id, {"channel": "webchat"})
        notifications = await services.automation_engine.list_notifications(user_id)
        return {"notifications": notifications[: max(1, min(limit, 100))]}

    @app.get("/api/automation/runs")
    async def automation_runs(request: Request, limit: int = 20) -> dict[str, Any]:
        services: GatewayServices = app.state.services
        device_id = request.cookies.get("sonarbot_webchat") or request.query_params.get("device_id") or "webchat-default"
        user_id = await services.user_profiles.resolve_user_id("webchat", device_id, {"channel": "webchat"})
        runs = await services.automation_engine.list_runs(user_id)
        return {"runs": runs[: max(1, min(limit, 100))]}

    @app.get("/api/automation/rules")
    async def automation_rules(request: Request) -> dict[str, Any]:
        services: GatewayServices = app.state.services
        device_id = request.cookies.get("sonarbot_webchat") or request.query_params.get("device_id") or "webchat-default"
        user_id = await services.user_profiles.resolve_user_id("webchat", device_id, {"channel": "webchat"})
        return {"rules": await services.automation_engine.list_rules(user_id)}

    @app.post("/api/automation/rules/{name}/pause")
    async def pause_automation_rule(name: str, request: Request) -> dict[str, Any]:
        services: GatewayServices = app.state.services
        device_id = request.cookies.get("sonarbot_webchat") or request.query_params.get("device_id") or "webchat-default"
        user_id = await services.user_profiles.resolve_user_id("webchat", device_id, {"channel": "webchat"})
        await services.automation_engine.pause_rule(user_id, name)
        return {"ok": True, "name": name, "paused": True}

    @app.post("/api/automation/rules/{name}/resume")
    async def resume_automation_rule(name: str, request: Request) -> dict[str, Any]:
        services: GatewayServices = app.state.services
        device_id = request.cookies.get("sonarbot_webchat") or request.query_params.get("device_id") or "webchat-default"
        user_id = await services.user_profiles.resolve_user_id("webchat", device_id, {"channel": "webchat"})
        await services.automation_engine.resume_rule(user_id, name)
        return {"ok": True, "name": name, "paused": False}

    @app.delete("/api/automation/rules/{name}")
    async def delete_automation_rule(name: str, request: Request) -> dict[str, Any]:
        services: GatewayServices = app.state.services
        device_id = request.cookies.get("sonarbot_webchat") or request.query_params.get("device_id") or "webchat-default"
        user_id = await services.user_profiles.resolve_user_id("webchat", device_id, {"channel": "webchat"})
        deleted = await services.automation_engine.delete_rule(user_id, name)
        return {"ok": deleted, "name": name}

    @app.post("/api/automation/runs/{run_id}/replay")
    async def replay_automation_run(run_id: str) -> dict[str, Any]:
        services: GatewayServices = app.state.services
        return {"ok": True, "result": await services.automation_engine.replay_run(run_id)}

    @app.get("/api/approvals")
    async def approvals(request: Request) -> dict[str, Any]:
        services: GatewayServices = app.state.services
        device_id = request.cookies.get("sonarbot_webchat") or request.query_params.get("device_id") or "webchat-default"
        user_id = await services.user_profiles.resolve_user_id("webchat", device_id, {"channel": "webchat"})
        return {"approvals": await services.automation_engine.list_approvals(user_id)}

    @app.post("/api/approvals/{approval_id}")
    async def decide_approval(approval_id: str, request: Request) -> dict[str, Any]:
        services: GatewayServices = app.state.services
        payload = await request.json()
        decision = str(payload.get("decision", "approved"))
        await services.automation_engine.decide_approval(approval_id, decision)
        return {"ok": True, "approval_id": approval_id, "decision": decision}

    @app.get("/api/skills")
    async def list_skills() -> dict[str, Any]:
        services: GatewayServices = app.state.services
        skills = [
            {
                "name": skill.name,
                "description": skill.description,
                "eligible": skill.eligible,
                "enabled": skill.enabled,
                "user_invocable": skill.user_invocable,
                "natural_language_enabled": skill.natural_language_enabled,
                "aliases": skill.aliases,
                "path": str(skill.path),
            }
            for skill in services.skill_registry.list_all()
        ]
        return {"skills": skills}

    @app.post("/api/skills/{name}/toggle")
    async def toggle_skill(name: str) -> dict[str, Any]:
        services: GatewayServices = app.state.services
        skill = services.skill_registry.toggle(name)
        return {"name": skill.name, "enabled": skill.enabled}

    @app.get("/api/settings")
    async def settings() -> dict[str, Any]:
        services: GatewayServices = app.state.services
        return {
            "gateway": {
                "host": services.config.gateway.host,
                "port": services.config.gateway.port,
            },
            "agent": {
                "workspace_dir": str(services.config.agent.workspace_dir),
                "model": services.config.agent.model,
                "max_tokens": services.config.agent.max_tokens,
                "context_window": services.config.agent.context_window,
            },
            "channels": services.config.channels.enabled,
            "automation": {
                "heartbeat_interval_minutes": services.config.automation.heartbeat_interval_minutes,
                "cron_jobs": [job.model_dump() for job in services.config.automation.cron_jobs],
            },
            "context_engine": {
                "enabled": services.config.context_engine.enabled,
                "interval_minutes": services.config.context_engine.interval_minutes,
            },
        }

    @app.get("/api/context-engine/state")
    async def context_engine_state(request: Request) -> dict[str, Any]:
        services: GatewayServices = app.state.services
        device_id = request.cookies.get("sonarbot_webchat") or request.query_params.get("device_id") or "webchat-default"
        user_id = await services.user_profiles.resolve_user_id("webchat", device_id, {"channel": "webchat"})
        return {
            "engine": services.context_engine.status(),
            "snapshot": await services.context_engine.latest_snapshot(user_id),
        }

    @app.post("/api/context-engine/run")
    async def context_engine_run() -> dict[str, Any]:
        services: GatewayServices = app.state.services
        return {"ok": True, "result": await services.context_engine.run_once()}

    @app.get("/api/system-access/approvals")
    async def system_access_approvals(request: Request, limit: int = 20) -> dict[str, Any]:
        services: GatewayServices = app.state.services
        device_id = request.cookies.get("sonarbot_webchat") or request.query_params.get("device_id") or "webchat-default"
        user_id = await services.user_profiles.resolve_user_id("webchat", device_id, {"channel": "webchat"})
        approvals = await services.system_access_manager.list_approvals(user_id, limit=max(1, min(limit, 100)))
        return {"approvals": approvals}

    @app.post("/api/system-access/approvals/{approval_id}")
    async def decide_system_access_approval(approval_id: str, request: Request) -> dict[str, Any]:
        services: GatewayServices = app.state.services
        payload = await request.json()
        decision = str(payload.get("decision", "approved"))
        approval = await services.system_access_manager.decide_approval(approval_id, decision)
        return {"ok": True, "approval": approval}

    @app.get("/api/system-access/audit")
    async def system_access_audit(session_id: str | None = None, today: bool = False, limit: int = 50) -> dict[str, Any]:
        services: GatewayServices = app.state.services
        rows = await services.system_access_manager.list_audit(
            session_id=session_id,
            today_only=today,
            limit=max(1, min(limit, 200)),
        )
        return {"entries": rows}

    @app.post("/api/system-access/audit/{backup_id}/restore")
    async def restore_system_access_backup(backup_id: str, request: Request) -> dict[str, Any]:
        services: GatewayServices = app.state.services
        device_id = request.cookies.get("sonarbot_webchat") or request.query_params.get("device_id") or "webchat-default"
        user_id = await services.user_profiles.resolve_user_id("webchat", device_id, {"channel": "webchat"})
        result = await services.system_access_manager.restore_backup(backup_id, user_id=user_id)
        return {"ok": True, "result": result}

    @app.get("/api/browser/state")
    async def browser_state() -> dict[str, Any]:
        services: GatewayServices = app.state.services
        runtime = services.browser_runtime
        return {"state": runtime.current_state() if runtime is not None else {"active": False, "tabs": []}}

    @app.get("/api/browser/tabs")
    async def browser_tabs() -> dict[str, Any]:
        services: GatewayServices = app.state.services
        runtime = services.browser_runtime
        return {"tabs": runtime.list_tabs() if runtime is not None else []}

    @app.get("/api/browser/logs")
    async def browser_logs(limit: int = 50) -> dict[str, Any]:
        services: GatewayServices = app.state.services
        runtime = services.browser_runtime
        return {"logs": runtime.list_logs(limit=limit) if runtime is not None else []}

    @app.get("/api/browser/downloads")
    async def browser_downloads(limit: int = 50) -> dict[str, Any]:
        services: GatewayServices = app.state.services
        runtime = services.browser_runtime
        return {"downloads": runtime.list_downloads(limit=limit) if runtime is not None else []}

    @app.get("/api/browser/profiles")
    async def browser_profiles() -> dict[str, Any]:
        services: GatewayServices = app.state.services
        runtime = services.browser_runtime
        return {"profiles": runtime.list_sessions() if runtime is not None else []}

    @app.post("/webhooks/{name}")
    async def receive_webhook(name: str, request: Request) -> dict[str, Any]:
        services: GatewayServices = app.state.services
        webhook = services.config.automation.webhooks.get(name)
        if webhook is None:
            return {"ok": False, "error": "Unknown webhook."}
        body = await request.body()
        if not verify_webhook_signature(webhook.secret, body, request.headers.get("X-Signature")):
            return {"ok": False, "error": "Invalid signature."}
        payload = json.loads(body.decode("utf-8"))
        message = render_webhook_message(webhook.message_template, payload)
        result = await services.automation_engine.handle_webhook(name, {**payload, "message": message}, message)
        return {"ok": True, "result": result}

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        services = app.state.services
        connection = None
        try:
            raw_connect = await websocket.receive_text()
            connect_frame = ConnectFrame.model_validate(json.loads(raw_connect))
            if not authenticate_token(connect_frame.auth.token, services.config.gateway.token):
                await websocket.close(code=1008, reason="Invalid gateway token.")
                return

            connection = await services.connection_manager.connect(websocket, connect_frame.device_id)
            user_id = await services.user_profiles.resolve_user_id(
                "cli",
                connect_frame.device_id,
                {"channel": "cli", "device_id": connect_frame.device_id},
            )
            services.connection_manager.bind_connection(connection.connection_id, user_id=user_id, channel_name="cli")
            await services.device_registry.seen(connect_frame.device_id)
            await websocket.send_text(HelloOkFrame().model_dump_json())

            while True:
                raw_request = await websocket.receive_text()
                request_id = "rate-limit"
                try:
                    raw_payload = json.loads(raw_request)
                    if isinstance(raw_payload, dict):
                        request_id = str(raw_payload.get("id", request_id))
                except Exception:
                    pass
                if not services.connection_manager.allow_request(connection.connection_id):
                    response = ResponseFrame(id=request_id, ok=False, error="Rate limit exceeded. Try again in a minute.")
                    await services.connection_manager.send_response(connection.connection_id, response)
                    continue
                response = await _process_websocket_request(
                    services,
                    connection.connection_id,
                    raw_request,
                )
                if response is not None:
                    if not response.ok:
                        response.error = sanitize_error_text(response.error or "Request rejected.")
                    await services.connection_manager.send_response(connection.connection_id, response)
                    await _emit_inline_command_response(services, connection.connection_id, response)
        except WebSocketDisconnect:
            pass
        finally:
            if connection is not None:
                await services.connection_manager.disconnect(connection.connection_id)
            else:
                await services.connection_manager.disconnect_websocket(websocket)

    @app.websocket("/webchat/ws")
    async def webchat_websocket(websocket: WebSocket) -> None:
        await websocket.accept()
        services = app.state.services
        device_id = get_webchat_device_id(websocket)
        connection = await services.connection_manager.connect(websocket, device_id)
        user_id = await services.user_profiles.resolve_user_id(
            "webchat",
            device_id,
            {"channel": "webchat", "device_id": device_id},
        )
        services.connection_manager.bind_connection(connection.connection_id, user_id=user_id, channel_name="webchat")
        await services.device_registry.seen(device_id)
        try:
            while True:
                raw_text = await websocket.receive_text()
                request_id = "rate-limit"
                payload = json.loads(raw_text)
                if isinstance(payload, dict):
                    request_id = str(payload.get("id", request_id))
                if not services.connection_manager.allow_request(connection.connection_id):
                    response = ResponseFrame(id=request_id, ok=False, error="Rate limit exceeded. Try again in a minute.")
                    await services.connection_manager.send_response(connection.connection_id, response)
                    continue
                if payload.get("type") == "connect":
                    await websocket.send_text(HelloOkFrame().model_dump_json())
                    continue
                if payload.get("type") == "req" and "params" in payload and "session_key" not in payload["params"]:
                    payload["params"]["session_key"] = "webchat_main"
                try:
                    request = RequestFrame.model_validate(payload)
                except Exception as exc:
                    response = ResponseFrame(id=payload.get("id", "unknown"), ok=False, error=f"Malformed frame: {exc}")
                    await services.connection_manager.send_response(connection.connection_id, response)
                    continue
                response = await services.router.handle_request(connection.connection_id, request)
                if not response.ok:
                    response.error = sanitize_error_text(response.error or "Request rejected.")
                await services.connection_manager.send_response(connection.connection_id, response)
                await _emit_inline_command_response(services, connection.connection_id, response)
        except WebSocketDisconnect:
            pass
        finally:
            await services.connection_manager.disconnect(connection.connection_id)

    return app


async def _process_websocket_request(
    services: GatewayServices,
    connection_id: str,
    raw_request: str,
) -> ResponseFrame | None:
    try:
        payload = json.loads(raw_request)
        request = RequestFrame.model_validate(payload)
    except Exception as exc:
        request_id = payload.get("id", "unknown") if isinstance(payload, dict) else "unknown"
        return ResponseFrame(id=request_id, ok=False, error=f"Malformed frame: {exc}")
    return await services.router.handle_request(connection_id, request)


def _build_channels(
    config: AppConfig,
    connection_manager: ConnectionManager,
    router: GatewayRouter,
    user_profiles: UserProfileStore,
    system_access_manager: SystemAccessManager,
) -> list[Any]:
    async def handle_channel_message(message: ChannelMessage) -> str:
        user_id = await user_profiles.resolve_user_id(
            message.channel,
            message.sender_id,
            {
                "channel": message.channel,
                "chat_id": message.metadata.get("chat_id", message.sender_id),
            },
        )
        route_id = connection_manager.register_channel_route(
            channel_name=message.channel,
            sender_id=message.sender_id,
            recipient_id=message.metadata.get("chat_id", message.sender_id),
            user_id=user_id,
            metadata={"media_type": message.media_type, "media_path": message.media_path},
        )
        session_key = f"{message.channel}:{message.sender_id}"
        content = _channel_message_to_text(message)
        response = await router.route_user_message(
            connection_id=route_id,
            request_id=uuid4().hex,
            session_key=session_key,
            message=content,
            metadata={
                "channel": message.channel,
                "sender_id": message.sender_id,
                "media_type": message.media_type,
                "media_path": message.media_path,
                "user_id": user_id,
            },
        )
        if not response.ok:
            await connection_manager.send_event(
                route_id,
                "agent.chunk",
                {"text": sanitize_error_text(response.error or "Request rejected.")},
            )
            await connection_manager.send_event(route_id, "agent.done", {"session_key": session_key})
        else:
            payload = response.payload or {}
            command_text = payload.get("command_response")
            if command_text:
                await connection_manager.send_event(route_id, "agent.chunk", {"text": str(command_text)})
                await connection_manager.send_event(
                    route_id,
                    "agent.done",
                    {"session_key": str(payload.get("session_key", session_key))},
                )
        return route_id

    channels: list[Any] = []
    if "telegram" in config.channels.enabled:
        channels.append(
            TelegramChannel(
                config=config,
                inbound_handler=handle_channel_message,
                host_approval_handler=system_access_manager.decide_approval,
            )
        )
    return channels


def _channel_message_to_text(message: ChannelMessage) -> str:
    content = message.text.strip()
    if message.media_type and message.media_path:
        attachment_line = f"[Attached {message.media_type}: {message.media_path}]"
        content = f"{content}\n\n{attachment_line}".strip()
    return content or "(empty message)"


def _resolve_webchat_session_key(session_key: str) -> str:
    normalized = session_key.strip() or "main"
    return normalized if normalized.startswith("webchat_") else f"webchat_{normalized}"


async def _run_startup_hooks(services: GatewayServices) -> None:
    event = await services.hook_runner.fire_event(
        "gateway:startup",
        context={
            "session_key": "main",
            "workspace_dir": str(services.config.agent.workspace_dir),
        },
    )
    for message in event.messages:
        text = message.get("text") or message.get("content") or message.get("message")
        if not text:
            continue
        await services.router.route_user_message(
            connection_id="",
            request_id=f"startup-{uuid4().hex}",
            session_key=str(message.get("session_key", "main")),
            message=str(text),
            metadata={"source": "startup-hook"},
            mode=QueueMode.FOLLOWUP,
            silent=bool(message.get("silent", True)),
        )


async def _emit_inline_command_response(
    services: GatewayServices,
    connection_id: str,
    response: ResponseFrame,
) -> None:
    payload = response.payload or {}
    command_text = payload.get("command_response")
    if not command_text:
        return
    session_key = str(payload.get("session_key", "main"))
    await services.connection_manager.send_event(connection_id, "agent.chunk", {"text": str(command_text)})
    await services.connection_manager.send_event(connection_id, "agent.done", {"session_key": session_key})


def _format_webchat_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    formatted: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role", "")).strip().lower()
        if role not in {"user", "assistant"}:
            continue
        content = str(message.get("content", "")).strip()
        if not content:
            continue
        formatted.append(
            {
                "id": str(message.get("id", uuid4().hex)),
                "role": role,
                "content": content,
                "created_at": message.get("created_at"),
            }
        )
    return formatted
