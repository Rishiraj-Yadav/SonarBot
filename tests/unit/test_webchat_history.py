from __future__ import annotations

from assistant.gateway.server import _format_webchat_history


def test_format_webchat_history_hides_tool_records_and_empty_messages() -> None:
    messages = [
        {"id": "1", "role": "user", "content": "hello"},
        {"id": "2", "role": "tool", "content": '{"result": true}'},
        {"id": "3", "role": "assistant", "content": ""},
        {"id": "4", "role": "assistant", "content": "Hi there"},
    ]

    formatted = _format_webchat_history(messages)

    assert formatted == [
        {"id": "1", "role": "user", "content": "hello", "created_at": None},
        {"id": "4", "role": "assistant", "content": "Hi there", "created_at": None},
    ]
