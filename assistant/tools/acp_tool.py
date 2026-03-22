"""ACP tool definitions."""

from __future__ import annotations

from assistant.tools.registry import ToolDefinition


def build_acp_tools(acp_client) -> list[ToolDefinition]:
    async def acp_send(payload: dict[str, object]) -> dict[str, object]:
        agent_name = str(payload["agent_name"])
        task = str(payload["task"])
        return await acp_client.send_task(agent_name, task)

    return [
        ToolDefinition(
            name="acp_send",
            description="Send a task to an external ACP-compatible local agent and stream back its result.",
            parameters={
                "type": "object",
                "properties": {
                    "agent_name": {"type": "string"},
                    "task": {"type": "string"},
                },
                "required": ["agent_name", "task"],
            },
            handler=acp_send,
        )
    ]
