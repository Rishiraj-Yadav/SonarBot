from __future__ import annotations

import asyncio

import pytest

from assistant.agent.loop import AgentLoop
from assistant.agent.queue import AgentRequest
from assistant.agent.session_manager import SessionManager
from assistant.agent.system_prompt import SystemPromptBuilder
from assistant.models.base import ModelResponse, ToolCall
from assistant.tools.registry import ToolDefinition, ToolRegistry
from tests.helpers import FakeProvider


@pytest.mark.asyncio
async def test_agent_loop_executes_tool_call(app_config) -> None:
    provider = FakeProvider(
        [
            [ModelResponse(tool_calls=[ToolCall(id="tool-1", name="echo_tool", arguments={"text": "hi"})], done=True)],
            [ModelResponse(text="Finished after tool.", done=True)],
        ]
    )
    prompt_builder = SystemPromptBuilder(app_config.agent.workspace_dir)
    session_manager = SessionManager(app_config)
    registry = ToolRegistry()

    async def echo_tool(payload):
        return {"echo": payload["text"]}

    registry.register(
        ToolDefinition(
            name="echo_tool",
            description="Echo text",
            parameters={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
            handler=echo_tool,
        )
    )

    events: list[tuple[str, dict[str, object]]] = []
    done_event = asyncio.Event()

    async def emit(_connection_id, event_name, payload):
        events.append((event_name, payload))
        if event_name == "agent.done":
            done_event.set()

    loop = AgentLoop(
        config=app_config,
        model_provider=provider,
        tool_registry=registry,
        session_manager=session_manager,
        system_prompt_builder=prompt_builder,
        event_emitter=emit,
    )
    await prompt_builder.start()
    await loop.start()
    await loop.enqueue(AgentRequest(connection_id="conn-1", session_key="main", message="hello", request_id="req-1"))
    await asyncio.wait_for(done_event.wait(), timeout=5)
    await loop.stop()
    await prompt_builder.stop()

    session = await session_manager.load_or_create("main")
    assert any(event_name == "agent.chunk" for event_name, _ in events)
    assert events[-1][0] == "agent.done"
    assert any(message.get("role") == "tool" for message in session.messages)


@pytest.mark.asyncio
async def test_agent_loop_hides_intermediate_tool_call_text(app_config) -> None:
    provider = FakeProvider(
        [
            [
                ModelResponse(
                    text="Let me check your repositories first.",
                    tool_calls=[ToolCall(id="tool-1", name="echo_tool", arguments={"text": "repos"})],
                    done=True,
                )
            ],
            [ModelResponse(text="You have 1 repository connected.", done=True)],
        ]
    )
    prompt_builder = SystemPromptBuilder(app_config.agent.workspace_dir)
    session_manager = SessionManager(app_config)
    registry = ToolRegistry()

    async def echo_tool(payload):
        return {"echo": payload["text"]}

    registry.register(
        ToolDefinition(
            name="echo_tool",
            description="Echo text",
            parameters={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
            handler=echo_tool,
        )
    )

    events: list[tuple[str, dict[str, object]]] = []
    done_event = asyncio.Event()

    async def emit(_connection_id, event_name, payload):
        events.append((event_name, payload))
        if event_name == "agent.done":
            done_event.set()

    loop = AgentLoop(
        config=app_config,
        model_provider=provider,
        tool_registry=registry,
        session_manager=session_manager,
        system_prompt_builder=prompt_builder,
        event_emitter=emit,
    )
    await prompt_builder.start()
    await loop.start()
    await loop.enqueue(AgentRequest(connection_id="conn-1", session_key="main", message="hello", request_id="req-2"))
    await asyncio.wait_for(done_event.wait(), timeout=5)
    await loop.stop()
    await prompt_builder.stop()

    chunks = [payload["text"] for event_name, payload in events if event_name == "agent.chunk"]
    assert chunks == ["You have 1 repository connected."]
