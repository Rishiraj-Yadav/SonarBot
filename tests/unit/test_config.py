from __future__ import annotations

from pathlib import Path

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


def test_load_config_merges_project_local_config_with_assistant_home(monkeypatch, tmp_path: Path) -> None:
    assistant_home = tmp_path / "assistant-home"
    assistant_home.mkdir(parents=True, exist_ok=True)
    config_path = assistant_home / "config.toml"
    local_config_path = tmp_path / "config.toml"
    dotenv_path = tmp_path / ".env"
    workspace_dir = (tmp_path / "workspace").as_posix()

    config_path.write_text(
        "[gateway]\n"
        'host = "127.0.0.1"\n'
        "port = 8765\n"
        'token = "from-home"\n\n'
        "[agent]\n"
        f'workspace_dir = "{workspace_dir}"\n'
        'model = "gemini-config"\n'
        "max_tokens = 2048\n"
        "context_window = 32768\n\n"
        "[llm]\n"
        'gemini_api_key = "config-key"\n',
        encoding="utf-8",
    )
    local_config_path.write_text(
        "[system_access]\n"
        "enabled = true\n\n"
        "[users]\n"
        'default_user_id = "local-user"\n',
        encoding="utf-8",
    )
    dotenv_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("ASSISTANT_HOME", str(assistant_home))
    monkeypatch.chdir(tmp_path)

    config = load_config(dotenv_path=dotenv_path)

    assert config.gateway.token == "from-home"
    assert config.system_access.enabled is True
    assert config.users.default_user_id == "local-user"
