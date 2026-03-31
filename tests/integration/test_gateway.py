from __future__ import annotations

import json

from fastapi.testclient import TestClient

from assistant.gateway.server import create_app
from assistant.models.base import ModelResponse
from tests.helpers import FakeProvider


def test_gateway_websocket_round_trip(app_config) -> None:
    provider = FakeProvider([[ModelResponse(text="Hello from SonarBot.", done=True)]])
    app = create_app(config=app_config, model_provider=provider)

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.send_text(
                json.dumps(
                    {
                        "type": "connect",
                        "device_id": "cli-test",
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
                        "params": {"message": "hello", "session_key": "main"},
                    }
                )
            )

            ack = json.loads(websocket.receive_text())
            assert ack["type"] == "res"
            assert ack["ok"] is True

            chunk = json.loads(websocket.receive_text())
            assert chunk["event"] == "agent.chunk"

            done = json.loads(websocket.receive_text())
            assert done["event"] == "agent.done"

    session_files = list((app_config.sessions_dir / "main").glob("*.jsonl"))
    assert session_files


def test_gateway_system_access_approval_preflight_allows_browser_requests(app_config) -> None:
    app = create_app(config=app_config, model_provider=FakeProvider([]))

    with TestClient(app) as client:
        response = client.options(
            "/api/system-access/approvals/test-approval-id",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )

    assert response.status_code in {200, 204}
    assert response.headers.get("access-control-allow-origin") in {"*", "http://localhost:3000"}
