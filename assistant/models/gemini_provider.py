"""Gemini model provider implementation."""

from __future__ import annotations

import json
import re
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

        try:
            data = await self._perform_request_with_fallback(payload)
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

    async def _perform_request_with_fallback(self, payload: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        candidate_models = self._candidate_models()
        for index, model_name in enumerate(candidate_models):
            try:
                return await self._perform_request_for_model(model_name, payload)
            except httpx.HTTPStatusError as exc:
                last_error = exc
                status = exc.response.status_code
                # A structural 400 (bad turn order) is caused by the payload
                # itself, not by the choice of model.  Sending the same broken
                # payload to the fallback model will also 400, and then burn
                # through that model's quota until it 429s.
                # Only allow model-fallback for 404 (model not found) errors.
                is_structural_400 = status == 400 and (
                    "function call turn" in exc.response.text.lower()
                    or "INVALID_ARGUMENT" in exc.response.text
                )
                if is_structural_400 or status not in {400, 404} or index == len(candidate_models) - 1:
                    raise
                self.logger.warning(
                    "gemini_model_fallback",
                    failed_model=model_name,
                    fallback_model=candidate_models[index + 1],
                    status_code=status,
                )
            except Exception as exc:
                last_error = exc
                raise
        if last_error is not None:
            raise last_error
        raise RuntimeError("Gemini request failed before a model could be selected.")

    @async_retry(max_attempts=3, base_delay=0.5)
    async def _perform_request_for_model(self, model_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, params={"key": self.api_key}, json=payload)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError:
                self.logger.error(
                    "gemini_http_error",
                    model=model_name,
                    status_code=response.status_code,
                    response_text=response.text[:2000],
                )
                raise
        return response.json()

    def _candidate_models(self) -> list[str]:
        candidates = [self.model, "gemini-2.0-flash"]
        unique: list[str] = []
        seen: set[str] = set()
        for item in candidates:
            normalized = str(item).strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            unique.append(normalized)
        return unique

    def _build_payload(
        self,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        contents = self._messages_to_contents(messages)
        # Guard against orphaned functionCall / functionResponse turns that
        # would cause a 400 INVALID_ARGUMENT from Gemini.
        contents = self._sanitize_contents(contents)

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
                            "parameters": self._sanitize_schema(tool["parameters"]),
                        }
                        for tool in tools
                    ]
                }
            ]
        return payload

    def _messages_to_contents(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        contents: list[dict[str, Any]] = []
        index = 0
        while index < len(messages):
            message = messages[index]
            role = message.get("role", "user")
            if role == "assistant" and message.get("tool_calls"):
                assistant_content = self._assistant_tool_call_content(message)
                if assistant_content is not None:
                    contents.append(assistant_content)
                index += 1
                tool_parts: list[dict[str, Any]] = []
                while index < len(messages) and messages[index].get("role") == "tool":
                    part = self._tool_response_part(messages[index])
                    if part is not None:
                        tool_parts.append(part)
                    index += 1
                if tool_parts:
                    contents.append({"role": "user", "parts": tool_parts})
                continue

            if role == "tool":
                content = self._tool_response_content(message)
            else:
                content = self._message_to_content(message)
            if content is not None:
                contents.append(content)
            index += 1
        return contents

    def _sanitize_contents(self, contents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Remove orphaned functionCall / functionResponse turns.

        Gemini enforces strict turn ordering:
          - A model turn with functionCall parts MUST be immediately followed
            by a user turn that contains only functionResponse parts.
          - A user turn with functionResponse parts MUST be immediately preceded
            by such a model turn.

        Violating either rule returns a 400 INVALID_ARGUMENT.  Orphaned turns
        can appear when session compaction cuts the history at a bad boundary.
        This method silently drops them and logs a warning so the root cause
        can be spotted in the logs.
        """
        if not contents:
            return contents

        def _has_fc(entry: dict[str, Any]) -> bool:
            return any("functionCall" in part for part in entry.get("parts", []))

        def _has_fr(entry: dict[str, Any]) -> bool:
            return any("functionResponse" in part for part in entry.get("parts", []))

        cleaned: list[dict[str, Any]] = []
        i = 0
        while i < len(contents):
            entry = contents[i]
            role = entry.get("role", "")

            # user turn carrying functionResponse parts must be immediately
            # preceded by a model turn that has functionCall parts.
            if role == "user" and _has_fr(entry):
                prev = cleaned[-1] if cleaned else None
                if prev is None or not _has_fc(prev):
                    self.logger.warning(
                        "gemini_orphaned_function_response_dropped", index=i
                    )
                    i += 1
                    continue

            # model turn carrying functionCall parts must be immediately
            # followed by a user turn that has functionResponse parts.
            if role == "model" and _has_fc(entry):
                nxt = contents[i + 1] if i + 1 < len(contents) else None
                if nxt is None or not _has_fr(nxt):
                    self.logger.warning(
                        "gemini_orphaned_function_call_dropped", index=i
                    )
                    i += 1
                    continue

            cleaned.append(entry)
            i += 1

        # Gemini requires the conversation to start with a user turn.
        while cleaned and cleaned[0].get("role") != "user":
            self.logger.warning(
                "gemini_dropping_non_user_first_turn", role=cleaned[0].get("role")
            )
            cleaned.pop(0)

        return cleaned

    def _sanitize_schema(self, schema: Any) -> Any:
        if isinstance(schema, list):
            return [self._sanitize_schema(item) for item in schema]
        if not isinstance(schema, dict):
            return schema

        description = str(schema.get("description", "")).strip()
        schema_type = schema.get("type")
        sanitized: dict[str, Any] = {}

        if schema_type is not None:
            sanitized["type"] = schema_type
        if description:
            sanitized["description"] = description

        if "enum" in schema and isinstance(schema["enum"], list):
            enum_values = schema["enum"]
            if all(isinstance(item, str) for item in enum_values):
                sanitized["enum"] = enum_values
        if "format" in schema and schema["format"]:
            sanitized["format"] = schema["format"]
        if "required" in schema and isinstance(schema["required"], list):
            sanitized["required"] = schema["required"]

        if "properties" in schema and isinstance(schema["properties"], dict):
            sanitized["properties"] = {
                key: self._sanitize_schema(value)
                for key, value in schema["properties"].items()
                if isinstance(key, str)
            }

        if "items" in schema:
            sanitized["items"] = self._sanitize_schema(schema["items"])

        additional_properties = schema.get("additionalProperties")
        if schema_type == "object" and additional_properties and "properties" not in sanitized:
            extra_description = self._describe_additional_properties(additional_properties)
            if extra_description:
                base = sanitized.get("description", "")
                sanitized["description"] = (
                    f"{base} {extra_description}".strip()
                    if base
                    else extra_description
                )

        return sanitized

    def _describe_additional_properties(self, schema: Any) -> str:
        if isinstance(schema, bool):
            return "Accepts key-value pairs." if schema else ""
        if not isinstance(schema, dict):
            return ""
        value_type = str(schema.get("type", "value")).strip() or "value"
        return f"Accepts arbitrary string-keyed entries where each value is a {value_type}."

    def _message_to_content(self, message: dict[str, Any]) -> dict[str, Any] | None:
        role = message.get("role", "user")
        if role == "assistant" and message.get("tool_calls"):
            return self._assistant_tool_call_content(message)
        if role == "tool":
            return self._tool_response_content(message)

        text = self._normalize_message_text(message)
        if not text:
            return None
        gemini_role = "model" if role == "assistant" else "user"
        return {"role": gemini_role, "parts": [{"text": text}]}

    def _assistant_tool_call_content(self, message: dict[str, Any]) -> dict[str, Any] | None:
        parts: list[dict[str, Any]] = []
        content = str(message.get("content", "")).strip()
        if content:
            parts.append({"text": content})
        for tool_call in message.get("tool_calls", []):
            if not isinstance(tool_call, dict):
                continue
            name = str(tool_call.get("name", "")).strip()
            if not name:
                continue
            parts.append(
                {
                    "functionCall": {
                        "name": name,
                        "args": tool_call.get("arguments", {}) or {},
                    }
                }
            )
        if not parts:
            return None
        return {"role": "model", "parts": parts}

    def _tool_response_content(self, message: dict[str, Any]) -> dict[str, Any] | None:
        part = self._tool_response_part(message)
        if part is None:
            return None
        return {"role": "user", "parts": [part]}

    def _tool_response_part(self, message: dict[str, Any]) -> dict[str, Any] | None:
        name = str(message.get("name", "tool")).strip() or "tool"
        content = str(message.get("content", "")).strip()
        if not content:
            return None
        try:
            response_payload = json.loads(content)
        except json.JSONDecodeError:
            response_payload = {"content": content}
        return {
            "functionResponse": {
                "name": name,
                "response": response_payload,
            }
        }

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

        text = "".join(text_parts).strip()
        recovered_calls, cleaned_text = self._recover_textual_tool_calls(text)
        if recovered_calls:
            tool_calls.extend(recovered_calls)
            text = cleaned_text

        return text, tool_calls, usage

    def _recover_textual_tool_calls(self, text: str) -> tuple[list[ToolCall], str]:
        if "Tool calls requested:" not in text:
            return [], text

        pattern = re.compile(r"Tool calls requested:\s*(\[[\s\S]*?\])\s*(```)?", re.IGNORECASE)
        match = pattern.search(text)
        if match is None:
            return [], text

        raw_payload = match.group(1).strip()
        try:
            parsed = json.loads(raw_payload)
        except json.JSONDecodeError:
            return [], text
        if not isinstance(parsed, list):
            return [], text

        recovered: list[ToolCall] = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            arguments = item.get("arguments", {}) or {}
            if not name or not isinstance(arguments, dict):
                continue
            recovered.append(
                ToolCall(
                    id=str(item.get("id") or uuid4()),
                    name=name,
                    arguments=arguments,
                )
            )
        if not recovered:
            return [], text

        cleaned = (text[: match.start()] + text[match.end() :]).strip()
        cleaned = cleaned.removesuffix("```").strip()
        return recovered, cleaned

    def _chunk_text(self, text: str, size: int = 120) -> list[str]:
        return [text[index : index + size] for index in range(0, len(text), size)] or [text]
