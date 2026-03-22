from __future__ import annotations

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
    def active_count(self) -> int:
        return 0

    def find_user_invocable(self, _name: str):
        return None

    def list_enabled(self) -> list[object]:
        return []


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
    def __init__(self) -> None:
        self.calls = []

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
        raise AssertionError(f"Unexpected tool: {tool_name}")


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
