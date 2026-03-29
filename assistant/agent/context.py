"""Helpers for turning session state into model-ready messages."""

from __future__ import annotations

import json
from typing import Any


def _looks_like_provider_error(text: str) -> bool:
    lowered = text.strip().lower()
    return lowered.startswith("[model error]") or (
        "generativelanguage.googleapis.com" in lowered
        or "developer.mozilla.org/en-us/docs/web/http/status/" in lowered
        or "client error '400 bad request'" in lowered
        or "resource_exhausted" in lowered
    )


def _looks_like_browser_failure(text: str) -> bool:
    lowered = text.strip().lower()
    if not lowered.startswith("i couldn't finish that browser task"):
        return False
    return True


def should_skip_from_model(message: dict[str, Any]) -> bool:
    role = str(message.get("role", "")).strip().lower()
    content = str(message.get("content", "")).strip()
    if not content:
        return False
    if role == "assistant" and _looks_like_provider_error(content):
        return True
    if role == "assistant" and _looks_like_browser_failure(content):
        return True
    return False


def build_model_messages(
    messages: list[dict[str, Any]],
    *,
    max_messages: int | None = None,
) -> list[dict[str, Any]]:
    model_messages: list[dict[str, Any]] = []
    for message in messages:
        if should_skip_from_model(message):
            continue
        model_messages.append(
            {
                "role": message.get("role", "user"),
                "content": message.get("content", ""),
                "name": message.get("name"),
                "tool_call_id": message.get("tool_call_id"),
                "tool_calls": message.get("tool_calls", []),
            }
        )

    if max_messages is None or len(model_messages) <= max_messages:
        return model_messages

    leading_summaries: list[dict[str, Any]] = []
    index = 0
    while index < len(model_messages):
        message = model_messages[index]
        if str(message.get("role", "")).strip().lower() == "system" and str(message.get("content", "")).startswith("[SUMMARY]:"):
            leading_summaries.append(message)
            index += 1
            continue
        break

    tail_budget = max(1, max_messages - len(leading_summaries))
    tail = model_messages[-tail_budget:]
    if tail and str(tail[0].get("role", "")).strip().lower() == "tool":
        previous_index = len(model_messages) - tail_budget - 1
        if previous_index >= 0:
            tail = [model_messages[previous_index], *tail]
    return [*leading_summaries, *tail]


def estimate_model_payload_size(messages: list[dict[str, Any]], system_prompt: str) -> int:
    total = len(system_prompt)
    for message in messages:
        total += len(json.dumps(message, ensure_ascii=False))
    return total
