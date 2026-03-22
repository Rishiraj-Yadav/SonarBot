from __future__ import annotations

from assistant.models.base import ModelResponse


class FakeProvider:
    def __init__(self, scripted_responses: list[list[ModelResponse]]) -> None:
        self.scripted_responses = scripted_responses
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, system, tools, stream=True):
        self.calls.append({"messages": messages, "system": system, "tools": tools, "stream": stream})
        responses = self.scripted_responses.pop(0) if self.scripted_responses else [ModelResponse(done=True)]
        for response in responses:
            yield response
