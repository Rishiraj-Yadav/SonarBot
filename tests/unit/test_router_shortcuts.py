from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from assistant.agent.queue import QueueMode
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
    def active_count(self) -> int:
        return 0

    def active_channels(self) -> list[str]:
        return []

    def get_connection(self, _connection_id: str):
        return None


class DummySessionManager:
    def __init__(self) -> None:
        self.messages = []
        self.session = type("Session", (), {"session_key": "webchat_main"})()

    async def load_or_create(self, session_key: str):
        self.session.session_key = session_key
        return self.session

    async def append_message(self, session, message):
        self.messages.append(message)

    async def session_history(self, _session_key: str, limit: int = 20):
        if limit <= 0:
            return []
        return self.messages[-limit:]

    def active_count(self) -> int:
        return 1


class DummyHookRunner:
    async def fire_event(self, *_args, **_kwargs):
        return type("HookEvent", (), {"messages": []})()


class DummySkillRegistry:
    def __init__(self, enabled=None, matches=None) -> None:
        self._enabled = enabled or []
        self._matches = matches or []

    def active_count(self) -> int:
        return len(self._enabled)

    def find_user_invocable(self, _name: str):
        return None

    def list_enabled(self) -> list[object]:
        return list(self._enabled)

    def match_natural_language(self, _message: str):
        return list(self._matches)

    def load_skill_prompt(self, name: str) -> str:
        return f"Skill prompt for {name}"


class DummyPresenceRegistry:
    def snapshot(self) -> list[object]:
        return []


class DummyOAuthFlowManager:
    async def start_oauth_flow(self, provider: str):
        return {"provider": provider, "authorize_url": "https://example.com", "redirect_uri": "http://127.0.0.1"}

    @property
    def token_manager(self):
        class _TokenManager:
            async def list_connected(self):
                return []

        return _TokenManager()


class DummyToolRegistry:
    def __init__(self, *, llm_task_response: dict[str, object] | None = None, has_llm_task: bool = True) -> None:
        self.calls = []
        self.llm_task_response = llm_task_response or {"content": json.dumps({"skill": "daily-briefing", "confidence": 0.91})}
        self.has_llm_task = has_llm_task

    def has(self, tool_name: str) -> bool:
        return self.has_llm_task and tool_name == "llm_task"

    async def dispatch(self, tool_name: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append((tool_name, payload))
        if tool_name == "gmail_latest_email":
            return {
                "found": True,
                "from": "sender@example.com",
                "subject": "Test subject",
                "date": "Mon, 01 Jan 2026 10:00:00 +0000",
                "snippet": "Snippet text",
                "body": "Body preview",
            }
        if tool_name == "github_list_repos":
            return {"repositories": [{"full_name": "octo/repo-1"}, {"full_name": "octo/repo-2"}]}
        if tool_name == "github_list_pull_requests":
            return {
                "owner": payload["owner"],
                "repo": payload["repo"],
                "pull_requests": [
                    {
                        "number": 7,
                        "title": "Improve routing",
                        "user": "octocat",
                        "html_url": "https://github.com/octo/repo/pull/7",
                    }
                ],
            }
        if tool_name == "llm_task":
            return self.llm_task_response
        raise AssertionError(f"Unexpected tool: {tool_name}")


class DummyAutomationEngine:
    async def list_notifications(self, _user_id: str):
        return []

    async def list_runs(self, _user_id: str):
        return []

    async def list_rules(self, _user_id: str):
        return []

    async def pause_rule(self, _user_id: str, _rule_name: str) -> None:
        return None

    async def resume_rule(self, _user_id: str, _rule_name: str) -> None:
        return None

    async def replay_run(self, _run_id: str):
        return {"status": "ok"}

    async def list_approvals(self, _user_id: str):
        return []

    async def decide_approval(self, _approval_id: str, _decision: str) -> None:
        return None


class DummyUserProfiles:
    async def resolve_user_id(self, _identity_type: str, _identity_value: str, _metadata=None) -> str:
        return "default"


class FakeSkill:
    def __init__(
        self,
        name: str,
        *,
        description: str = "Skill description",
        aliases: list[str] | None = None,
        activation_examples: list[str] | None = None,
        keywords: list[str] | None = None,
        user_invocable: bool = True,
        natural_language_enabled: bool = True,
    ) -> None:
        self.name = name
        self.description = description
        self.aliases = aliases or []
        self.activation_examples = activation_examples or []
        self.keywords = keywords or []
        self.user_invocable = user_invocable
        self.natural_language_enabled = natural_language_enabled
        self.priority = 0


class FakeSkillMatch:
    def __init__(self, skill, score: int, *, exact: bool = False) -> None:
        self.skill = skill
        self.score = score
        self.exact = exact


@pytest.mark.asyncio
async def test_router_shortcuts_latest_email_without_model(app_config) -> None:
    tool_registry = DummyToolRegistry()
    session_manager = DummySessionManager()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=session_manager,
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-1",
        request_id="req-1",
        session_key="webchat_main",
        message="what is the last mail i got",
        metadata={"trace_id": "trace-1"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is False
    assert "Latest email in your inbox" in response.payload["command_response"]
    assert tool_registry.calls[0][0] == "gmail_latest_email"
    assert [message["role"] for message in session_manager.messages] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_router_shortcuts_repo_count_without_model(app_config) -> None:
    tool_registry = DummyToolRegistry()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-1",
        request_id="req-2",
        session_key="webchat_main",
        message="how many repos do i have",
        metadata={"trace_id": "trace-2"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is False
    assert "2 repositories" in response.payload["command_response"]
    assert tool_registry.calls[0][0] == "github_list_repos"


@pytest.mark.asyncio
async def test_router_shortcuts_pull_request_check_uses_recent_repo_context(app_config) -> None:
    tool_registry = DummyToolRegistry()
    session_manager = DummySessionManager()
    await session_manager.append_message(
        session_manager.session,
        {
            "id": "msg-1",
            "role": "assistant",
            "content": "The repository is Rishiraj-Yadav/Personal-AI-Assistant.",
        },
    )
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=session_manager,
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-1",
        request_id="req-3",
        session_key="webchat_main",
        message="is there any pull request to this repo",
        metadata={"trace_id": "trace-3"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is False
    assert "Rishiraj-Yadav/Personal-AI-Assistant" in response.payload["command_response"]
    assert "#7: Improve routing" in response.payload["command_response"]
    assert tool_registry.calls[-1][0] == "github_list_pull_requests"


@pytest.mark.asyncio
async def test_router_shortcuts_win_before_skill_activation(app_config) -> None:
    tool_registry = DummyToolRegistry()
    skill = FakeSkill("gmail-triage", aliases=["check my inbox"], keywords=["inbox", "email"])
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(matches=[FakeSkillMatch(skill, 100, exact=True)]),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-1",
        request_id="req-4",
        session_key="webchat_main",
        message="what is the last email i received",
        metadata={"trace_id": "trace-4"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is False
    assert tool_registry.calls[0][0] == "gmail_latest_email"


@pytest.mark.asyncio
async def test_router_auto_activates_exact_skill_match(app_config) -> None:
    agent_loop = DummyAgentLoop()
    skill = FakeSkill(
        "gmail-triage",
        description="Inbox triage",
        aliases=["check my inbox"],
        activation_examples=["triage my emails"],
        keywords=["email", "inbox"],
    )
    router = GatewayRouter(
        config=app_config,
        agent_loop=agent_loop,
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(enabled=[skill], matches=[FakeSkillMatch(skill, 100, exact=True)]),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(),
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-1",
        request_id="req-5",
        session_key="webchat_main",
        message="check my inbox",
        metadata={"trace_id": "trace-5"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is True
    assert response.payload["activated_skill"] == "gmail-triage"
    enqueued = agent_loop.enqueued[-1]
    assert enqueued.metadata["activated_skill"] == "gmail-triage"
    assert enqueued.metadata["skill_activation_source"] == "exact"
    assert "## Active Skill" in (enqueued.system_suffix or "")


@pytest.mark.asyncio
async def test_router_falls_back_when_skill_match_is_ambiguous(app_config) -> None:
    agent_loop = DummyAgentLoop()
    first = FakeSkill("daily-briefing", aliases=["daily briefing"], keywords=["briefing", "today"])
    second = FakeSkill("gmail-briefing", aliases=["inbox briefing"], keywords=["briefing", "email"])
    router = GatewayRouter(
        config=app_config,
        agent_loop=agent_loop,
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(
            enabled=[first, second],
            matches=[FakeSkillMatch(first, 5), FakeSkillMatch(second, 4)],
        ),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(has_llm_task=False),
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-1",
        request_id="req-6",
        session_key="webchat_main",
        message="give me a briefing",
        metadata={"trace_id": "trace-6"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is True
    enqueued = agent_loop.enqueued[-1]
    assert "activated_skill" not in enqueued.metadata


@pytest.mark.asyncio
async def test_router_uses_classifier_for_plausible_skill_candidates(app_config) -> None:
    agent_loop = DummyAgentLoop()
    target = FakeSkill("daily-briefing", aliases=["morning briefing"], keywords=["briefing", "today"])
    other = FakeSkill("gmail-briefing", aliases=["inbox briefing"], keywords=["briefing", "email"])
    router = GatewayRouter(
        config=app_config,
        agent_loop=agent_loop,
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(
            enabled=[target, other],
            matches=[FakeSkillMatch(target, 5), FakeSkillMatch(other, 4)],
        ),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(
            llm_task_response={"content": json.dumps({"skill": "daily-briefing", "confidence": 0.93})}
        ),
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-1",
        request_id="req-7",
        session_key="webchat_main",
        message="give me a briefing for today",
        metadata={"trace_id": "trace-7"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is True
    assert response.payload["activated_skill"] == "daily-briefing"
    enqueued = agent_loop.enqueued[-1]
    assert enqueued.metadata["skill_activation_source"] == "classifier"
