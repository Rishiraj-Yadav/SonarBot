"""Gemini model provider implementation."""

from __future__ import annotations

from typing import Any, AsyncIterator
from uuid import uuid4

import httpx

from assistant.models.base import ModelProvider, ModelResponse, ToolCall, UsageStats
from assistant.utils import CircuitBreaker, async_retry, get_logger


class GeminiProvider(ModelProvider):
    """Thin REST wrapper around the Gemini generateContent endpoint."""

    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model
        self.circuit_breaker = CircuitBreaker(failure_threshold=5, reset_timeout_seconds=60)
        self.logger = get_logger("gemini_provider", model=model)

    async def complete(
        self,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]],
        stream: bool = True,
    ) -> AsyncIterator[ModelResponse]:
        if not self.api_key:
            raise RuntimeError("Missing GEMINI_API_KEY or llm.gemini_api_key configuration.")
        if not self.circuit_breaker.allow_request():
            raise RuntimeError("LLM provider temporarily unavailable due to repeated failures. Please retry shortly.")

        payload = self._build_payload(messages=messages, system=system, tools=tools)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"

        try:
            data = await self._perform_request(url, payload)
            self.circuit_breaker.record_success()
        except Exception:
            self.circuit_breaker.record_failure()
            self.logger.exception("gemini_request_failed")
            raise
        text, tool_calls, usage = self._parse_response(data)
        if text:
            if stream:
                for chunk in self._chunk_text(text):
                    yield ModelResponse(text=chunk)
            else:
                yield ModelResponse(text=text)

        yield ModelResponse(tool_calls=tool_calls, usage=usage, done=True)

    @async_retry(max_attempts=3, base_delay=0.5)
    async def _perform_request(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, params={"key": self.api_key}, json=payload)
            response.raise_for_status()
        return response.json()

    def _build_payload(
        self,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        contents = []
        for message in messages:
            content = self._message_to_content(message)
            if content is not None:
                contents.append(content)

        payload: dict[str, Any] = {
            "contents": contents,
        }
        if system:
            payload["system_instruction"] = {"parts": [{"text": system}]}
        if tools:
            payload["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": tool["name"],
                            "description": tool["description"],
                            "parameters": tool["parameters"],
                        }
                        for tool in tools
                    ]
                }
            ]
        return payload

    def _message_to_content(self, message: dict[str, Any]) -> dict[str, Any] | None:
        role = message.get("role", "user")
        text = self._normalize_message_text(message)
        if not text:
            return None

        gemini_role = "model" if role == "assistant" else "user"
        return {"role": gemini_role, "parts": [{"text": text}]}

    def _normalize_message_text(self, message: dict[str, Any]) -> str:
        role = message.get("role", "user")
        content = str(message.get("content", "")).strip()
        if role == "tool":
            name = message.get("name", "tool")
            return f"Tool result from {name}:\n{content}"
        if message.get("tool_calls") and not content:
            tool_names = [
                str(tool_call.get("name", "tool"))
                for tool_call in message.get("tool_calls", [])
                if isinstance(tool_call, dict)
            ]
            if tool_names:
                return f"Assistant requested tool call(s): {', '.join(tool_names)}"
            return "Assistant requested a tool call."
        return content

    def _parse_response(self, data: dict[str, Any]) -> tuple[str, list[ToolCall], UsageStats | None]:
        candidates = data.get("candidates", [])
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        for candidate in candidates:
            parts = (
                candidate.get("content", {}).get("parts", [])
                if isinstance(candidate.get("content"), dict)
                else []
            )
            for part in parts:
                if "text" in part:
                    text_parts.append(part["text"])
                function_call = part.get("functionCall") or part.get("function_call")
                if function_call:
                    tool_calls.append(
                        ToolCall(
                            id=str(uuid4()),
                            name=function_call.get("name", "unknown_tool"),
                            arguments=function_call.get("args", {}) or {},
                        )
                    )

        usage_data = data.get("usageMetadata", {})
        usage = UsageStats(
            input_tokens=int(usage_data.get("promptTokenCount", 0)),
            output_tokens=int(usage_data.get("candidatesTokenCount", 0)),
            total_tokens=int(usage_data.get("totalTokenCount", 0)),
        )

        return "".join(text_parts).strip(), tool_calls, usage

    def _chunk_text(self, text: str, size: int = 120) -> list[str]:
        return [text[index : index + size] for index in range(0, len(text), size)] or [text]
