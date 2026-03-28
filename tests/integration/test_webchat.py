from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient

from assistant.gateway.server import create_app
from assistant.models.base import ModelResponse
from assistant.tools.browser_runtime import BrowserRuntime, BrowserTabState, profile_key_for
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


def test_webchat_browser_api_exposes_state_tabs_logs_downloads_and_profiles(app_config) -> None:
    provider = FakeProvider([[ModelResponse(done=True)]])
    app = create_app(config=app_config, model_provider=provider)

    with TestClient(app) as client:
        services = app.state.services
        runtime = services.browser_runtime
        profile_key = profile_key_for("example.com", "work")
        runtime.session_index_path.write_text(
            json.dumps(
                {
                    profile_key: {
                        "profile_key": profile_key,
                        "site_name": "example.com",
                        "profile_name": "work",
                        "domain": "example.com",
                        "storage_path": str(runtime.sessions_dir / "example-work.json"),
                        "status": "active",
                        "last_used_at": "2026-03-24T09:00:00+00:00",
                    }
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        runtime.current_profile_key = profile_key
        runtime.current_tab_id = "tab-1"
        runtime.current_headless = False
        runtime._tabs["tab-1"] = BrowserTabState(
            tab_id="tab-1",
            page=None,
            created_at="2026-03-24T09:00:00+00:00",
            title="Example",
            url="https://example.com/work",
        )
        runtime._recent_logs.append(
            {
                "timestamp": "2026-03-24T09:05:00+00:00",
                "kind": "console",
                "level": "log",
                "message": "ready",
                "tab_id": "tab-1",
                "url": "https://example.com/work",
                "profile_key": profile_key,
            }
        )
        runtime._recent_downloads.append(
            {
                "path": str(runtime.downloads_dir / "example.com" / "work" / "report.csv"),
                "filename": "report.csv",
                "profile_key": profile_key,
                "created_at": "2026-03-24T09:06:00+00:00",
                "size": 256,
            }
        )

        state = client.get("/api/browser/state")
        tabs = client.get("/api/browser/tabs")
        logs = client.get("/api/browser/logs?limit=4")
        downloads = client.get("/api/browser/downloads?limit=4")
        profiles = client.get("/api/browser/profiles")

        assert state.status_code == 200
        assert state.json()["state"]["active_profile"]["profile_name"] == "work"
        assert tabs.json()["tabs"][0]["title"] == "Example"
        assert logs.json()["logs"][0]["message"] == "ready"
        assert downloads.json()["downloads"][0]["filename"] == "report.csv"
        assert profiles.json()["profiles"][0]["site_name"] == "example.com"


def test_webchat_context_engine_api_exposes_engine_status(app_config) -> None:
    app_config.context_engine.enabled = True
    provider = FakeProvider([[ModelResponse(done=True)]])
    app = create_app(config=app_config, model_provider=provider)

    with TestClient(app) as client:
        response = client.get("/api/context-engine/state")

        assert response.status_code == 200
        payload = response.json()
        assert payload["engine"]["enabled"] is True
        assert "snapshot_dir" in payload["engine"]


def test_webchat_browser_click_endpoint_invokes_runtime(app_config, monkeypatch) -> None:
    provider = FakeProvider([[ModelResponse(done=True)]])
    app = create_app(config=app_config, model_provider=provider)

    called: dict[str, object] = {}

    async def fake_click_coordinate(self, x: int, y: int, *, tab_id: str | None = None, user_id: str | None = None):
        called["click"] = {"x": x, "y": y, "tab_id": tab_id, "user_id": user_id}
        return {"clicked": True}

    async def fake_refresh_active_tab(self, user_id: str | None = None):
        called["refresh_user_id"] = user_id

    monkeypatch.setattr(BrowserRuntime, "click_coordinate", fake_click_coordinate)
    monkeypatch.setattr(BrowserRuntime, "refresh_active_tab", fake_refresh_active_tab)

    with TestClient(app) as client:
        runtime = app.state.services.browser_runtime
        runtime.current_tab_id = "tab-9"

        response = client.post("/webchat/browser/click", json={"x": 42, "y": 84, "tab_id": "tab-9"})

        assert response.status_code == 200
        payload = response.json()
        assert payload["ok"] is True
        assert called["click"] == {"x": 42, "y": 84, "tab_id": "tab-9", "user_id": "default"}
        assert called["refresh_user_id"] == "default"


def test_webchat_browser_live_screenshot_endpoint_returns_payload(app_config, monkeypatch) -> None:
    provider = FakeProvider([[ModelResponse(done=True)]])
    app = create_app(config=app_config, model_provider=provider)

    async def fake_latest_screenshot_payload(self):
        return {
            "tab_id": "tab-3",
            "url": "https://leetcode.com/problemset/",
            "title": "LeetCode",
            "mode": "headless",
            "image_data_url": "data:image/jpeg;base64,abc123",
        }

    monkeypatch.setattr(BrowserRuntime, "latest_screenshot_payload", fake_latest_screenshot_payload)

    with TestClient(app) as client:
        response = client.get("/api/browser/live-screenshot")

        assert response.status_code == 200
        payload = response.json()
        assert payload["screenshot"]["tab_id"] == "tab-3"
        assert payload["screenshot"]["image_data_url"] == "data:image/jpeg;base64,abc123"


def test_startup_browser_session_health_check_is_best_effort(app_config, monkeypatch) -> None:
    provider = FakeProvider([[ModelResponse(done=True)]])
    index_path = app_config.agent.workspace_dir / app_config.tools.browser_profiles_subdir / "index.json"
    profile_key = profile_key_for("example.com", "work")
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        json.dumps(
            {
                profile_key: {
                    "profile_key": profile_key,
                    "site_name": "example.com",
                    "profile_name": "work",
                    "domain": "example.com",
                    "storage_path": str(app_config.agent.workspace_dir / app_config.tools.browser_profiles_subdir / "example-work.json"),
                    "status": "active",
                    "last_used_at": "2026-03-24T09:00:00+00:00",
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    seen: list[tuple[str, str]] = []

    async def fake_session_health_check(self, site_name: str, profile_name: str = "default", user_id: str | None = None):
        seen.append((site_name, profile_name))
        return {"site_name": site_name, "profile_name": profile_name, "status": "active"}

    monkeypatch.setattr(BrowserRuntime, "session_health_check", fake_session_health_check)
    app = create_app(config=app_config, model_provider=provider)

    with TestClient(app):
        time.sleep(0.1)

    assert ("example.com", "work") in seen
