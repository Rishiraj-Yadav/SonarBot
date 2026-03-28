"""Model provider exports and routing."""

from __future__ import annotations

from typing import Any

from assistant.models.anthropic_provider import AnthropicProvider
from assistant.models.base import ModelProvider, ModelResponse, ToolCall, UsageStats
from assistant.models.gemini_provider import GeminiProvider

__all__ = [
    "AnthropicProvider",
    "GeminiProvider",
    "ModelProvider",
    "ModelResponse",
    "ToolCall",
    "UsageStats",
    "ProviderRouter",
    "get_provider",
]


class ProviderRouter(ModelProvider):
    def __init__(self, primary: ModelProvider, *, cheap: ModelProvider | None = None) -> None:
        self.primary = primary
        self.cheap = cheap or primary
        self.model = getattr(primary, "model", "")

    def get_task_provider(self, requested_model: str | None = None) -> ModelProvider:
        lowered = str(requested_model or "").strip().lower()
        if lowered == "cheap":
            return self.cheap
        return self.primary

    async def complete(
        self,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]],
        stream: bool = True,
    ):
        async for item in self.primary.complete(messages=messages, system=system, tools=tools, stream=stream):
            yield item

    async def complete_with_image(
        self,
        prompt: str,
        *,
        image_b64: str,
        image_mime: str = "image/png",
        requested_model: str | None = None,
    ) -> dict[str, Any]:
        provider = self.get_task_provider(requested_model)
        image_helper = getattr(provider, "complete_with_image", None)
        if not callable(image_helper):
            image_helper = getattr(self.primary, "complete_with_image", None)
        if not callable(image_helper):
            raise RuntimeError("The configured model provider does not support image inputs.")
        return await image_helper(prompt, image_b64=image_b64, image_mime=image_mime)


def get_provider(config) -> ModelProvider:
    model_name = str(config.agent.model or "").strip()
    if model_name.lower().startswith("claude"):
        primary: ModelProvider = AnthropicProvider(api_key=config.llm.anthropic_api_key, model=model_name)
    else:
        primary = GeminiProvider(api_key=config.llm.gemini_api_key, model=model_name)

    cheap_provider: ModelProvider | None = None
    anthropic_key = str(getattr(config.llm, "anthropic_api_key", "") or "").strip()
    if anthropic_key:
        cheap_provider = AnthropicProvider(api_key=anthropic_key, model="claude-3-5-haiku-latest")
    return ProviderRouter(primary, cheap=cheap_provider)
