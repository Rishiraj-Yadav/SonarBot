from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from assistant.gateway.server import create_app
from assistant.system_access.models import HostApprovalRequest
from tests.helpers import FakeProvider


def test_system_access_api_lists_and_decides_approvals(app_config, tmp_path) -> None:
    home_root = tmp_path / "home"
    home_root.mkdir(parents=True, exist_ok=True)
    app_config.system_access.enabled = True
    app_config.system_access.home_root = home_root
    app_config.system_access.protected_roots = []
    app_config.system_access.backup_root = tmp_path / "backups"
    app_config.system_access.audit_log_path = tmp_path / "logs" / "system_actions.jsonl"
    app_config.ensure_runtime_dirs()

    app = create_app(config=app_config, model_provider=FakeProvider([]))

    with TestClient(app) as client:
        approval = HostApprovalRequest(
            user_id="default",
            session_id="sess-api",
            session_key="webchat_main",
            action_kind="write_host_file",
            target_summary=str(home_root / "note.txt"),
            category="ask_once",
            payload={"path": str(home_root / "note.txt")},
        )
        asyncio.run(client.app.state.services.system_access_manager.store.create_approval(approval))

        listed = client.get("/api/system-access/approvals").json()
        assert listed["approvals"]
        assert listed["approvals"][0]["approval_id"] == approval.approval_id

        decided = client.post(
            f"/api/system-access/approvals/{approval.approval_id}",
            json={"decision": "approved"},
        ).json()
        assert decided["ok"] is True
        assert decided["approval"]["status"] == "approved"
