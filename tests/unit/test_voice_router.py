from __future__ import annotations

from datetime import datetime, timezone

import pytest

from assistant.gateway.router import GatewayRouter


class DummyAgentLoop:
    def __init__(self) -> None:
        self.enqueued = []
        self.queue = type("Queue", (), {"pending_count": lambda self: 0})()

    async def enqueue(self, request) -> None:
        self.enqueued.append(request)

    def status(self) -> dict[str, object]:
        return {"running": False, "pending": 0, "current_session_key": None}


class DummyConnectionManager:
    def get_connection(self, _connection_id: str):
        return None

    def active_count(self) -> int:
        return 0

    def active_channels(self) -> list[str]:
        return []


class DummySessionManager:
    async def load_or_create(self, _session_key: str):
        return type("Session", (), {"session_key": "webchat_main"})()

    async def append_message(self, _session, _message):
        return None

    async def session_history(self, _session_key: str, limit: int = 20):
        return []

    def active_count(self) -> int:
        return 0


class DummyHookRunner:
    async def fire_event(self, *_args, **_kwargs):
        return type("HookEvent", (), {"messages": []})()


class DummySkillRegistry:
    def active_count(self) -> int:
        return 0

    def find_user_invocable(self, _name: str):
        return None

    def list_enabled(self):
        return []

    def match_natural_language(self, _message: str):
        return []

    def load_skill_prompt(self, _name: str) -> str:
        return ""


class DummyPresenceRegistry:
    def snapshot(self) -> list[object]:
        return []


class DummyOAuthFlowManager:
    async def start_oauth_flow(self, provider: str):
        return {"provider": provider, "authorize_url": "https://example.com"}

    @property
    def token_manager(self):
        class _TokenManager:
            async def list_connected(self):
                return []

        return _TokenManager()


class DummyUserProfiles:
    async def resolve_user_id(self, *_args, **_kwargs) -> str:
        return "default"


class DummyToolRegistry:
    def __init__(self) -> None:
        self.calls = []
        self.browser_runtime = type("BrowserRuntimeStub", (), {"current_state": lambda self: {}})()

    def has(self, tool_name: str) -> bool:
        return tool_name in {"apps_open"}

    async def dispatch(self, tool_name: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append((tool_name, payload))
        if tool_name == "apps_open":
            return {"alias": payload["alias"], "path": "C:/Program Files/Google/Chrome/Application/chrome.exe"}
        raise AssertionError(f"Unexpected tool call: {tool_name}")


class DummyAutomationEngine:
    async def list_report_jobs(self, user_id: str | None = None):
        return [{"job_id": "job-1", "topic": "AI trends", "schedule": "0 18 * * *", "paused": False}]


class StubNlp:
    async def rewrite_canonical(self, message: str) -> str:
        return message

    async def classify(self, _message: str) -> dict[str, object]:
        return {
            "intent": "open_app",
            "target": "chrome",
            "action": "open",
            "time_expr": "",
            "corrected": "open chrome",
            "confidence": 0.94,
            "raw_slots": {},
        }

    async def fuzzy_match_app(self, user_input: str, known_aliases: set[str]) -> str | None:
        return user_input if user_input in known_aliases else None


def build_router(app_config, *, agent_loop: DummyAgentLoop | None = None, tool_registry: DummyToolRegistry | None = None):
    router = GatewayRouter(
        config=app_config,
        agent_loop=agent_loop or DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=object(),
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry or DummyToolRegistry(),
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )
    router._nlp = StubNlp()
    return router


@pytest.mark.asyncio
async def test_voice_alias_can_reach_report_list_without_typed_slash(app_config) -> None:
    router = build_router(app_config)

    response = await router.route_user_message(
        connection_id="voice-1",
        request_id="req-1",
        session_key="webchat_main",
        message="list report jobs",
        metadata={"input_mode": "voice", "voice_confidence": 0.95},
    )

    assert response.ok is True
    assert "Report jobs" in str(response.payload["command_response"])


@pytest.mark.asyncio
async def test_low_confidence_voice_skips_inline_shortcuts_and_queues_clarification(app_config) -> None:
    app_config.desktop_apps.enabled = True
    agent_loop = DummyAgentLoop()
    tool_registry = DummyToolRegistry()
    router = build_router(app_config, agent_loop=agent_loop, tool_registry=tool_registry)

    response = await router.route_user_message(
        connection_id="voice-2",
        request_id="req-2",
        session_key="webchat_main",
        message="open chrome",
        metadata={"input_mode": "voice", "voice_confidence": 0.31},
    )

    assert response.ok is True
    assert response.payload["queued"] is True
    assert tool_registry.calls == []
    assert len(agent_loop.enqueued) == 1
    assert "clarifying question" in str(agent_loop.enqueued[0].system_suffix).lower()
