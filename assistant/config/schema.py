"""Pydantic models for application configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

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
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/gmail.compose",
            "https://www.googleapis.com/auth/gmail.modify",
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
    temporal_decay_lambda: float = 0.02
    mmr_lambda: float = 0.7
    multimodal_enabled: bool = True
    auto_capture_enabled: bool = True


class WebhookConfig(BaseModel):
    secret: str
    message_template: str


class CronJobConfig(BaseModel):
    schedule: str
    message: str


class AutomationRuleConfig(BaseModel):
    name: str
    trigger: str
    prompt_or_skill: str
    enabled: bool = True
    conditions: dict[str, str] = Field(default_factory=dict)
    action_policy: str = "notify_first"
    delivery_policy: str = "primary"
    cooldown_seconds: int = 0
    dedupe_window_seconds: int = 300
    quiet_hours_behavior: str = "queue"
    severity: str = "info"


class AutomationDeliveryConfig(BaseModel):
    retry_attempts: int = 3
    retry_backoff_seconds: int = 30
    fallback_to_secondary: bool = True


class AutomationApprovalsConfig(BaseModel):
    enabled: bool = True
    timeout_minutes: int = 60
    default_action: str = "deny"


class AutomationNotificationsConfig(BaseModel):
    inbox_retention_days: int = 30
    default_severity: str = "info"


class DesktopAutomationConfig(BaseModel):
    enabled: bool = False
    watch_enabled: bool = False
    ignored_patterns: list[str] = Field(
        default_factory=lambda: [
            "~$*",
            "*.tmp",
            "*.temp",
            "*.crdownload",
            "*.part",
            ".DS_Store",
        ]
    )
    event_debounce_ms: int = 1500
    poll_interval_seconds: int = 3


class AutomationConfig(BaseModel):
    heartbeat_interval_minutes: int = 15
    cron_jobs: list[CronJobConfig] = Field(default_factory=list)
    webhooks: dict[str, WebhookConfig] = Field(default_factory=dict)
    rules: list[AutomationRuleConfig] = Field(default_factory=list)
    delivery: AutomationDeliveryConfig = Field(default_factory=AutomationDeliveryConfig)
    approvals: AutomationApprovalsConfig = Field(default_factory=AutomationApprovalsConfig)
    notifications: AutomationNotificationsConfig = Field(default_factory=AutomationNotificationsConfig)
    desktop: DesktopAutomationConfig = Field(default_factory=DesktopAutomationConfig)


class ContextEngineConfig(BaseModel):
    enabled: bool = False
    interval_minutes: int = 180
    recent_session_message_limit: int = 6
    session_count_limit: int = 4
    gmail_thread_limit: int = 5
    calendar_event_limit: int = 6
    max_notifications_per_run: int = 2
    min_confidence: float = 0.82
    min_urgency: float = 0.55
    dedupe_days: int = 7
    snapshot_subdir: str = "context_engine/life_state"
    insights_subdir: str = "context_engine/insights"


class ToolsConfig(BaseModel):
    brave_api_key: str = ""
    browser_headless: bool = True
    browser_profiles_subdir: str = "browser_sessions"
    browser_screenshots_subdir: str = "browser"
    browser_downloads_subdir: str = "inbox/browser_downloads"
    browser_log_retention: int = 200
    browser_screenshot_stream_interval_seconds: int = 3


class SandboxConfig(BaseModel):
    enabled: bool = False
    image: str = "python:3.12-slim"
    cpu_limit: float = 0.5
    memory_limit_mb: int = 512


SystemAccessDecision = Literal["auto_allow", "ask_once", "always_ask", "deny"]


def _default_system_access_protected_roots() -> list[Path]:
    home = Path.home()
    return [
        _expand_path(Path("C:/Windows")),
        _expand_path(Path("C:/Program Files")),
        _expand_path(Path("C:/Program Files (x86)")),
        _expand_path(Path("C:/ProgramData")),
        _expand_path(home / "AppData"),
        _expand_path(Path("C:/$Recycle.Bin")),
        _expand_path(Path("C:/System Volume Information")),
        _expand_path(Path("R:/$Recycle.Bin")),
        _expand_path(Path("R:/System Volume Information")),
    ]


class SystemAccessPathRuleConfig(BaseModel):
    path: Path
    read: SystemAccessDecision = "auto_allow"
    write: SystemAccessDecision = "ask_once"
    overwrite: SystemAccessDecision = "always_ask"
    delete: SystemAccessDecision = "always_ask"
    execute: SystemAccessDecision = "ask_once"

    @field_validator("path", mode="before")
    @classmethod
    def validate_path(cls, value: str | Path) -> Path:
        return _expand_path(value)


class SystemAccessConfig(BaseModel):
    enabled: bool = False
    home_root: Path = Field(default_factory=lambda: _expand_path(Path.home()))
    shell: str = "powershell"
    approval_timeout_seconds: int = 300
    ask_once_session_cache: bool = True
    default_outside_policy: SystemAccessDecision = "deny"
    protected_roots: list[Path] = Field(default_factory=_default_system_access_protected_roots)
    path_rules: list[SystemAccessPathRuleConfig] = Field(default_factory=list)
    audit_log_path: Path = Field(
        default_factory=lambda: _expand_path(Path.home() / ".assistant" / "logs" / "system_actions.jsonl")
    )
    backup_root: Path = Field(
        default_factory=lambda: _expand_path(Path.home() / ".assistant" / "backups" / "system_access")
    )

    @field_validator("home_root", "audit_log_path", "backup_root", mode="before")
    @classmethod
    def validate_paths(cls, value: str | Path) -> Path:
        return _expand_path(value)

    @field_validator("protected_roots", mode="before")
    @classmethod
    def validate_protected_roots(cls, value: object) -> list[Path]:
        if value in (None, "", []):
            return []
        if isinstance(value, list):
            return [_expand_path(item) for item in value]
        raise TypeError("system_access.protected_roots must be a list of paths.")


class UsersConfig(BaseModel):
    default_user_id: str = "default"
    primary_channel: str = "webchat"
    fallback_channels: list[str] = Field(default_factory=list)
    quiet_hours_start: str = ""
    quiet_hours_end: str = ""
    notification_level: str = "normal"
    automation_enabled: bool = True
    auto_link_single_user: bool = True


class AppConfig(BaseModel):
    assistant_home: Path = Field(default_factory=lambda: _expand_path(Path.home() / ".assistant"))
    gateway: GatewayConfig
    agent: AgentConfig
    llm: LlmConfig
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    automation: AutomationConfig = Field(default_factory=AutomationConfig)
    context_engine: ContextEngineConfig = Field(default_factory=ContextEngineConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    oauth: OAuthConfig = Field(default_factory=OAuthConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    system_access: SystemAccessConfig = Field(default_factory=SystemAccessConfig)
    users: UsersConfig = Field(default_factory=UsersConfig)

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

    @property
    def acp_registry_path(self) -> Path:
        return self.assistant_home / "acp_agents.json"

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
        (self.agent.workspace_dir / self.context_engine.snapshot_subdir).mkdir(parents=True, exist_ok=True)
        (self.agent.workspace_dir / self.context_engine.insights_subdir).mkdir(parents=True, exist_ok=True)
        self.sandbox_dir.mkdir(parents=True, exist_ok=True)
        self.system_access.home_root.mkdir(parents=True, exist_ok=True)
        self.system_access.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        self.system_access.backup_root.mkdir(parents=True, exist_ok=True)
