"""Base channel abstractions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable


@dataclass(slots=True)
class ChannelMessage:
    sender_id: str
    channel: str
    text: str
    media_type: str | None = None
    media_path: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    raw_message: Any | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


InboundHandler = Callable[[ChannelMessage], Awaitable[str]]


class Channel(ABC):
    name: str

    def __init__(self, config, inbound_handler: InboundHandler) -> None:
        self.config = config
        self.inbound_handler = inbound_handler

    @abstractmethod
    async def start(self) -> None:
        """Begin receiving messages."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop channel processing."""

    @abstractmethod
    async def send_message(self, recipient_id: str, text: str) -> None:
        """Deliver a message to the recipient."""

    @abstractmethod
    async def send_typing(self, recipient_id: str) -> None:
        """Show typing state to the recipient."""

    async def handle_event(self, route_id: str, event_name: str, payload: dict[str, Any]) -> None:
        """Handle streamed events for this channel."""
