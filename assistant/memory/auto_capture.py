"""Automatic long-term memory promotion for stable user context."""

from __future__ import annotations

import json
import re

from assistant.agent.context import build_model_messages

DURABLE_HINT_PATTERNS = (
    re.compile(r"\bremember\b"),
    re.compile(r"\bsave (?:this|that|it|for later)\b"),
    re.compile(r"\bkeep (?:this|that|it) in mind\b"),
    re.compile(r"\bdon'?t forget\b"),
    re.compile(r"\bmy name is\b"),
    re.compile(r"\bcall me\b"),
    re.compile(r"\bi prefer\b"),
    re.compile(r"\bmy preferred\b"),
    re.compile(r"\bmy favorite\b"),
    re.compile(r"\bmy timezone is\b"),
    re.compile(r"\bi usually\b"),
    re.compile(r"\bi use\b"),
    re.compile(r"\bi live in\b"),
    re.compile(r"\bi am from\b"),
    re.compile(r"\bfor this project\b"),
    re.compile(r"\bfrom now on\b"),
    re.compile(r"\balways use\b"),
    re.compile(r"\bnever use\b"),
)
SECRET_HINTS = ("api key", "token", "client secret", "secret", "password", "passwd", "otp")


class MemoryAutoCaptureRunner:
    def __init__(
        self,
        config,
        model_provider,
        tool_registry,
        max_messages: int = 8,
        memory_classifier=None,
        ml_metrics_tracker=None,
    ) -> None:
        self.config = config
        self.model_provider = model_provider
        self.tool_registry = tool_registry
        self.max_messages = max_messages
        self.memory_classifier = memory_classifier
        self.ml_metrics_tracker = ml_metrics_tracker

    async def maybe_capture(
        self,
        session,
        system_prompt: str,
        latest_user_message: str,
        latest_assistant_message: str = "",
    ) -> None:
        if not getattr(self.config.memory, "auto_capture_enabled", True):
            return

        candidate = latest_user_message.strip()
        if not self._looks_like_durable_memory_candidate(candidate):
            return

        memory_tool = self._memory_write_tool()
        if memory_tool is None:
            return

        prompt = (
            f"{system_prompt}\n\n"
            "You are SonarBot's durable memory capture worker. Review the latest turn and save only stable user facts, "
            "preferences, recurring workflows, or long-lived project context into long-term memory. "
            "Never store secrets, credentials, tokens, passwords, one-off tasks, transient plans, or raw attachments. "
            "Use memory_write with memory_type='longterm'. Reuse existing headings when appropriate and keep content concise. "
            "If nothing qualifies, reply with NO_MEMORY."
        )
        decision_message = {
            "role": "user",
            "content": (
                "Check whether the latest turn contains durable memory worth saving.\n\n"
                f"Latest user message:\n{candidate}\n\n"
                f"Latest assistant reply:\n{latest_assistant_message.strip() or '[none]'}\n\n"
                "Only save stable identity, preferences, recurring routines, or important long-lived project context."
            ),
        }
        base_messages = build_model_messages(session.messages[-self.max_messages :])
        follow_up_messages: list[dict[str, str]] = []

        for _ in range(2):
            tool_calls = []
            async for response in self.model_provider.complete(
                messages=base_messages + [decision_message, *follow_up_messages],
                system=prompt,
                tools=[memory_tool],
                stream=False,
            ):
                if response.text and response.text.strip().upper() == "NO_MEMORY":
                    return
                if response.tool_calls:
                    tool_calls.extend(tool_call for tool_call in response.tool_calls if tool_call.name == "memory_write")

            if not tool_calls:
                return

            for tool_call in tool_calls:
                payload = dict(tool_call.arguments)
                payload["memory_type"] = "longterm"
                try:
                    result = await self.tool_registry.dispatch("memory_write", payload)
                except Exception as exc:  # pragma: no cover - defensive runtime path
                    result = {"error": str(exc), "tool_name": "memory_write"}
                follow_up_messages.append(
                    {
                        "role": "tool",
                        "name": "memory_write",
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

    def _looks_like_durable_memory_candidate(self, message: str) -> bool:
        if not message:
            return False
        normalized = re.sub(r"\s+", " ", message.lower()).strip()
        if normalized.startswith("/"):
            return False
        if any(secret_hint in normalized for secret_hint in SECRET_HINTS):
            return False
        if self.memory_classifier is not None:
            decision = self.memory_classifier.decide(normalized)
            if self.ml_metrics_tracker is not None:
                self.ml_metrics_tracker.record_memory_classifier(
                    keep=decision.keep,
                    confidence=decision.confidence,
                    reason=decision.reason,
                )
            if decision.keep:
                return True
        return any(pattern.search(normalized) for pattern in DURABLE_HINT_PATTERNS)

    def _memory_write_tool(self) -> dict | None:
        for tool in self.tool_registry.get_tools_schema():
            if tool["name"] == "memory_write":
                return tool
        return None
