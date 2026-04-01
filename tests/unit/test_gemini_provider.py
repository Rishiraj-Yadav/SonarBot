from __future__ import annotations

import httpx
import pytest

from assistant.models.gemini_provider import GeminiProvider


def test_gemini_provider_summarizes_tool_call_history_without_raw_json() -> None:
    provider = GeminiProvider(api_key="fake-key", model="gemini-test")

    normalized = provider._normalize_message_text(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "tool-1", "name": "github_list_repos", "arguments": {}},
                {"id": "tool-2", "name": "memory_search", "arguments": {"query": "repo"}},
            ],
        }
    )

    assert normalized == "Assistant requested tool call(s): github_list_repos, memory_search"
    assert '"arguments"' not in normalized


def test_gemini_provider_serializes_tool_calls_as_structured_parts() -> None:
    provider = GeminiProvider(api_key="fake-key", model="gemini-test")

    content = provider._message_to_content(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "tool-1", "name": "gmail_search", "arguments": {"query": "in:inbox newer_than:3d"}}
            ],
        }
    )

    assert content is not None
    assert content["role"] == "model"
    assert content["parts"][0]["functionCall"]["name"] == "gmail_search"
    assert content["parts"][0]["functionCall"]["args"] == {"query": "in:inbox newer_than:3d"}


def test_gemini_provider_serializes_tool_results_as_function_response() -> None:
    provider = GeminiProvider(api_key="fake-key", model="gemini-test")

    content = provider._message_to_content(
        {
            "role": "tool",
            "name": "pdf_extract",
            "content": '{"pages": 2, "text": "Summary"}',
        }
    )

    assert content is not None
    assert content["role"] == "user"
    assert content["parts"][0]["functionResponse"]["name"] == "pdf_extract"
    assert content["parts"][0]["functionResponse"]["response"] == {"pages": 2, "text": "Summary"}


def test_gemini_provider_recovers_textual_tool_calls_from_model_text() -> None:
    provider = GeminiProvider(api_key="fake-key", model="gemini-test")

    tool_calls, cleaned = provider._recover_textual_tool_calls(
        """I will read the attached PDF file and summarize its content.

Tool calls requested: [{"id": "tool-1", "name": "pdf_extract", "arguments": {"path": "C:/tmp/doc.pdf"}}]
```"""
    )

    assert len(tool_calls) == 1
    assert tool_calls[0].name == "pdf_extract"
    assert tool_calls[0].arguments == {"path": "C:/tmp/doc.pdf"}
    assert "Tool calls requested" not in cleaned
    assert cleaned == "I will read the attached PDF file and summarize its content."


def test_gemini_provider_sanitizes_dynamic_object_schemas_for_gemini() -> None:
    provider = GeminiProvider(api_key="fake-key", model="gemini-test")

    sanitized = provider._sanitize_schema(
        {
            "type": "object",
            "properties": {
                "fields": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": "Map selectors to values.",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "minimum": 1,
                    "default": 10,
                },
            },
            "required": ["fields"],
        }
    )

    assert sanitized["type"] == "object"
    assert sanitized["required"] == ["fields"]
    assert sanitized["properties"]["fields"]["type"] == "object"
    assert "additionalProperties" not in sanitized["properties"]["fields"]
    assert "Map selectors to values." in sanitized["properties"]["fields"]["description"]
    assert "arbitrary string-keyed entries" in sanitized["properties"]["fields"]["description"]
    assert "minimum" not in sanitized["properties"]["timeout_seconds"]
    assert "default" not in sanitized["properties"]["timeout_seconds"]


def test_gemini_provider_drops_non_string_enums_from_schema() -> None:
    provider = GeminiProvider(api_key="fake-key", model="gemini-test")

    sanitized = provider._sanitize_schema(
        {
            "type": "object",
            "properties": {
                "count": {
                    "type": "integer",
                    "enum": [1, 2],
                    "default": 1,
                },
                "button": {
                    "type": "string",
                    "enum": ["left", "right"],
                },
            },
        }
    )

    assert "enum" not in sanitized["properties"]["count"]
    assert sanitized["properties"]["button"]["enum"] == ["left", "right"]


def test_gemini_provider_groups_consecutive_tool_results_into_one_user_turn() -> None:
    provider = GeminiProvider(api_key="fake-key", model="gemini-test")

    payload = provider._build_payload(
        messages=[
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "tool-1", "name": "desktop_keyboard_hotkey", "arguments": {"hotkey": "ctrl+s"}},
                    {"id": "tool-2", "name": "desktop_keyboard_type", "arguments": {"content": "R:/6_semester/test.txt"}},
                ],
            },
            {
                "role": "tool",
                "name": "desktop_keyboard_hotkey",
                "tool_call_id": "tool-1",
                "content": '{"status": "completed", "hotkey": "ctrl+s"}',
            },
            {
                "role": "tool",
                "name": "desktop_keyboard_type",
                "tool_call_id": "tool-2",
                "content": '{"status": "completed", "characters_typed": 22}',
            },
        ],
        system="test",
        tools=[],
    )

    assert len(payload["contents"]) == 2
    assert payload["contents"][0]["role"] == "model"
    assert payload["contents"][1]["role"] == "user"
    assert len(payload["contents"][1]["parts"]) == 2
    assert payload["contents"][1]["parts"][0]["functionResponse"]["name"] == "desktop_keyboard_hotkey"
    assert payload["contents"][1]["parts"][1]["functionResponse"]["name"] == "desktop_keyboard_type"


@pytest.mark.asyncio
async def test_gemini_provider_falls_back_on_model_400(monkeypatch) -> None:
    provider = GeminiProvider(api_key="fake-key", model="gemini-2.5-pro")
    calls: list[str] = []

    async def fake_request(model_name: str, payload: dict[str, object]) -> dict[str, object]:
        calls.append(model_name)
        if model_name == "gemini-2.5-pro":
            request = httpx.Request(
                "POST",
                f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent",
            )
            response = httpx.Response(400, request=request, text="model not available for this request")
            raise httpx.HTTPStatusError("bad model", request=request, response=response)
        return {
            "candidates": [{"content": {"parts": [{"text": "fallback ok"}]}}],
            "usageMetadata": {},
        }

    monkeypatch.setattr(provider, "_perform_request_for_model", fake_request)

    responses = []
    async for response in provider.complete(messages=[], system="hello", tools=[], stream=False):
        responses.append(response)

    assert calls == ["gemini-2.5-pro", "gemini-2.0-flash"]
    assert any(item.text == "fallback ok" for item in responses)
