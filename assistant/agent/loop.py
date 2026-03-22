"""Main agent loop."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from assistant.agent.compaction import CompactionManager
from assistant.agent.context import build_model_messages
from assistant.agent.queue import AgentQueue, AgentRequest
from assistant.agent.session import create_message
from assistant.agent.streaming import merge_text_chunks

EventEmitter = Callable[[str, str, dict[str, Any]], Awaitable[None]]
TypingEmitter = Callable[[str], Awaitable[None]]


class AgentLoop:
    def __init__(
        self,
        config,
        model_provider,
        tool_registry,
        session_manager,
        system_prompt_builder,
        event_emitter: EventEmitter,
        typing_emitter: TypingEmitter | None = None,
    ) -> None:
        self.config = config
        self.model_provider = model_provider
        self.tool_registry = tool_registry
        self.session_manager = session_manager
        self.system_prompt_builder = system_prompt_builder
        self.event_emitter = event_emitter
        self.typing_emitter = typing_emitter
        self.queue = AgentQueue()
        self.compaction_manager = CompactionManager(config, session_manager, model_provider, tool_registry)
        self._task: asyncio.Task[None] | None = None
        self._current_task: asyncio.Task[None] | None = None
        self._current_request: AgentRequest | None = None
        self._running = False

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def enqueue(self, request: AgentRequest) -> None:
        await self.queue.put(request)

    def is_idle(self) -> bool:
        return not self._running and self.queue.pending_count() == 0

    def status(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "pending": self.queue.pending_count(),
            "current_session_key": self._current_request.session_key if self._current_request else None,
        }

    async def cancel_session(self, session_key: str | None = None) -> bool:
        if self._current_task is None or self._current_task.done():
            return False
        if session_key is not None and self._current_request is not None:
            if self._current_request.session_key != session_key:
                return False
        self._current_task.cancel()
        return True

    async def wait_for_idle(self, timeout: float = 30.0) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if self.is_idle():
                return True
            await asyncio.sleep(0.1)
        return self.is_idle()

    async def _run(self) -> None:
        while True:
            request = await self.queue.get()
            self._running = True
            self._current_request = request
            self._current_task = asyncio.create_task(self._handle_request(request))
            try:
                await self._current_task
            except asyncio.CancelledError:
                if self._current_task is not None and not self._current_task.done():
                    self._current_task.cancel()
                    try:
                        await self._current_task
                    except asyncio.CancelledError:
                        pass
                if not request.silent and request.connection_id:
                    await self.event_emitter(
                        request.connection_id,
                        "agent.done",
                        {"session_key": request.session_key, "cancelled": True},
                    )
                raise
            finally:
                self._running = False
                self._current_request = None
                self._current_task = None

    async def _handle_request(self, request: AgentRequest) -> None:
        session = await self.session_manager.load_or_create(request.session_key)
        if self.typing_emitter is not None and not request.silent and request.connection_id:
            await self.typing_emitter(request.connection_id)
        await self.session_manager.append_message(
            session,
            create_message("user", request.message, **(request.metadata or {})),
        )

        system_prompt = await self.system_prompt_builder.build()
        if request.system_suffix:
            system_prompt = f"{system_prompt}\n\n{request.system_suffix}".strip()
        await self.compaction_manager.maybe_compact(session, system_prompt)

        current_connection_id = request.connection_id

        while True:
            system_prompt = await self.system_prompt_builder.build()
            if request.system_suffix:
                system_prompt = f"{system_prompt}\n\n{request.system_suffix}".strip()
            text_chunks: list[str] = []
            tool_calls = []
            usage = None

            try:
                async for response in self.model_provider.complete(
                    messages=build_model_messages(session.messages),
                    system=system_prompt,
                    tools=self.tool_registry.get_tools_schema(),
                    stream=True,
                ):
                    if response.text:
                        text_chunks.append(response.text)
                        if not request.silent and current_connection_id:
                            await self.event_emitter(current_connection_id, "agent.chunk", {"text": response.text})
                    if response.tool_calls:
                        tool_calls.extend(response.tool_calls)
                    if response.usage is not None:
                        usage = response.usage
            except Exception as exc:  # pragma: no cover - defensive runtime path
                error_text = f"[Model error] {exc}"
                await self.session_manager.append_message(session, create_message("assistant", error_text))
                if not request.silent and current_connection_id:
                    await self.event_emitter(current_connection_id, "agent.chunk", {"text": error_text})
                    await self.event_emitter(current_connection_id, "agent.done", {"session_key": request.session_key})
                return

            assistant_text = merge_text_chunks(text_chunks)
            skip_assistant_record = request.silent and assistant_text.strip().upper() == "NO_REPLY" and not tool_calls
            if (assistant_text or tool_calls) and not skip_assistant_record:
                await self.session_manager.append_message(
                    session,
                    create_message(
                        "assistant",
                        assistant_text,
                        tool_calls=[
                            {"id": item.id, "name": item.name, "arguments": item.arguments} for item in tool_calls
                        ],
                        usage=(
                            {
                                "input_tokens": usage.input_tokens,
                                "output_tokens": usage.output_tokens,
                                "total_tokens": usage.total_tokens,
                            }
                            if usage
                            else None
                        ),
                    ),
                )

            if not tool_calls:
                if not request.silent and current_connection_id:
                    await self.event_emitter(current_connection_id, "agent.done", {"session_key": request.session_key})
                return

            for tool_call in tool_calls:
                tool_result = await self._dispatch_tool(tool_call.name, tool_call.arguments, session.session_key)
                await self.session_manager.append_message(
                    session,
                    create_message(
                        "tool",
                        json.dumps(tool_result, ensure_ascii=False),
                        name=tool_call.name,
                        tool_call_id=tool_call.id,
                    ),
                )

            steer_messages = await self.queue.collect_steer_messages(session.session_key)
            for steer_request in steer_messages:
                current_connection_id = steer_request.connection_id or current_connection_id
                await self.session_manager.append_message(
                    session,
                    create_message("user", steer_request.message, **(steer_request.metadata or {})),
                )

            await self.compaction_manager.maybe_compact(session, system_prompt)

    async def _dispatch_tool(self, tool_name: str, tool_input: dict[str, Any], session_key: str) -> dict[str, Any]:
        try:
            payload = dict(tool_input)
            payload.setdefault("session_key", session_key)
            return await self.tool_registry.dispatch(tool_name, payload)
        except Exception as exc:  # pragma: no cover - defensive runtime path
            return {"error": str(exc), "tool_name": tool_name}
