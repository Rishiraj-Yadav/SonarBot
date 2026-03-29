"""Load configuration from TOML, .env, and environment variables."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from pydantic import ValidationError

from assistant.config.schema import AppConfig

ENV_MAPPING: dict[str, tuple[str, ...]] = {
    "ASSISTANT_HOME": ("assistant_home",),
    "GATEWAY_HOST": ("gateway", "host"),
    "GATEWAY_PORT": ("gateway", "port"),
    "GATEWAY_TOKEN": ("gateway", "token"),
    "AGENT_WORKSPACE_DIR": ("agent", "workspace_dir"),
    "AGENT_MODEL": ("agent", "model"),
    "AGENT_MAX_TOKENS": ("agent", "max_tokens"),
    "AGENT_CONTEXT_WINDOW": ("agent", "context_window"),
    "OPENAI_API_KEY": ("llm", "openai_api_key"),
    "GEMINI_API_KEY": ("llm", "gemini_api_key"),
    "LLM_GEMINI_API_KEY": ("llm", "gemini_api_key"),
    "TELEGRAM_BOT_TOKEN": ("telegram", "bot_token"),
    "TELEGRAM_ALLOWED_USER_IDS": ("telegram", "allowed_user_ids"),
    "BRAVE_API_KEY": ("tools", "brave_api_key"),
    "SANDBOX_ENABLED": ("sandbox", "enabled"),
    "SYSTEM_ACCESS_ENABLED": ("system_access", "enabled"),
    "GOOGLE_CLIENT_ID": ("oauth", "google", "client_id"),
    "GOOGLE_CLIENT_SECRET": ("oauth", "google", "client_secret"),
    "GITHUB_CLIENT_ID": ("oauth", "github", "client_id"),
    "GITHUB_CLIENT_SECRET": ("oauth", "github", "client_secret"),
}


def default_assistant_home() -> Path:
    return Path(os.environ.get("ASSISTANT_HOME", Path.home() / ".assistant")).expanduser().resolve()


def _deep_set(data: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    cursor = data
    for segment in path[:-1]:
        cursor = cursor.setdefault(segment, {})
    cursor[path[-1]] = value


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        raw = handle.read()
    return tomllib.loads(raw.decode("utf-8-sig"))


def load_config(config_path: Path | None = None, dotenv_path: Path | None = None) -> AppConfig:
    config_path = (config_path or (default_assistant_home() / "config.toml")).expanduser().resolve()
    dotenv_path = (dotenv_path or (Path.cwd() / ".env")).expanduser().resolve()

    raw_config = _load_toml(config_path)
    env_values = {
        key: value
        for key, value in {**dotenv_values(dotenv_path), **os.environ}.items()
        if value not in (None, "")
    }

    merged = dict(raw_config)
    if "assistant_home" not in merged:
        merged["assistant_home"] = str(default_assistant_home())

    for key, path in ENV_MAPPING.items():
        if key in env_values:
            _deep_set(merged, path, env_values[key])

    try:
        return AppConfig.model_validate(merged)
    except ValidationError as exc:
        raise RuntimeError(
            "Configuration is invalid. Populate ~/.assistant/config.toml or a local .env with the required values."
        ) from exc
