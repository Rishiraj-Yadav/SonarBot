from __future__ import annotations

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
