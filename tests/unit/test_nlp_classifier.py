from __future__ import annotations

from datetime import datetime, timezone

import pytest

from assistant.agent.queue import QueueMode
from assistant.gateway.nlp_classifier import IntentClassifier
from assistant.gateway.router import GatewayRouter


class DummyAgentLoop:
    def __init__(self) -> None:
        self.enqueued = []
        self.queue = type("Queue", (), {"pending_count": lambda self: 0})()

    async def enqueue(self, request) -> None:
        self.enqueued.append(request)


class DummyConnectionManager:
    def active_count(self) -> int:
        return 0

    def active_channels(self) -> list[str]:
        return []

    def get_connection(self, _connection_id: str):
        return None


class DummySessionManager:
    async def load_or_create(self, _session_key: str):
        return type("Session", (), {"session_key": "webchat_main"})()

    async def append_message(self, _session, _message):
        return None

    async def session_history(self, _session_key: str, limit: int = 20):
        return [] if limit > 0 else []

    def active_count(self) -> int:
        return 1


class DummyHookRunner:
    async def fire_event(self, *_args, **_kwargs):
        return type("HookEvent", (), {"messages": []})()


class DummySkillRegistry:
    def active_count(self) -> int:
        return 0

    def find_user_invocable(self, _name: str):
        return None

    def list_enabled(self) -> list[object]:
        return []

    def match_natural_language(self, _message: str):
        return []

    def load_skill_prompt(self, name: str) -> str:
        return f"Skill prompt for {name}"


class DummyPresenceRegistry:
    def snapshot(self) -> list[object]:
        return []


class DummyOAuthFlowManager:
    async def start_oauth_flow(self, provider: str):
        return {"provider": provider}

    @property
    def token_manager(self):
        class _TokenManager:
            async def list_connected(self):
                return []

        return _TokenManager()


class DummyToolRegistry:
    def __init__(self, *, app_tools_enabled: bool = False) -> None:
        self.app_tools_enabled = app_tools_enabled
        self.calls: list[tuple[str, dict[str, object]]] = []

    def has(self, tool_name: str) -> bool:
        if self.app_tools_enabled and tool_name in {
            "apps_list_windows",
            "apps_open",
            "apps_focus",
            "apps_minimize",
            "apps_maximize",
            "apps_restore",
            "apps_snap",
        }:
            return True
        return False

    async def dispatch(self, tool_name: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append((tool_name, payload))
        if tool_name == "apps_open":
            target = str(payload["target"])
            return {"alias": target, "path": f"C:/Program Files/{target}/{target}.exe", "launched": True}
        if tool_name in {"apps_focus", "apps_minimize", "apps_maximize", "apps_restore"}:
            target = str(payload["target"])
            return {"window": {"title": target.title(), "process_name": target}, "target": target}
        if tool_name == "apps_snap":
            target = str(payload["target"])
            return {"window": {"title": target.title(), "process_name": target}, "target": target, "position": payload["position"]}
        raise AssertionError(f"Unexpected tool dispatch: {tool_name}")


def _build_router(app_config, *, tool_registry: DummyToolRegistry | None = None) -> tuple[GatewayRouter, DummyAgentLoop]:
    agent_loop = DummyAgentLoop()
    router = GatewayRouter(
        config=app_config,
        agent_loop=agent_loop,
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=object(),
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry or DummyToolRegistry(),
        automation_engine=None,
        user_profiles=object(),
        started_at=datetime.now(timezone.utc),
    )
    return router, agent_loop


@pytest.mark.asyncio
async def test_classify_open_chorme_returns_open_app(app_config) -> None:
    classifier = IntentClassifier(app_config)
    result = await classifier.classify("open chorme")
    assert result["intent"] == "open_app"
    assert "chrome" in result["corrected"].lower()


@pytest.mark.asyncio
async def test_classify_weather_returns_chat(app_config) -> None:
    classifier = IntentClassifier(app_config)
    result = await classifier.classify("what is the weather")
    assert result["intent"] == "chat"


@pytest.mark.asyncio
async def test_classify_take_screenshot_returns_screen_action(app_config) -> None:
    classifier = IntentClassifier(app_config)
    result = await classifier.classify("take a screenshot")
    assert result["intent"] == "screen_action"


@pytest.mark.asyncio
async def test_classify_browser_task_from_email_navigation(app_config) -> None:
    classifier = IntentClassifier(app_config)
    result = await classifier.classify("go to gmail.com and read my emails")
    assert result["intent"] == "browser_task"


@pytest.mark.asyncio
async def test_classify_reminder_returns_schedule_reminder(app_config) -> None:
    classifier = IntentClassifier(app_config)
    result = await classifier.classify("remind me at 6pm to check the report")
    assert result["intent"] == "schedule_reminder"


@pytest.mark.asyncio
async def test_fuzzy_match_app_finds_chrome(app_config) -> None:
    classifier = IntentClassifier(app_config)
    matched = await classifier.fuzzy_match_app("chorme", {"chrome", "edge", "vscode"})
    assert matched == "chrome"


@pytest.mark.asyncio
async def test_fuzzy_match_app_returns_none_for_noise(app_config) -> None:
    classifier = IntentClassifier(app_config)
    matched = await classifier.fuzzy_match_app("xyz123", {"chrome", "edge"})
    assert matched is None


@pytest.mark.asyncio
async def test_rewrite_canonical_fixes_typos_with_mocked_llm(monkeypatch, app_config) -> None:
    classifier = IntentClassifier(app_config)

    async def _fake_request(*_args, **_kwargs) -> str:
        return "open chrome and take screenshot"

    monkeypatch.setattr(classifier, "_request_text_completion", _fake_request)
    rewritten = await classifier.rewrite_canonical("opn chrom nd tak screnshot")
    assert "chrome" in rewritten.lower()
    assert "screenshot" in rewritten.lower()


@pytest.mark.asyncio
async def test_high_confidence_result_does_not_trigger_clarification(app_config, monkeypatch) -> None:
    router, agent_loop = _build_router(app_config)

    async def _rewrite(_message: str) -> str:
        return "hello there"

    async def _classify(_message: str) -> dict[str, object]:
        return {
            "intent": "chat",
            "target": "",
            "action": "answer",
            "time_expr": "",
            "corrected": "hello there",
            "confidence": 0.92,
            "raw_slots": {},
        }

    monkeypatch.setattr(router._nlp, "rewrite_canonical", _rewrite)
    monkeypatch.setattr(router._nlp, "classify", _classify)

    response = await router.route_user_message(
        connection_id="conn-1",
        request_id="req-1",
        session_key="session-1",
        message="hello there",
        metadata={},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert agent_loop.enqueued
    system_suffix = agent_loop.enqueued[0].system_suffix or ""
    assert "Ask a single clarifying question before acting." not in system_suffix


@pytest.mark.asyncio
async def test_low_confidence_result_adds_clarification_question(app_config, monkeypatch) -> None:
    router, agent_loop = _build_router(app_config)

    async def _rewrite(_message: str) -> str:
        return "blargh maybe perhaps"

    async def _classify(_message: str) -> dict[str, object]:
        return {
            "intent": "unknown",
            "target": "",
            "action": "",
            "time_expr": "",
            "corrected": "blargh maybe perhaps",
            "confidence": 0.2,
            "raw_slots": {},
        }

    monkeypatch.setattr(router._nlp, "rewrite_canonical", _rewrite)
    monkeypatch.setattr(router._nlp, "classify", _classify)

    response = await router.route_user_message(
        connection_id="conn-2",
        request_id="req-2",
        session_key="session-2",
        message="blargh maybe perhaps",
        metadata={},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert agent_loop.enqueued
    system_suffix = agent_loop.enqueued[0].system_suffix or ""
    assert "Ask a single clarifying question before acting." in system_suffix


@pytest.mark.asyncio
async def test_router_fuzzy_matches_open_app_shortcut(app_config) -> None:
    app_config.desktop_apps.enabled = True
    tool_registry = DummyToolRegistry(app_tools_enabled=True)
    router, _agent_loop = _build_router(app_config, tool_registry=tool_registry)

    response = await router.route_user_message(
        connection_id="conn-3",
        request_id="req-3",
        session_key="session-3",
        message="open chorme",
        metadata={},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert tool_registry.calls
    tool_name, payload = tool_registry.calls[0]
    assert tool_name == "apps_open"
    assert payload["target"] == "chrome"


@pytest.mark.asyncio
async def test_classifier_cache_is_bounded_to_500_entries(app_config, monkeypatch) -> None:
    classifier = IntentClassifier(app_config)
    app_config.llm.gemini_api_key = ""

    async def _regex_precheck(message: str) -> dict[str, object]:
        return {
            "intent": "unknown",
            "target": "",
            "action": "",
            "time_expr": "",
            "corrected": message,
            "confidence": 0.0,
            "raw_slots": {},
        }

    monkeypatch.setattr(classifier, "_regex_precheck", _regex_precheck)
    for index in range(505):
        await classifier.classify(f"message {index}")
    assert len(classifier._cache) == 500
