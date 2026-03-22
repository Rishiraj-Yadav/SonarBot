from __future__ import annotations

from pathlib import Path

import pytest

from assistant.config.schema import AppConfig
PROMPT_FILES = {
    "SOUL.md": "You are SonarBot.",
    "AGENTS.md": "Be helpful.",
    "USER.md": "User profile.",
    "IDENTITY.md": "Name: SonarBot",
    "TOOLS.md": "Use tools carefully.",
    "MEMORY.md": "Memory starter.",
    "STANDING_ORDERS.md": "- Remind the user about urgent tasks.",
    "BOOT.md": "",
}


@pytest.fixture
def app_config(tmp_path: Path) -> AppConfig:
    assistant_home = tmp_path / ".assistant"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    for name, content in PROMPT_FILES.items():
        (workspace_dir / name).write_text(content, encoding="utf-8")

    config = AppConfig.model_validate(
        {
            "assistant_home": str(assistant_home),
            "gateway": {"host": "127.0.0.1", "port": 8765, "token": "secret-token"},
            "agent": {
                "workspace_dir": str(workspace_dir),
                "model": "gemini-test",
                "max_tokens": 2048,
                "context_window": 512,
            },
            "llm": {"gemini_api_key": "fake-key"},
            "channels": {"enabled": []},
        }
    )
    config.ensure_runtime_dirs()
    return config
