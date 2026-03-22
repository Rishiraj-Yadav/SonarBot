"""WebSocket client helpers for the CLI."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import websockets

from assistant.config.schema import AppConfig


@dataclass(slots=True)
class GatewayClient:
    config: AppConfig
    device_id: str = "cli-local"
    _socket: Any | None = field(init=False, default=None, repr=False)

    async def __aenter__(self) -> "GatewayClient":
        self._socket = await websockets.connect(
            f"ws://{self.config.gateway.host}:{self.config.gateway.port}/ws"
        )
        await self._socket.send(
            json.dumps(
                {
                    "type": "connect",
                    "device_id": self.device_id,
                    "auth": {"token": self.config.gateway.token},
                }
            )
        )
        response = json.loads(await self._socket.recv())
        if response.get("type") != "hello-ok":
            raise RuntimeError(f"Handshake failed: {response}")
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._socket is not None:
            await self._socket.close()
            self._socket = None

    async def send_request(self, method: str, params: dict[str, Any] | None = None) -> str:
        request_id = uuid4().hex
        await self._socket.send(
            json.dumps(
                {
                    "type": "req",
                    "id": request_id,
                    "method": method,
                    "params": params or {},
                }
            )
        )
        return request_id

    async def recv(self) -> dict[str, Any]:
        return json.loads(await self._socket.recv())
