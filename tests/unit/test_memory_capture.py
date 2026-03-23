from __future__ import annotations

import pytest

from assistant.agent.session import create_message
from assistant.agent.session_manager import SessionManager
from assistant.memory import MemoryAutoCaptureRunner, MemoryManager
from assistant.models.base import ModelResponse, ToolCall
from assistant.tools.memory_tool import build_memory_tools
from assistant.tools.registry import ToolRegistry
from tests.helpers import FakeProvider


@pytest.mark.asyncio
async def test_memory_auto_capture_writes_long_term_memory(app_config) -> None:
    memory_manager = MemoryManager(app_config)
    session_manager = SessionManager(app_config)
    session = await session_manager.create_session("main")
    await session_manager.append_message(session, create_message("user", "Remember that my favorite editor is VS Code."))
    await session_manager.append_message(session, create_message("assistant", "I'll remember that for future coding help."))

    provider = FakeProvider(
        [
            [
                ModelResponse(
                    tool_calls=[
                        ToolCall(
                            id="memory-1",
                            name="memory_write",
                            arguments={
                                "memory_type": "longterm",
                                "key": "Preferred Editor",
                                "content": "The user prefers VS Code for coding tasks.",
                            },
                        )
                    ],
                    done=True,
                )
            ],
            [ModelResponse(text="NO_MEMORY", done=True)],
        ]
    )
    registry = ToolRegistry()
    for tool in build_memory_tools(memory_manager):
        registry.register(tool)

    runner = MemoryAutoCaptureRunner(app_config, provider, registry)
    await runner.maybe_capture(
        session,
        "System prompt",
        "Remember that my favorite editor is VS Code.",
        "I'll remember that for future coding help.",
    )

    long_term = await memory_manager.read_long_term()
    assert "Preferred Editor" in long_term
    assert "VS Code" in long_term


@pytest.mark.asyncio
async def test_memory_auto_capture_ignores_generic_chatter(app_config) -> None:
    memory_manager = MemoryManager(app_config)
    provider = FakeProvider([])
    registry = ToolRegistry()
    for tool in build_memory_tools(memory_manager):
        registry.register(tool)

    runner = MemoryAutoCaptureRunner(app_config, provider, registry)
    session = await SessionManager(app_config).create_session("main")
    await runner.maybe_capture(session, "System prompt", "hello there", "Hi!")

    assert provider.calls == []
