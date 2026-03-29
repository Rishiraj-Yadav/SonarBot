"""Cheap non-streaming LLM task tool."""

from __future__ import annotations

from typing import Any

from assistant.agent.streaming import merge_text_chunks
from assistant.tools.registry import ToolDefinition


def build_llm_task_tool(model_provider) -> ToolDefinition:
    async def llm_task(payload: dict[str, Any]) -> dict[str, Any]:
        prompt = str(payload["prompt"])
        requested_model = str(payload.get("model", "cheap"))
        provider = model_provider
        getter = getattr(model_provider, "get_task_provider", None)
        if callable(getter):
            provider = getter(requested_model)
        chunks: list[str] = []
        async for response in provider.complete(
            messages=[{"role": "user", "content": prompt}],
            system=f"You are a fast helper. Use a concise {requested_model} response style.",
            tools=[],
            stream=False,
        ):
            if response.text:
                chunks.append(response.text)
        return {"model": requested_model, "content": merge_text_chunks(chunks)}

    return ToolDefinition(
        name="llm_task",
        description="Run a quick non-streaming LLM subtask for classification, formatting, or summarization.",
        parameters={
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "model": {"type": "string", "default": "cheap"},
            },
            "required": ["prompt"],
        },
        handler=llm_task,
    )
