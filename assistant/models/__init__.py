"""Model provider exports."""

from assistant.models.base import ModelProvider, ModelResponse, ToolCall, UsageStats
from assistant.models.gemini_provider import GeminiProvider

__all__ = [
    "GeminiProvider",
    "ModelProvider",
    "ModelResponse",
    "ToolCall",
    "UsageStats",
    "get_provider",
]


def get_provider(config) -> GeminiProvider:
    return GeminiProvider(api_key=config.llm.gemini_api_key, model=config.agent.model)
