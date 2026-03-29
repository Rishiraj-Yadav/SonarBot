from __future__ import annotations

from pathlib import Path

import pytest

from assistant.config.loader import load_config


def test_load_config_with_dotenv_override(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    dotenv_path = tmp_path / ".env"
    workspace_dir = (tmp_path / "workspace").as_posix()

    config_path.write_text(
        "[gateway]\n"
        'host = "127.0.0.1"\n'
        "port = 8765\n"
        'token = "from-config"\n\n'
        "[agent]\n"
        f'workspace_dir = "{workspace_dir}"\n'
        'model = "gemini-config"\n'
        "max_tokens = 2048\n"
        "context_window = 32768\n\n"
        "[llm]\n"
        'gemini_api_key = "config-key"\n',
        encoding="utf-8",
    )
    dotenv_path.write_text("GATEWAY_TOKEN=from-dotenv\nGEMINI_API_KEY=dotenv-key\n", encoding="utf-8")

    config = load_config(config_path=config_path, dotenv_path=dotenv_path)

    assert config.gateway.token == "from-dotenv"
    assert config.llm.gemini_api_key == "dotenv-key"


def _minimal_config_toml(tmp_path: Path) -> Path:
    workspace_dir = (tmp_path / "workspace").as_posix()
    path = tmp_path / "config.toml"
    path.write_text(
        "[gateway]\n"
        'host = "127.0.0.1"\n'
        "port = 8765\n"
        'token = "t"\n\n'
        "[agent]\n"
        f'workspace_dir = "{workspace_dir}"\n'
        'model = "m"\n'
        "max_tokens = 2048\n"
        "context_window = 32768\n\n"
        "[llm]\n"
        'gemini_api_key = "k"\n',
        encoding="utf-8",
    )
    return path


def test_system_access_enabled_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYSTEM_ACCESS_ENABLED", "true")
    config_path = _minimal_config_toml(tmp_path)
    dotenv = tmp_path / ".env"
    dotenv.write_text("", encoding="utf-8")

    config = load_config(config_path=config_path, dotenv_path=dotenv)

    assert config.system_access.enabled is True
    monkeypatch.delenv("SYSTEM_ACCESS_ENABLED", raising=False)


def test_load_config_accepts_utf8_bom(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    workspace_dir = (tmp_path / "workspace").as_posix()

    config_path.write_bytes(
        (
            "\ufeff[gateway]\n"
            'host = "127.0.0.1"\n'
            "port = 8765\n"
            'token = "from-config"\n\n'
            "[agent]\n"
            f'workspace_dir = "{workspace_dir}"\n'
            'model = "gemini-config"\n'
            "max_tokens = 2048\n"
            "context_window = 32768\n\n"
            "[llm]\n"
            'gemini_api_key = "config-key"\n'
        ).encode("utf-8")
    )

    config = load_config(config_path=config_path, dotenv_path=tmp_path / ".env")

    assert config.gateway.token == "from-config"
    assert config.agent.model == "gemini-config"
