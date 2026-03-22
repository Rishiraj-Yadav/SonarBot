"""Silent memory flush before session compaction."""

from __future__ import annotations

import json

from assistant.agent.context import build_model_messages


class MemoryFlushRunner:
    def __init__(self, model_provider, tool_registry) -> None:
        self.model_provider = model_provider
        self.tool_registry = tool_registry

    async def maybe_flush(self, session, system_prompt: str) -> None:
        if session.metadata.get("memory_flush_ran"):
            return

        memory_tool = self._memory_write_tool()
        if memory_tool is None:
            session.metadata["memory_flush_ran"] = True
            return

        prompt = (
            f"{system_prompt}\n\n"
            "Session nearing compaction. Store any durable memories now before context is compressed."
        )
        messages = build_model_messages(session.messages) + [
            {"role": "user", "content": "Write lasting notes to today's memory file. Reply with NO_REPLY if nothing to save."}
        ]
        follow_up_messages: list[dict[str, str]] = []

        for _ in range(2):
            tool_calls = []
            async for response in self.model_provider.complete(
                messages=messages + follow_up_messages,
                system=prompt,
                tools=[memory_tool],
                stream=False,
            ):
                if response.text and response.text.strip().upper() == "NO_REPLY":
                    session.metadata["memory_flush_ran"] = True
                    return
                if response.tool_calls:
                    tool_calls.extend(response.tool_calls)

            if not tool_calls:
                break

            for tool_call in tool_calls:
                if tool_call.name != "memory_write":
                    continue
                result = await self.tool_registry.dispatch(tool_call.name, tool_call.arguments)
                follow_up_messages.append(
                    {
                        "role": "tool",
                        "name": tool_call.name,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

        session.metadata["memory_flush_ran"] = True

    def reset_cycle(self, session) -> None:
        session.metadata["memory_flush_ran"] = False

    def _memory_write_tool(self) -> dict | None:
        for tool in self.tool_registry.get_tools_schema():
            if tool["name"] == "memory_write":
                return tool
        return None
