"""Manage active WebSocket connections."""

from __future__ import annotations

from dataclasses import dataclass
from collections import deque
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import WebSocket

from assistant.gateway.protocol import EventFrame, ResponseFrame


@dataclass(slots=True)
class ConnectionContext:
    connection_id: str
    device_id: str
    websocket: WebSocket
    connected_at: str


@dataclass(slots=True)
class ChannelRoute:
    route_id: str
    channel_name: str
    sender_id: str
    recipient_id: str
    metadata: dict[str, Any]


class ConnectionManager:
    def __init__(self, rate_limit_per_minute: int = 10) -> None:
        self._connections: dict[str, ConnectionContext] = {}
        self._channels: dict[str, Any] = {}
        self._channel_routes: dict[str, ChannelRoute] = {}
        self._sender_channels: dict[str, str] = {}
        self.rate_limit_per_minute = rate_limit_per_minute
        self._request_windows: dict[str, deque[float]] = {}

    async def connect(self, websocket: WebSocket, device_id: str) -> ConnectionContext:
        connection = ConnectionContext(
            connection_id=uuid4().hex,
            device_id=device_id,
            websocket=websocket,
            connected_at=datetime.now(timezone.utc).isoformat(),
        )
        self._connections[connection.connection_id] = connection
        return connection

    async def disconnect(self, connection_id: str) -> None:
        self._connections.pop(connection_id, None)

    async def disconnect_websocket(self, websocket: WebSocket) -> None:
        for connection_id, context in list(self._connections.items()):
            if context.websocket is websocket:
                await self.disconnect(connection_id)

    async def close_all(self) -> None:
        for context in list(self._connections.values()):
            try:
                await context.websocket.close()
            except Exception:
                pass
        self._connections.clear()

    def register_channel(self, channel: Any) -> None:
        self._channels[channel.name] = channel

    def register_channel_route(
        self,
        channel_name: str,
        sender_id: str,
        recipient_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        route_id = uuid4().hex
        self._channel_routes[route_id] = ChannelRoute(
            route_id=route_id,
            channel_name=channel_name,
            sender_id=sender_id,
            recipient_id=recipient_id,
            metadata=metadata or {},
        )
        self._sender_channels[sender_id] = channel_name
        return route_id

    async def send_response(self, connection_id: str, response: ResponseFrame) -> None:
        context = self._connections.get(connection_id)
        if context is None:
            return
        try:
            await context.websocket.send_text(response.model_dump_json(exclude_none=True))
        except Exception:
            await self.disconnect(connection_id)

    async def send_event(self, connection_id: str, event_name: str, payload: dict[str, Any]) -> None:
        context = self._connections.get(connection_id)
        if context is not None:
            frame = EventFrame(event=event_name, payload=payload)
            try:
                await context.websocket.send_text(frame.model_dump_json(exclude_none=True))
            except Exception:
                await self.disconnect(connection_id)
            return

        route = self._channel_routes.get(connection_id)
        if route is None:
            return
        channel = self._channels.get(route.channel_name)
        if channel is None:
            return
        await channel.handle_event(connection_id, event_name, payload)

    async def send_typing(self, connection_id: str) -> None:
        route = self._channel_routes.get(connection_id)
        if route is None:
            return
        channel = self._channels.get(route.channel_name)
        if channel is None:
            return
        await channel.send_typing(route.recipient_id)

    def active_count(self) -> int:
        return len(self._connections)

    def active_channels(self) -> list[str]:
        return sorted(self._channels.keys())

    def allow_request(self, connection_id: str) -> bool:
        context = self._connections.get(connection_id)
        if context is None:
            return True
        device_id = context.device_id
        now = datetime.now(timezone.utc).timestamp()
        window = self._request_windows.setdefault(device_id, deque())
        while window and (now - window[0]) > 60:
            window.popleft()
        if len(window) >= self.rate_limit_per_minute:
            return False
        window.append(now)
        return True
