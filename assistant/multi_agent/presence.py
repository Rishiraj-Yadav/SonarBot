"""Track running agent presence and status."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class AgentPresence:
    agent_name: str
    session_key: str
    status: str = "idle"
    capabilities: list[str] = field(default_factory=list)


class PresenceRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, AgentPresence] = {}

    def register(self, agent_name: str, session_key: str, capabilities: list[str] | None = None) -> None:
        self._agents[agent_name] = AgentPresence(
            agent_name=agent_name,
            session_key=session_key,
            capabilities=list(capabilities or []),
        )

    def update(self, agent_name: str, status: str) -> None:
        if agent_name in self._agents:
            self._agents[agent_name].status = status

    def unregister(self, agent_name: str) -> None:
        self._agents.pop(agent_name, None)

    def snapshot(self) -> list[dict[str, Any]]:
        return [
            {
                "agent_name": item.agent_name,
                "session_key": item.session_key,
                "status": item.status,
                "capabilities": item.capabilities,
            }
            for item in self._agents.values()
        ]

    def active_count(self) -> int:
        return len(self._agents)
