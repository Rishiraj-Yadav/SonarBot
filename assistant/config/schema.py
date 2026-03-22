"""Pydantic models for application configuration."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field, field_validator


def _expand_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


class GatewayConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8765
    token: str
    rate_limit_per_minute: int = 10


class MemoryFlushConfig(BaseModel):
    enabled: bool = False


class CompactionConfig(BaseModel):
    memory_flush: MemoryFlushConfig = Field(default_factory=MemoryFlushConfig)


class AgentConfig(BaseModel):
    workspace_dir: Path
    model: str = "gemini-2.0-flash"
    max_tokens: int = 2048
    context_window: int = 32768
    max_sessions_per_key: int = 20
    session_max_age_days: int = 90
    compaction: CompactionConfig = Field(default_factory=CompactionConfig)

    @field_validator("workspace_dir", mode="before")
    @classmethod
    def validate_workspace_dir(cls, value: str | Path) -> Path:
        return _expand_path(value)


class LlmConfig(BaseModel):
    gemini_api_key: str
    openai_api_key: str = ""


class OAuthGoogleConfig(BaseModel):
    client_id: str = ""
    client_secret: str = ""
    scopes: list[str] = Field(
        default_factory=lambda: [
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/calendar.readonly",
        ]
    )


class OAuthGitHubConfig(BaseModel):
    client_id: str = ""
    client_secret: str = ""
    scopes: list[str] = Field(default_factory=lambda: ["repo", "read:user"])


class OAuthConfig(BaseModel):
    enabled: bool = True
    google: OAuthGoogleConfig = Field(default_factory=OAuthGoogleConfig)
    github: OAuthGitHubConfig = Field(default_factory=OAuthGitHubConfig)


class ChannelsConfig(BaseModel):
    enabled: list[str] = Field(default_factory=list)


class TelegramConfig(BaseModel):
    bot_token: str = ""
    allowed_user_ids: list[int] = Field(default_factory=list)

    @field_validator("allowed_user_ids", mode="before")
    @classmethod
    def validate_allowed_user_ids(cls, value: object) -> list[int]:
        if value in (None, "", []):
            return []
        if isinstance(value, str):
            return [int(item.strip()) for item in value.split(",") if item.strip()]
        if isinstance(value, list):
            return [int(item) for item in value]
        raise TypeError("telegram.allowed_user_ids must be a list or comma-separated string.")


class MemoryConfig(BaseModel):
    vector_enabled: bool = True


class WebhookConfig(BaseModel):
    secret: str
    message_template: str


class CronJobConfig(BaseModel):
    schedule: str
    message: str


class AutomationConfig(BaseModel):
    heartbeat_interval_minutes: int = 15
    cron_jobs: list[CronJobConfig] = Field(default_factory=list)
    webhooks: dict[str, WebhookConfig] = Field(default_factory=dict)


class ToolsConfig(BaseModel):
    brave_api_key: str = ""
    browser_headless: bool = True


class SandboxConfig(BaseModel):
    enabled: bool = False
    image: str = "python:3.12-slim"
    cpu_limit: float = 0.5
    memory_limit_mb: int = 512


class AppConfig(BaseModel):
    assistant_home: Path = Field(default_factory=lambda: _expand_path(Path.home() / ".assistant"))
    gateway: GatewayConfig
    agent: AgentConfig
    llm: LlmConfig
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    automation: AutomationConfig = Field(default_factory=AutomationConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    oauth: OAuthConfig = Field(default_factory=OAuthConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)

    @field_validator("assistant_home", mode="before")
    @classmethod
    def validate_assistant_home(cls, value: str | Path) -> Path:
        return _expand_path(value)

    @property
    def logs_dir(self) -> Path:
        return self.assistant_home / "logs"

    @property
    def sessions_dir(self) -> Path:
        return self.assistant_home / "sessions"

    @property
    def config_dir(self) -> Path:
        return self.assistant_home

    @property
    def chroma_dir(self) -> Path:
        return self.assistant_home / "chroma"

    @property
    def archive_sessions_dir(self) -> Path:
        return self.sessions_dir / "archive"

    @property
    def skills_home(self) -> Path:
        return self.assistant_home / "skills"

    @property
    def hooks_home(self) -> Path:
        return self.assistant_home / "hooks"

    @property
    def oauth_dir(self) -> Path:
        return self.assistant_home / "oauth"

    @property
    def sandbox_dir(self) -> Path:
        return self.agent.workspace_dir / "sandbox"

    @property
    def data_db_path(self) -> Path:
        return self.assistant_home / "assistant.db"

    @property
    def systemd_user_dir(self) -> Path:
        return Path.home() / ".config" / "systemd" / "user"

    @property
    def launch_agents_dir(self) -> Path:
        return Path.home() / "Library" / "LaunchAgents"

    def ensure_runtime_dirs(self) -> None:
        self.assistant_home.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.archive_sessions_dir.mkdir(parents=True, exist_ok=True)
        self.chroma_dir.mkdir(parents=True, exist_ok=True)
        self.skills_home.mkdir(parents=True, exist_ok=True)
        self.hooks_home.mkdir(parents=True, exist_ok=True)
        self.oauth_dir.mkdir(parents=True, exist_ok=True)
        self.agent.workspace_dir.mkdir(parents=True, exist_ok=True)
        (self.agent.workspace_dir / "memory").mkdir(parents=True, exist_ok=True)
        (self.agent.workspace_dir / "inbox").mkdir(parents=True, exist_ok=True)
        (self.agent.workspace_dir / "skills").mkdir(parents=True, exist_ok=True)
        (self.agent.workspace_dir / "hooks").mkdir(parents=True, exist_ok=True)
        self.sandbox_dir.mkdir(parents=True, exist_ok=True)
