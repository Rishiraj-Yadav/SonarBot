from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest
import uvicorn
import websockets

from assistant.gateway.server import create_app
from assistant.models.base import ModelResponse


class EchoProvider:
    async def complete(self, messages, system, tools, stream=True):
        last_user_message = next(
            (message["content"] for message in reversed(messages) if message.get("role") == "user"),
            "",
        )
        yield ModelResponse(text=f"Echo: {last_user_message}", done=True)


@pytest.mark.asyncio
async def test_gateway_handles_ten_concurrent_clients(app_config, unused_tcp_port: int) -> None:
    app = create_app(config=app_config, model_provider=EchoProvider())
    config = uvicorn.Config(app, host="127.0.0.1", port=unused_tcp_port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    try:
        for _ in range(50):
            if server.started:
                break
            time.sleep(0.1)
        assert server.started

        async def run_client(index: int) -> str:
            async with websockets.connect(f"ws://127.0.0.1:{unused_tcp_port}/ws") as websocket:
                await websocket.send(
                    json.dumps(
                        {
                            "type": "connect",
                            "device_id": f"load-client-{index}",
                            "auth": {"token": app_config.gateway.token},
                        }
                    )
                )
                hello = json.loads(await websocket.recv())
                assert hello["type"] == "hello-ok"

                await websocket.send(
                    json.dumps(
                        {
                            "type": "req",
                            "id": f"req-{index}",
                            "method": "agent.send",
                            "params": {"message": f"hello {index}", "session_key": f"load-{index}"},
                        }
                    )
                )
                await websocket.recv()  # ack
                chunks: list[str] = []
                while True:
                    frame = json.loads(await websocket.recv())
                    if frame.get("type") == "event" and frame.get("event") == "agent.chunk":
                        chunks.append(frame["payload"]["text"])
                    if frame.get("type") == "event" and frame.get("event") == "agent.done":
                        break
                return "".join(chunks)

        results = await asyncio.gather(*(run_client(index) for index in range(10)))
        assert len(results) == 10
        assert len(set(results)) == 10
    finally:
        server.should_exit = True
        thread.join(timeout=5)
