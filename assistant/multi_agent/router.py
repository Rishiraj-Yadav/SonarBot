"""Basic capability and mention based routing helpers."""

from __future__ import annotations


class MessageRouter:
    def route(self, message: str, known_agents: list[str]) -> str:
        lowered = message.lower()
        for agent_name in known_agents:
            mention = f"@{agent_name.lower()}"
            if mention in lowered:
                return agent_name
        if "sub_agent_result" in lowered:
            return "parent"
        return "main"
