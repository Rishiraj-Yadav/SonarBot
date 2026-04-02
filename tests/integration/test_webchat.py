from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from assistant.gateway.server import create_app
from assistant.automation.models import Notification
from assistant.models.base import ModelResponse
from assistant.tools.browser_runtime import BrowserTabState, profile_key_for
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


def test_webchat_automation_rules_api_includes_desktop_rules(app_config) -> None:
    app_config.automation.desktop.enabled = True
    provider = FakeProvider([[ModelResponse(done=True)]])
    app = create_app(config=app_config, model_provider=provider)

    with TestClient(app) as client:
        services = app.state.services
        created = asyncio.run(
            services.automation_engine.create_desktop_automation_rule(
                app_config.users.default_user_id,
                name="Move PDFs from Download2",
                trigger_type="file_watch",
                watch_path="R:/Download2",
                event_types=["file_created"],
                file_extensions=["pdf"],
                action_type="move",
                destination_path="R:/Documents/PDFs",
            )
        )

        response = client.get("/api/automation/rules")

        assert response.status_code == 200
        rules = response.json()["rules"]
        assert any(rule["name"] == f"desktop:{created['rule_id']}" for rule in rules)


def test_webchat_automation_rules_api_includes_desktop_routines_and_run_endpoint(app_config) -> None:
    app_config.automation.desktop.enabled = True
    app_config.desktop_apps.enabled = True
    provider = FakeProvider([[ModelResponse(done=True)]])
    app = create_app(config=app_config, model_provider=provider)

    with TestClient(app) as client:
        services = app.state.services
        created = asyncio.run(
            services.automation_engine.create_desktop_routine_rule(
                app_config.users.default_user_id,
                name="Study mode",
                trigger_type="manual",
                steps=[
                    {"type": "notify", "text": "Study mode ready."},
                ],
            )
        )

        response = client.get("/api/automation/rules")
        run_response = client.post(f"/api/automation/rules/routine:{created['routine_id']}/run")

        assert response.status_code == 200
        rules = response.json()["rules"]
        assert any(rule["name"] == f"routine:{created['routine_id']}" for rule in rules)
        assert run_response.status_code == 200
        assert run_response.json()["ok"] is True


def test_webchat_coworker_api_supports_plan_and_history(app_config) -> None:
    app_config.desktop_coworker.enabled = True
    provider = FakeProvider([[ModelResponse(done=True)]])
    app = create_app(config=app_config, model_provider=provider)

    class FakeCoworkerService:
        async def plan_task(self, *, user_id: str, session_key: str, request_text: str) -> dict[str, object]:
            return {
                "task_id": "coworker-1",
                "user_id": user_id,
                "session_key": session_key,
                "request_text": request_text,
                "status": "planned",
                "summary": "Open Task Manager and summarize system usage.",
                "steps": [{"type": "task_manager_open", "title": "Open Task Manager"}],
                "current_step_index": 0,
                "total_steps": 1,
                "latest_state": {},
                "transcript": [],
            }

        async def list_tasks(self, *, user_id: str, limit: int = 20) -> list[dict[str, object]]:
            return [
                {
                    "task_id": "coworker-1",
                    "summary": "Open Task Manager and summarize system usage.",
                    "status": "planned",
                    "current_step_index": 0,
                    "total_steps": 1,
                }
            ]

        def backend_health(self) -> dict[str, object]:
            return {"uia": {"available": False}, "ocr_boxes": {"available": True}}

    with TestClient(app) as client:
        app.state.services.coworker_service = FakeCoworkerService()

        plan_response = client.post(
            "/api/coworker/tasks/plan",
            json={"task": "open task manager and summarize system usage"},
        )
        history_response = client.get("/api/coworker/tasks")

        assert plan_response.status_code == 200
        assert plan_response.json()["ok"] is True
        assert plan_response.json()["task"]["task_id"] == "coworker-1"
        assert history_response.status_code == 200
        assert history_response.json()["enabled"] is True
        assert history_response.json()["tasks"][0]["task_id"] == "coworker-1"
        assert "backend_health" in history_response.json()["tasks"][0]


def test_webchat_coworker_api_supports_retry_and_artifact_download(app_config, tmp_path) -> None:
    app_config.desktop_coworker.enabled = True
    provider = FakeProvider([[ModelResponse(done=True)]])
    app = create_app(config=app_config, model_provider=provider)
    artifact = tmp_path / "coworker-artifact.png"
    artifact.write_bytes(b"fake-image")

    class FakeCoworkerService:
        async def retry_task(self, *, user_id: str, task_id: str) -> dict[str, object]:
            return {
                "task_id": task_id,
                "user_id": user_id,
                "session_key": "webchat_main",
                "request_text": "click on the desktop",
                "summary": "Click on the desktop.",
                "status": "failed",
                "current_step_index": 0,
                "total_steps": 1,
                "artifacts": [
                    {
                        "artifact_id": "artifact-1",
                        "path": str(artifact),
                        "kind": "screenshot",
                        "created_at": "2026-04-01T00:00:00+00:00",
                    }
                ],
                "latest_state": {
                    "artifacts": [
                        {
                            "artifact_id": "artifact-1",
                            "path": str(artifact),
                            "kind": "screenshot",
                            "created_at": "2026-04-01T00:00:00+00:00",
                        }
                    ],
                    "stop_reason": "The visible target did not change.",
                },
                "transcript": [],
                "last_backend": "ocr_boxes",
                "current_attempt": 2,
                "stop_reason": "The visible target did not change.",
            }

        async def get_task(self, *, user_id: str, task_id: str) -> dict[str, object]:
            return await self.retry_task(user_id=user_id, task_id=task_id)

        def backend_health(self) -> dict[str, object]:
            return {"uia": {"available": False}, "ocr_boxes": {"available": True}}

    with TestClient(app) as client:
        app.state.services.coworker_service = FakeCoworkerService()

        retry_response = client.post("/api/coworker/tasks/coworker-1/retry")
        artifact_response = client.get("/api/coworker/tasks/coworker-1/artifacts/artifact-1")

        assert retry_response.status_code == 200
        assert retry_response.json()["ok"] is True
        assert retry_response.json()["task"]["current_attempt"] == 2
        assert artifact_response.status_code == 200
        assert artifact_response.content == b"fake-image"


def test_settings_api_exposes_coworker_backend_health(app_config) -> None:
    app_config.desktop_coworker.enabled = True
    provider = FakeProvider([[ModelResponse(done=True)]])
    app = create_app(config=app_config, model_provider=provider)

    class FakeCoworkerService:
        def backend_health(self) -> dict[str, object]:
            return {"uia": {"available": False}, "ocr_boxes": {"available": True}}

    with TestClient(app) as client:
        app.state.services.coworker_service = FakeCoworkerService()
        response = client.get("/api/settings")

        assert response.status_code == 200
        payload = response.json()
        assert payload["desktop_coworker"]["enabled"] is True
        assert payload["desktop_coworker"]["backend_health"]["ocr_boxes"]["available"] is True


def test_webchat_receives_automation_notification_events(app_config) -> None:
    provider = FakeProvider([[ModelResponse(done=True)]])
    app = create_app(config=app_config, model_provider=provider)

    with TestClient(app) as client:
        with client.websocket_connect("/webchat/ws") as websocket:
            services = app.state.services
            asyncio.run(
                services.automation_engine.dispatcher.dispatch(
                    Notification(
                        notification_id="notif-webchat-1",
                        user_id=app_config.users.default_user_id,
                        title="Cron summary",
                        body="Cron summary body",
                        source="cron:0",
                        severity="info",
                        delivery_mode="primary",
                        status="queued",
                        target_channels=[],
                    )
                )
            )

            frame = json.loads(websocket.receive_text())
            assert frame["type"] == "event"
            assert frame["event"] == "notification.created"
            assert frame["payload"]["title"] == "Cron summary"
            assert frame["payload"]["body"] == "Cron summary body"
