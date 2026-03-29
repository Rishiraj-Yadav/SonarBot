"""Anthropic model provider implementation."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator
from uuid import uuid4

import httpx

from assistant.models.base import ModelProvider, ModelResponse, ToolCall, UsageStats
from assistant.utils import CircuitBreaker, async_retry, get_logger


class AnthropicProvider(ModelProvider):
    """Thin REST wrapper around the Anthropic messages endpoint."""

    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model
        self.circuit_breaker = CircuitBreaker(failure_threshold=5, reset_timeout_seconds=60)
        self.logger = get_logger("anthropic_provider", model=model)

    async def complete(
        self,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]],
        stream: bool = True,
    ) -> AsyncIterator[ModelResponse]:
        if not self.api_key:
            raise RuntimeError("Missing ANTHROPIC_API_KEY or llm.anthropic_api_key configuration.")
        if not self.circuit_breaker.allow_request():
            raise RuntimeError("LLM provider temporarily unavailable due to repeated failures. Please retry shortly.")

        payload = self._build_payload(messages=messages, system=system, tools=tools)
        url = "https://api.anthropic.com/v1/messages"
        try:
            data = await self._perform_request(url, payload)
            self.circuit_breaker.record_success()
        except Exception:
            self.circuit_breaker.record_failure()
            self.logger.exception("anthropic_request_failed")
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
            response = await client.post(
                url,
                json=payload,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                },
            )
            response.raise_for_status()
        return response.json()

    def _build_payload(
        self,
        *,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 2048,
            "messages": [item for item in (self._message_to_content(message) for message in messages) if item is not None],
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [
                {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "input_schema": tool.get("parameters", {"type": "object", "properties": {}}),
                }
                for tool in tools
            ]
        return payload

    def _message_to_content(self, message: dict[str, Any]) -> dict[str, Any] | None:
        role = str(message.get("role", "user"))
        content = message.get("content", "")
        if role == "tool":
            return {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": str(message.get("tool_call_id") or message.get("id") or uuid4()),
                        "content": str(content),
                    }
                ],
            }
        if isinstance(content, list):
            blocks: list[dict[str, Any]] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("type", "")).strip().lower()
                if item_type == "text":
                    blocks.append({"type": "text", "text": str(item.get("text", ""))})
                elif item_type == "image_url":
                    url = str((item.get("image_url") or {}).get("url", ""))
                    if url.startswith("data:") and ";base64," in url:
                        header, data = url.split(";base64,", 1)
                        media_type = header.removeprefix("data:") or "image/png"
                        blocks.append(
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": data,
                                },
                            }
                        )
            if not blocks:
                return None
            return {"role": "assistant" if role == "assistant" else "user", "content": blocks}
        text = str(content or "").strip()
        if not text and not message.get("tool_calls"):
            return None
        blocks: list[dict[str, Any]] = []
        if text:
            blocks.append({"type": "text", "text": text})
        for tool_call in message.get("tool_calls", []):
            if not isinstance(tool_call, dict):
                continue
            name = str(tool_call.get("name", "")).strip()
            if not name:
                continue
            blocks.append(
                {
                    "type": "tool_use",
                    "id": str(tool_call.get("id") or uuid4()),
                    "name": name,
                    "input": tool_call.get("arguments", {}) or {},
                }
            )
        if not blocks:
            return None
        return {"role": "assistant" if role == "assistant" else "user", "content": blocks}

    def _parse_response(self, data: dict[str, Any]) -> tuple[str, list[ToolCall], UsageStats | None]:
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in data.get("content", []):
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type", "")).lower()
            if block_type == "text":
                text_parts.append(str(block.get("text", "")))
            elif block_type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=str(block.get("id") or uuid4()),
                        name=str(block.get("name", "unknown_tool")),
                        arguments=block.get("input", {}) or {},
                    )
                )
        usage_raw = data.get("usage", {}) if isinstance(data.get("usage"), dict) else {}
        usage = UsageStats(
            input_tokens=int(usage_raw.get("input_tokens", 0)),
            output_tokens=int(usage_raw.get("output_tokens", 0)),
            total_tokens=int(usage_raw.get("input_tokens", 0)) + int(usage_raw.get("output_tokens", 0)),
        )
        return "".join(text_parts).strip(), tool_calls, usage

    async def complete_with_image(
        self,
        prompt: str,
        *,
        image_b64: str,
        image_mime: str = "image/png",
    ) -> dict[str, Any]:
        data = await self._perform_request(
            "https://api.anthropic.com/v1/messages",
            {
                "model": self.model,
                "max_tokens": 1024,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": image_mime,
                                    "data": image_b64,
                                },
                            },
                        ],
                    }
                ],
            },
        )
        text, tool_calls, usage = self._parse_response(data)
        return {"text": text, "tool_calls": tool_calls, "usage": usage}

    def _chunk_text(self, text: str, size: int = 120) -> list[str]:
        return [text[index : index + size] for index in range(0, len(text), size)] or [text]
