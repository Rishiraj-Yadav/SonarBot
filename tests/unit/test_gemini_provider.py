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
