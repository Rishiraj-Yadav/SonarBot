"""Sub-agent delegation tool."""

from __future__ import annotations

import asyncio

from assistant.tools.registry import ToolDefinition


def build_agent_send_tool(sub_agent_manager) -> ToolDefinition:
    async def agent_send(payload):
        task = str(payload["task"])
        context = str(payload.get("context", ""))
        tools = payload.get("tools")
        tool_list = [str(item) for item in tools] if isinstance(tools, list) else None
        handle = sub_agent_manager.spawn_sub_agent(task=task, tools=tool_list, context=context)
        result = await asyncio.wait_for(handle.result(), timeout=300)
        return {"session_key": handle.session_key, "result": result}

    return ToolDefinition(
        name="agent_send",
        description="Delegate a bounded task to a sub-agent with an isolated session and optional restricted tools.",
        parameters={
            "type": "object",
            "properties": {
                "task": {"type": "string"},
                "context": {"type": "string", "default": ""},
                "tools": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["task"],
        },
        handler=agent_send,
    )
