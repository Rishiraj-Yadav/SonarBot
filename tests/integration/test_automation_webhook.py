from __future__ import annotations

import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from assistant.config.schema import WebhookConfig
from assistant.gateway.server import create_app
from assistant.models.base import ModelResponse
from tests.helpers import FakeProvider


def _signature(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_webhook_creates_notification_and_rule_state(app_config) -> None:
    app_config.automation.webhooks = {
        "github_push": WebhookConfig(
            secret="top-secret",
            message_template="Push to {repository.full_name}: {commits[0].message}",
        )
    }
    provider = FakeProvider([[ModelResponse(text="Webhook automation summary.", done=True)]])
    app = create_app(config=app_config, model_provider=provider)

    payload = {
        "repository": {"full_name": "octo/repo"},
        "commits": [{"message": "ship automation"}],
    }
    body = json.dumps(payload).encode("utf-8")

    with TestClient(app) as client:
        response = client.post(
            "/webhooks/github_push",
            content=body,
            headers={"Content-Type": "application/json", "X-Signature": _signature("top-secret", body)},
        )

        assert response.status_code == 200
        assert response.json()["ok"] is True

        notifications = client.get("/api/notifications")
        assert notifications.status_code == 200
        data = notifications.json()
        assert len(data["notifications"]) == 1
        assert "Webhook automation summary." in data["notifications"][0]["body"]
