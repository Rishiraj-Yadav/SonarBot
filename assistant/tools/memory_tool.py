"""Memory tool definitions."""

from __future__ import annotations

from typing import Any

from assistant.tools.registry import ToolDefinition


def build_memory_tools(memory_manager) -> list[ToolDefinition]:
    async def memory_write(payload: dict[str, Any]) -> dict[str, Any]:
        content = str(payload["content"])
        memory_type = payload.get("memory_type", "daily")
        if memory_type == "daily":
            return await memory_manager.write_daily(content)
        if memory_type == "longterm":
            key = str(payload.get("key") or "").strip()
            if not key:
                raise ValueError("memory_write with memory_type='longterm' requires a key.")
            return await memory_manager.write_long_term(key, content)
        raise ValueError("memory_type must be 'daily' or 'longterm'.")

    async def memory_get(payload: dict[str, Any]) -> dict[str, Any]:
        selector = str(payload.get("file", "today"))
        if selector not in {"today", "yesterday", "longterm"}:
            raise ValueError("file must be one of today, yesterday, or longterm.")
        return {"file": selector, "content": await memory_manager.get_memory_file(selector)}

    async def memory_search(payload: dict[str, Any]) -> dict[str, Any]:
        query = str(payload["query"])
        limit = int(payload.get("limit", 5))
        return {"query": query, "results": await memory_manager.search(query, limit)}

    return [
        ToolDefinition(
            name="memory_write",
            description="Write durable memory to the daily log or long-term MEMORY.md.",
            parameters={
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "memory_type": {"type": "string", "enum": ["daily", "longterm"], "default": "daily"},
                    "key": {"type": "string"},
                },
                "required": ["content"],
            },
            handler=memory_write,
        ),
        ToolDefinition(
            name="memory_get",
            description="Read today's, yesterday's, or long-term memory content.",
            parameters={
                "type": "object",
                "properties": {"file": {"type": "string", "enum": ["today", "yesterday", "longterm"]}},
                "required": ["file"],
            },
            handler=memory_get,
        ),
        ToolDefinition(
            name="memory_search",
            description="Search memory using hybrid semantic and BM25 retrieval.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "default": 5},
                },
                "required": ["query"],
            },
            handler=memory_search,
        ),
    ]
