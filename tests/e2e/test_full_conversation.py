from __future__ import annotations

import json

from fastapi.testclient import TestClient

from assistant.gateway.server import create_app
from assistant.models.base import ModelResponse, ToolCall
from tests.helpers import FakeProvider


def test_full_conversation_persists_tool_usage(app_config) -> None:
    provider = FakeProvider(
        [
            [ModelResponse(tool_calls=[ToolCall(id="tool-1", name="read_file", arguments={"path": "TOOLS.md"})], done=True)],
            [ModelResponse(text="I read the file successfully.", done=True)],
        ]
    )
    app = create_app(config=app_config, model_provider=provider)

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.send_text(
                json.dumps(
                    {
                        "type": "connect",
                        "device_id": "e2e-cli",
                        "auth": {"token": app_config.gateway.token},
                    }
                )
            )
            hello = json.loads(websocket.receive_text())
            assert hello["type"] == "hello-ok"

            websocket.send_text(
                json.dumps(
                    {
                        "type": "req",
                        "id": "req-1",
                        "method": "agent.send",
                        "params": {"message": "Read TOOLS.md", "session_key": "main"},
                    }
                )
            )

            ack = json.loads(websocket.receive_text())
            assert ack["type"] == "res"
            assert ack["ok"] is True

            events = []
            while True:
                frame = json.loads(websocket.receive_text())
                if frame.get("type") == "event":
                    events.append(frame)
                if frame.get("type") == "event" and frame.get("event") == "agent.done":
                    break

    assert any(event["event"] == "agent.chunk" and "read the file" in event["payload"]["text"].lower() for event in events)

    session_files = list((app_config.sessions_dir / "main").glob("*.jsonl"))
    assert session_files
    session_lines = [json.loads(line) for line in session_files[0].read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(line.get("role") == "tool" and line.get("name") == "read_file" for line in session_lines)
    assert any("I read the file successfully." in line.get("content", "") for line in session_lines)
