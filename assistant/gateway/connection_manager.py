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
    channel_name: str = "ws"
    user_id: str = ""


@dataclass(slots=True)
class ChannelRoute:
    route_id: str
    channel_name: str
    sender_id: str
    recipient_id: str
    user_id: str
    metadata: dict[str, Any]


class ConnectionManager:
    def __init__(self, rate_limit_per_minute: int = 10) -> None:
        self._connections: dict[str, ConnectionContext] = {}
        self._channels: dict[str, Any] = {}
        self._channel_routes: dict[str, ChannelRoute] = {}
        self._sender_channels: dict[str, str] = {}
        self._user_connections: dict[str, set[str]] = {}
        self._user_routes: dict[str, set[str]] = {}
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
        context = self._connections.pop(connection_id, None)
        if context is not None and context.user_id:
            connection_ids = self._user_connections.get(context.user_id)
            if connection_ids is not None:
                connection_ids.discard(connection_id)
                if not connection_ids:
                    self._user_connections.pop(context.user_id, None)

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

    def get_channel(self, channel_name: str) -> Any | None:
        return self._channels.get(channel_name)

    def register_channel_route(
        self,
        channel_name: str,
        sender_id: str,
        recipient_id: str,
        user_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        route_id = uuid4().hex
        self._channel_routes[route_id] = ChannelRoute(
            route_id=route_id,
            channel_name=channel_name,
            sender_id=sender_id,
            recipient_id=recipient_id,
            user_id=user_id,
            metadata=metadata or {},
        )
        self._sender_channels[sender_id] = channel_name
        self._user_routes.setdefault(user_id, set()).add(route_id)
        return route_id

    def bind_connection(self, connection_id: str, *, user_id: str, channel_name: str) -> None:
        context = self._connections.get(connection_id)
        if context is None:
            return
        context.user_id = user_id
        context.channel_name = channel_name
        self._user_connections.setdefault(user_id, set()).add(connection_id)

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

    async def send_channel_message(self, channel_name: str, recipient_id: str, text: str) -> bool:
        channel = self._channels.get(channel_name)
        if channel is None:
            return False
        await channel.send_message(recipient_id, text)
        return True

    async def send_user_event(
        self,
        user_id: str,
        event_name: str,
        payload: dict[str, Any],
        *,
        channel_name: str | None = None,
    ) -> int:
        sent = 0
        for connection_id in list(self._user_connections.get(user_id, set())):
            context = self._connections.get(connection_id)
            if context is None:
                continue
            if channel_name is not None and context.channel_name != channel_name:
                continue
            await self.send_event(connection_id, event_name, payload)
            sent += 1
        return sent

    def active_user_connections(self, user_id: str, channel_name: str | None = None) -> list[str]:
        connection_ids = []
        for connection_id in self._user_connections.get(user_id, set()):
            context = self._connections.get(connection_id)
            if context is None:
                continue
            if channel_name is not None and context.channel_name != channel_name:
                continue
            connection_ids.append(connection_id)
        return connection_ids

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

    def get_connection(self, connection_id: str) -> ConnectionContext | None:
        return self._connections.get(connection_id)
