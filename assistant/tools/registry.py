"""Tool registry and dispatch."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
CleanupHandler = Callable[[], Awaitable[None]]
ResultRedactor = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
InputRedactor = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(slots=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler
    persistence_policy: str = "full"
    redactor: ResultRedactor | None = None
    input_redactor: InputRedactor | None = None


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._cleanup_handlers: list[CleanupHandler] = []

    def register(self, tool: ToolDefinition) -> None:
        self._tools[tool.name] = tool

    def register_cleanup(self, handler: CleanupHandler) -> None:
        self._cleanup_handlers.append(handler)

    def has(self, tool_name: str) -> bool:
        return tool_name in self._tools

    def get(self, tool_name: str) -> ToolDefinition:
        if tool_name not in self._tools:
            raise KeyError(f"Unknown tool '{tool_name}'.")
        return self._tools[tool_name]

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def subset(self, tool_names: list[str] | None = None) -> "ToolRegistry":
        child = ToolRegistry()
        selected = tool_names or list(self._tools.keys())
        for name in selected:
            if name in self._tools:
                child.register(self._tools[name])
        return child

    def get_tools_schema(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            }
            for tool in self._tools.values()
        ]

    async def dispatch(self, tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        return await self.get(tool_name).handler(tool_input)

    def redact_result(self, tool_name: str, tool_input: dict[str, Any], tool_result: dict[str, Any]) -> dict[str, Any]:
        definition = self.get(tool_name)
        if definition.persistence_policy != "redacted" or definition.redactor is None:
            return tool_result
        return definition.redactor(tool_input, tool_result)

    def redact_input(self, tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        definition = self.get(tool_name)
        if definition.persistence_policy != "redacted" or definition.input_redactor is None:
            return tool_input
        return definition.input_redactor(tool_input)

    async def close(self) -> None:
        if not self._cleanup_handlers:
            return
        await asyncio.gather(*(handler() for handler in self._cleanup_handlers), return_exceptions=True)
