from __future__ import annotations

import asyncio

import pytest
import httpx

from assistant.agent.loop import AgentLoop
from assistant.agent.queue import AgentRequest
from assistant.agent.session import create_message
from assistant.agent.session_manager import SessionManager
from assistant.agent.system_prompt import SystemPromptBuilder
from assistant.models.base import ModelResponse, ToolCall
from assistant.tools.registry import ToolDefinition, ToolRegistry
from tests.helpers import FakeProvider


class StubMemoryCaptureRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    async def maybe_capture(self, session, system_prompt: str, latest_user_message: str, latest_assistant_message: str) -> None:
        self.calls.append(
            {
                "session_key": session.session_key,
                "system_prompt": system_prompt,
                "latest_user_message": latest_user_message,
                "latest_assistant_message": latest_assistant_message,
            }
        ) 


class RaisingProvider:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    async def complete(self, messages, system, tools, stream=True):
        raise self.exc
        yield  # pragma: no cover


class RetryOnce400Provider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.actual_calls: list[list[dict[str, object]]] = []

    async def complete(self, messages, system, tools, stream=True):
        self.calls.append({"messages": messages, "system": system, "tools": tools, "stream": stream})
        if messages and len(messages) == 1 and str(messages[0].get("content", "")).startswith(
            "Summarize this conversation history concisely"
        ):
            yield ModelResponse(text="Compacted summary.", done=True)
            return

        self.actual_calls.append(messages)
        if len(self.actual_calls) == 1:
            request = httpx.Request("POST", "https://example.com")
            response = httpx.Response(400, request=request, text='{"error":{"message":"bad request"}}')
            raise httpx.HTTPStatusError("bad request", request=request, response=response)

        yield ModelResponse(text="Recovered after retry.", done=True)


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
    assistant_tool_messages = [message for message in session.messages if message.get("role") == "assistant" and message.get("tool_calls")]
    assert assistant_tool_messages
    assert assistant_tool_messages[0].get("content", "") == ""


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
    assert chunks == ["Let me check your repositories first.", "You have 1 repository connected."]
    session = await session_manager.load_or_create("main")
    assistant_tool_messages = [message for message in session.messages if message.get("role") == "assistant" and message.get("tool_calls")]
    assert assistant_tool_messages
    assert assistant_tool_messages[0].get("content", "") == "Let me check your repositories first."


@pytest.mark.asyncio
async def test_agent_loop_runs_memory_capture_after_completed_turn(app_config) -> None:
    provider = FakeProvider([[ModelResponse(text="I'll remember that.", done=True)]])
    prompt_builder = SystemPromptBuilder(app_config.agent.workspace_dir)
    session_manager = SessionManager(app_config)
    registry = ToolRegistry()
    memory_capture_runner = StubMemoryCaptureRunner()

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
        memory_capture_runner=memory_capture_runner,
    )
    await prompt_builder.start()
    await loop.start()
    await loop.enqueue(
        AgentRequest(
            connection_id="conn-1",
            session_key="main",
            message="Remember that I prefer concise answers.",
            request_id="req-3",
        )
    )
    await asyncio.wait_for(done_event.wait(), timeout=5)
    await loop.stop()
    await prompt_builder.stop()

    assert events[-1][0] == "agent.done"
    assert memory_capture_runner.calls
    assert memory_capture_runner.calls[0]["latest_user_message"] == "Remember that I prefer concise answers."
    assert memory_capture_runner.calls[0]["latest_assistant_message"] == "I'll remember that."


@pytest.mark.asyncio
async def test_agent_loop_surfaces_friendly_model_error(app_config) -> None:
    request = httpx.Request("POST", "https://example.com")
    response = httpx.Response(429, request=request)
    provider = RaisingProvider(httpx.HTTPStatusError("rate limited", request=request, response=response))
    prompt_builder = SystemPromptBuilder(app_config.agent.workspace_dir)
    session_manager = SessionManager(app_config)
    registry = ToolRegistry()

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
    await loop.enqueue(AgentRequest(connection_id="conn-1", session_key="main", message="hello", request_id="req-err"))
    await asyncio.wait_for(done_event.wait(), timeout=5)
    await loop.stop()
    await prompt_builder.stop()

    chunks = [payload["text"] for event_name, payload in events if event_name == "agent.chunk"]
    assert chunks == ["The model is temporarily rate-limited. Please wait a minute and try again."]


@pytest.mark.asyncio
async def test_agent_loop_filters_prior_model_errors_from_context(app_config) -> None:
    provider = FakeProvider([[ModelResponse(text="Clean context reply.", done=True)]])
    prompt_builder = SystemPromptBuilder(app_config.agent.workspace_dir)
    session_manager = SessionManager(app_config)
    registry = ToolRegistry()
    session = await session_manager.load_or_create("main")
    await session_manager.append_message(
        session,
        create_message(
            "assistant",
            "[Model error] Client error '400 Bad Request' for url 'https://generativelanguage.googleapis.com/...'",
        ),
    )
    await session_manager.append_message(
        session,
        create_message(
            "assistant",
            "For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/400",
        ),
    )

    done_event = asyncio.Event()

    async def emit(_connection_id, event_name, _payload):
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
    await loop.enqueue(AgentRequest(connection_id="conn-1", session_key="main", message="hello", request_id="req-clean"))
    await asyncio.wait_for(done_event.wait(), timeout=5)
    await loop.stop()
    await prompt_builder.stop()

    first_call_messages = provider.calls[0]["messages"]
    assert all("[Model error]" not in str(message.get("content", "")) for message in first_call_messages)
    assert all("developer.mozilla.org" not in str(message.get("content", "")) for message in first_call_messages)


@pytest.mark.asyncio
async def test_agent_loop_retries_once_with_trimmed_context_on_gemini_400(app_config) -> None:
    app_config.agent.context_window = 99999
    provider = RetryOnce400Provider()
    prompt_builder = SystemPromptBuilder(app_config.agent.workspace_dir)
    session_manager = SessionManager(app_config)
    registry = ToolRegistry()
    session = await session_manager.load_or_create("main")
    for index in range(20):
        await session_manager.append_message(session, create_message("user", f"history user {index}"))
        await session_manager.append_message(session, create_message("assistant", f"history assistant {index}"))

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
    await loop.enqueue(AgentRequest(connection_id="conn-1", session_key="main", message="fresh request", request_id="req-retry"))
    await asyncio.wait_for(done_event.wait(), timeout=5)
    await loop.stop()
    await prompt_builder.stop()

    assert len(provider.actual_calls) == 2
    assert len(provider.actual_calls[1]) < len(provider.actual_calls[0])
    chunks = [payload["text"] for event_name, payload in events if event_name == "agent.chunk"]
    assert chunks == ["Recovered after retry."]
