from __future__ import annotations

import pytest

from assistant.models import ProviderRouter
from assistant.models.base import ModelResponse
from assistant.tools.llm_task_tool import build_llm_task_tool


class FakeProvider:
    def __init__(self, label: str) -> None:
        self.label = label

    async def complete(self, messages, system, tools, stream=True):
        yield ModelResponse(text=self.label, done=True)


@pytest.mark.asyncio
async def test_llm_task_uses_cheap_provider_when_available() -> None:
    router = ProviderRouter(primary=FakeProvider("primary"), cheap=FakeProvider("cheap"))
    tool = build_llm_task_tool(router)

    result = await tool.handler({"prompt": "classify this", "model": "cheap"})

    assert result["content"] == "cheap"


@pytest.mark.asyncio
async def test_llm_task_falls_back_to_primary_provider_for_default_model() -> None:
    router = ProviderRouter(primary=FakeProvider("primary"), cheap=FakeProvider("cheap"))
    tool = build_llm_task_tool(router)

    result = await tool.handler({"prompt": "summarize this", "model": "default"})

    assert result["content"] == "primary"
