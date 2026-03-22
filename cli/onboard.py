"""Interactive onboarding flow."""

from __future__ import annotations

import platform
from pathlib import Path

from rich.console import Console
from rich.prompt import Confirm, Prompt

from assistant.config import default_assistant_home, load_config

console = Console()

TEMPLATE_FILES = ["SOUL.md", "AGENTS.md", "USER.md", "IDENTITY.md", "TOOLS.md", "MEMORY.md", "STANDING_ORDERS.md", "BOOT.md"]


def run_onboarding() -> None:
    assistant_home = default_assistant_home()
    workspace_default = Path.cwd() / "workspace"
    workspace_dir = Path(
        Prompt.ask("Workspace directory", default=str(workspace_default))
    ).expanduser().resolve()
    gateway_token = Prompt.ask("Gateway token", default="change-me")
    model_name = Prompt.ask("Gemini model", default="gemini-2.0-flash")
    gemini_api_key = Prompt.ask("Gemini API key", password=True, default="")
    telegram_bot_token = Prompt.ask("Telegram bot token (optional)", default="")
    telegram_allowed_user = Prompt.ask("Telegram allowed user id (optional)", default="")
    brave_api_key = Prompt.ask("Brave Search API key (optional)", default="")
    google_client_id = Prompt.ask("Google OAuth client id (optional)", default="")
    google_client_secret = Prompt.ask("Google OAuth client secret (optional)", default="")
    github_client_id = Prompt.ask("GitHub OAuth client id (optional)", default="")
    github_client_secret = Prompt.ask("GitHub OAuth client secret (optional)", default="")
    enable_sandbox = Confirm.ask("Enable Docker sandbox support when available?", default=False)
    enable_autostart = Confirm.ask("Generate an auto-start service file?", default=False)

    assistant_home.mkdir(parents=True, exist_ok=True)
    workspace_dir.mkdir(parents=True, exist_ok=True)

    config_path = assistant_home / "config.toml"
    config_text = (
        "[gateway]\n"
        'host = "127.0.0.1"\n'
        "port = 8765\n"
        f'token = "{gateway_token}"\n\n'
        "[agent]\n"
        f'workspace_dir = "{workspace_dir}"\n'
        f'model = "{model_name}"\n'
        "max_tokens = 2048\n"
        "context_window = 32768\n"
        "max_sessions_per_key = 20\n"
        "session_max_age_days = 90\n\n"
        "[agent.compaction.memory_flush]\n"
        "enabled = true\n\n"
        "[llm]\n"
        'gemini_api_key = ""\n\n'
        "[channels]\n"
        f"enabled = {['telegram'] if telegram_bot_token else []}\n\n"
        "[telegram]\n"
        f'bot_token = "{telegram_bot_token}"\n'
        f"allowed_user_ids = {[int(telegram_allowed_user)] if telegram_allowed_user else []}\n\n"
        "[memory]\n"
        "vector_enabled = true\n\n"
        "[automation]\n"
        "heartbeat_interval_minutes = 15\n"
        "cron_jobs = []\n\n"
        "[tools]\n"
        f'brave_api_key = "{brave_api_key}"\n'
        "browser_headless = true\n\n"
        "[oauth.google]\n"
        f'client_id = "{google_client_id}"\n'
        f'client_secret = "{google_client_secret}"\n\n'
        "[oauth.github]\n"
        f'client_id = "{github_client_id}"\n'
        f'client_secret = "{github_client_secret}"\n\n'
        "[sandbox]\n"
        f"enabled = {str(enable_sandbox).lower()}\n"
        'image = "python:3.12-slim"\n'
        "cpu_limit = 0.5\n"
        "memory_limit_mb = 512\n"
    )
    config_path.write_text(config_text, encoding="utf-8")

    _copy_workspace_templates(workspace_dir)

    if gemini_api_key:
        env_path = Path.cwd() / ".env"
        env_path.write_text(f"GEMINI_API_KEY={gemini_api_key}\n", encoding="utf-8")
        console.print(f"[green]Wrote local API key placeholder to {env_path}[/green]")
    elif Confirm.ask("Skip writing a local .env file?", default=True):
        console.print("[yellow]Remember to set GEMINI_API_KEY before starting the daemon.[/yellow]")

    if enable_autostart:
        _write_service_file(config_path)

    console.print(f"[green]Created config at {config_path}[/green]")
    console.print(f"[green]Workspace ready at {workspace_dir}[/green]")


def _copy_workspace_templates(workspace_dir: Path) -> None:
    template_dir = Path(__file__).resolve().parents[1] / "workspace"
    for template_name in TEMPLATE_FILES:
        target = workspace_dir / template_name
        if target.exists():
            continue
        source = template_dir / template_name
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


def _write_service_file(config_path: Path) -> None:
    config = load_config(config_path=config_path)
    scripts_dir = "Scripts" if platform.system().lower() == "windows" else "bin"
    python_name = "python.exe" if platform.system().lower() == "windows" else "python"
    python_executable = Path.cwd() / ".venv" / scripts_dir / python_name
    if platform.system().lower() == "linux":
        service_dir = config.systemd_user_dir
        service_dir.mkdir(parents=True, exist_ok=True)
        service_path = service_dir / "assistant.service"
        service_path.write_text(
            (
                "[Unit]\nDescription=SonarBot Assistant\n\n"
                "[Service]\n"
                f"WorkingDirectory={Path.cwd()}\n"
                f"ExecStart={python_executable} -m uvicorn assistant.main:app --host 127.0.0.1 --port 8765\n"
                "Restart=on-failure\n\n"
                "[Install]\nWantedBy=default.target\n"
            ),
            encoding="utf-8",
        )
        console.print(f"[green]Wrote systemd unit to {service_path}[/green]")
        return

    if platform.system().lower() == "darwin":
        service_dir = config.launch_agents_dir
        service_dir.mkdir(parents=True, exist_ok=True)
        plist_path = service_dir / "ai.assistant.plist"
        plist_path.write_text(
            (
                "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
                "<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n"
                "<plist version=\"1.0\"><dict>"
                "<key>Label</key><string>ai.assistant</string>"
                "<key>ProgramArguments</key><array>"
                f"<string>{python_executable}</string>"
                "<string>-m</string><string>uvicorn</string><string>assistant.main:app</string>"
                "<string>--host</string><string>127.0.0.1</string>"
                "<string>--port</string><string>8765</string>"
                "</array>"
                f"<key>WorkingDirectory</key><string>{Path.cwd()}</string>"
                "<key>RunAtLoad</key><true/>"
                "</dict></plist>"
            ),
            encoding="utf-8",
        )
        console.print(f"[green]Wrote launchd plist to {plist_path}[/green]")
