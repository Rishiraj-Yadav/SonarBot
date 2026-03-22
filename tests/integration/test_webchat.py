from __future__ import annotations

import json

from fastapi.testclient import TestClient

from assistant.gateway.server import create_app
from assistant.models.base import ModelResponse
from tests.helpers import FakeProvider


def test_webchat_endpoint_streams_chunks(app_config) -> None:
    provider = FakeProvider([[ModelResponse(text="Hello from webchat.", done=True)]])
    app = create_app(config=app_config, model_provider=provider)

    with TestClient(app) as client:
        with client.websocket_connect("/webchat/ws") as websocket:
            websocket.send_text(
                json.dumps(
                    {
                        "type": "req",
                        "id": "web-1",
                        "method": "agent.send",
                        "params": {"message": "hello from browser"},
                    }
                )
            )

            ack = json.loads(websocket.receive_text())
            assert ack["type"] == "res"
            assert ack["ok"] is True

            chunk = json.loads(websocket.receive_text())
            assert chunk["event"] == "agent.chunk"
            assert "Hello from webchat." in chunk["payload"]["text"]

            done = json.loads(websocket.receive_text())
            assert done["event"] == "agent.done"

        history = client.get("/webchat/history?session_key=main&limit=10")
        assert history.status_code == 200
        payload = history.json()
        assert payload["session_key"] == "webchat_main"
        assert any(message["role"] == "assistant" for message in payload["messages"])


def test_webchat_oauth_connect_is_returned_as_single_clean_response(app_config) -> None:
    app_config.oauth.github.client_id = "github-client"
    app_config.oauth.github.client_secret = "github-secret"
    provider = FakeProvider([[ModelResponse(done=True)]])
    app = create_app(config=app_config, model_provider=provider)

    with TestClient(app) as client:
        with client.websocket_connect("/webchat/ws") as websocket:
            websocket.send_text(
                json.dumps(
                    {
                        "type": "req",
                        "id": "web-oauth-1",
                        "method": "agent.send",
                        "params": {"message": "connect my github account"},
                    }
                )
            )

            ack = json.loads(websocket.receive_text())
            assert ack["type"] == "res"
            assert ack["ok"] is True
            assert ack["payload"]["queued"] is False
            assert "authorize_url" in ack["payload"]

            chunk = json.loads(websocket.receive_text())
            assert chunk["event"] == "agent.chunk"
            assert "https://github.com/login/oauth/authorize" in chunk["payload"]["text"]
            assert "Open this URL in your browser" in chunk["payload"]["text"]

            done = json.loads(websocket.receive_text())
            assert done["event"] == "agent.done"
