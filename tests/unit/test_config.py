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
