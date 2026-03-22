"""Multi-agent orchestration support."""

from assistant.multi_agent.presence import PresenceRegistry
from assistant.multi_agent.router import MessageRouter
from assistant.multi_agent.sub_agent import SubAgentHandle, SubAgentManager

__all__ = ["MessageRouter", "PresenceRegistry", "SubAgentHandle", "SubAgentManager"]
