"""Sub-agent spawning and orchestration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from assistant.agent.loop import AgentLoop
from assistant.agent.queue import AgentRequest, QueueMode


class StaticPromptBuilder:
    def __init__(self, prompt: str) -> None:
        self.prompt = prompt

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def build(self) -> str:
        return self.prompt


@dataclass(slots=True)
class SubAgentHandle:
    agent_name: str
    session_key: str
    _task: asyncio.Task[str]

    async def result(self) -> str:
        return await self._task


class SubAgentManager:
    def __init__(
        self,
        config,
        model_provider,
        session_manager,
        base_tool_registry,
        presence_registry,
    ) -> None:
        self.config = config
        self.model_provider = model_provider
        self.session_manager = session_manager
        self.base_tool_registry = base_tool_registry
        self.presence_registry = presence_registry
        self._handles: dict[str, SubAgentHandle] = {}

    def spawn_sub_agent(
        self,
        task: str,
        tools: list[str] | None = None,
        context: str = "",
        session_key: str | None = None,
        max_turns: int = 20,
    ) -> SubAgentHandle:
        agent_name = f"subagent-{uuid4().hex[:8]}"
        resolved_session_key = session_key or f"subagent:{uuid4().hex}"
        selected_tools = tools or [name for name in self.base_tool_registry.names() if name != "agent_send"]
        tool_registry = self.base_tool_registry.subset([name for name in selected_tools if name != "agent_send"])
        prompt = (
            "You are a focused SonarBot sub-agent.\n"
            f"Task: {task}\n"
            f"Context: {context or '[none]'}\n"
            f"Finish the delegated task within {max_turns} reasoning passes and return a compact final answer."
        )
        prompt_builder = StaticPromptBuilder(prompt)
        done_event = asyncio.Event()

        async def emit(_connection_id: str, event_name: str, _payload: dict[str, Any]) -> None:
            if event_name == "agent.done":
                done_event.set()

        loop = AgentLoop(
            config=self.config,
            model_provider=self.model_provider,
            tool_registry=tool_registry,
            session_manager=self.session_manager,
            system_prompt_builder=prompt_builder,
            event_emitter=emit,
        )

        async def runner() -> str:
            self.presence_registry.register(agent_name, resolved_session_key, capabilities=tools or [])
            self.presence_registry.update(agent_name, "running")
            await prompt_builder.start()
            await loop.start()
            try:
                await loop.enqueue(
                    AgentRequest(
                        connection_id=agent_name,
                        session_key=resolved_session_key,
                        message=task,
                        request_id=uuid4().hex,
                        mode=QueueMode.FOLLOWUP,
                    )
                )
                await asyncio.wait_for(done_event.wait(), timeout=300)
                session = await self.session_manager.load_or_create(resolved_session_key)
                for message in reversed(session.messages):
                    if message.get("role") == "assistant" and message.get("content", "").strip():
                        return str(message["content"])
                return ""
            finally:
                self.presence_registry.update(agent_name, "idle")
                await loop.stop()
                await prompt_builder.stop()
                self.presence_registry.unregister(agent_name)

        task_handle = asyncio.create_task(runner())
        handle = SubAgentHandle(agent_name=agent_name, session_key=resolved_session_key, _task=task_handle)
        self._handles[agent_name] = handle
        return handle
