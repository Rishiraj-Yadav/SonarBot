from __future__ import annotations

import pytest

from assistant.agent.session_manager import SessionManager
from assistant.models.base import ModelResponse
from assistant.multi_agent import PresenceRegistry, SubAgentManager
from assistant.tools import create_default_tool_registry
from tests.helpers import FakeProvider


@pytest.mark.asyncio
async def test_sub_agent_completes_and_returns_result(app_config) -> None:
    provider = FakeProvider([[ModelResponse(text="Sub-agent finished.", done=True)]])
    session_manager = SessionManager(app_config)
    registry = create_default_tool_registry(app_config, model_provider=provider)
    presence = PresenceRegistry()
    manager = SubAgentManager(
        config=app_config,
        model_provider=provider,
        session_manager=session_manager,
        base_tool_registry=registry,
        presence_registry=presence,
    )

    handle = manager.spawn_sub_agent(task="Summarize this task", tools=["llm_task"], context="Short context")
    result = await handle.result()

    assert result == "Sub-agent finished."
    assert presence.active_count() == 0
