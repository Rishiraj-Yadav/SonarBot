"""Helpers for turning session state into model-ready messages."""

from __future__ import annotations

from typing import Any


def build_model_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    model_messages: list[dict[str, Any]] = []
    for message in messages:
        model_messages.append(
            {
                "role": message.get("role", "user"),
                "content": message.get("content", ""),
                "name": message.get("name"),
                "tool_call_id": message.get("tool_call_id"),
                "tool_calls": message.get("tool_calls", []),
            }
        )
    return model_messages
