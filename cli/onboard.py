"""Interactive onboarding flow."""

from __future__ import annotations

import asyncio
import json
import platform
from datetime import datetime
from pathlib import Path

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from assistant.config import default_assistant_home, load_config

console = Console()

TEMPLATE_FILES = ["SOUL.md", "AGENTS.md", "USER.md", "IDENTITY.md", "TOOLS.md", "MEMORY.md", "STANDING_ORDERS.md", "BOOT.md"]


def run_onboarding() -> None:
    assistant_home = default_assistant_home()
    workspace_default = Path.cwd() / "workspace"

    console.print(Panel.fit("SonarBot Setup Wizard", subtitle="Phase 5 onboarding"))
    console.print("This will configure your local assistant, workspace, channels, automation, and optional integrations.")

    gemini_api_key = Prompt.ask("Gemini API key", password=True, default="")
    model_name = Prompt.ask("Gemini model", default="gemini-2.0-flash")
    gateway_token = Prompt.ask("Gateway token", default="change-me")
    workspace_dir = Path(Prompt.ask("Workspace directory", default=str(workspace_default))).expanduser().resolve()

    assistant_name = Prompt.ask("Assistant name", default="SonarBot")
    personality = Prompt.ask("Assistant personality", default="Helpful, proactive, calm, and concise.")
    user_name = Prompt.ask("Your name", default="Ritesh")
    timezone_name = Prompt.ask("Your timezone", default=str(datetime.now().astimezone().tzinfo or "UTC"))
    preferences = Prompt.ask("Your preferences", default="Prefer direct answers, practical plans, and local-first tools.")

    telegram_bot_token = Prompt.ask("Telegram bot token (optional)", default="")
    telegram_allowed_user = Prompt.ask("Telegram allowed user id (optional)", default="")

    enable_daily_briefing = Confirm.ask("Enable a daily morning briefing?", default=False)
    cron_jobs = []
    if enable_daily_briefing:
        daily_schedule = Prompt.ask("Daily briefing cron schedule", default="0 8 * * *")
        cron_jobs.append({"schedule": daily_schedule, "message": "Good morning briefing"})

    brave_api_key = Prompt.ask("Brave Search API key (optional)", default="")
    google_client_id, google_client_secret = _collect_oauth_credentials("Google")
    github_client_id, github_client_secret = _collect_oauth_credentials("GitHub")

    enable_sandbox = Confirm.ask("Enable Docker sandbox support when available?", default=False)
    enable_autostart = Confirm.ask("Generate an auto-start service file?", default=False)

    assistant_home.mkdir(parents=True, exist_ok=True)
    workspace_dir.mkdir(parents=True, exist_ok=True)

    _copy_workspace_templates(workspace_dir)
    _write_persona_files(workspace_dir, assistant_name, personality, user_name, timezone_name, preferences)

    config_path = assistant_home / "config.toml"
    config_text = _build_config_text(
        workspace_dir=workspace_dir,
        gateway_token=gateway_token,
        model_name=model_name,
        telegram_bot_token=telegram_bot_token,
        telegram_allowed_user=telegram_allowed_user,
        brave_api_key=brave_api_key,
        cron_jobs=cron_jobs,
        google_client_id=google_client_id,
        google_client_secret=google_client_secret,
        github_client_id=github_client_id,
        github_client_secret=github_client_secret,
        enable_sandbox=enable_sandbox,
    )
    config_path.write_text(config_text, encoding="utf-8")

    env_updates = {"GEMINI_API_KEY": gemini_api_key}
    _write_env_file(env_updates)

    console.print("")
    console.print(Panel.fit("Validation", subtitle="Best-effort checks"))
    if gemini_api_key:
        _print_validation("Gemini API key", asyncio.run(_validate_gemini_api_key(gemini_api_key, model_name)))
    else:
        _print_validation("Gemini API key", False, "Missing. Set GEMINI_API_KEY before starting the daemon.")

    if telegram_bot_token:
        ok, detail = asyncio.run(_validate_telegram_token(telegram_bot_token))
        _print_validation("Telegram bot token", ok, detail)
    else:
        _print_validation("Telegram bot token", True, "Skipped.")

    if enable_autostart:
        _write_service_file(config_path)

    console.print("")
    console.print(Panel.fit("Setup complete", subtitle="Next steps"))
    console.print(f"[green]Config:[/green] {config_path}")
    console.print(f"[green]Workspace:[/green] {workspace_dir}")
    console.print("Next:")
    console.print("1. `uv run assistant start`")
    console.print("2. `uv run assistant status`")
    console.print("3. `uv run assistant chat`")


def _collect_oauth_credentials(provider_name: str) -> tuple[str, str]:
    if not Confirm.ask(f"Configure {provider_name} OAuth now?", default=False):
        return "", ""
    client_id = Prompt.ask(f"{provider_name} client id", default="")
    client_secret = Prompt.ask(f"{provider_name} client secret", default="")
    return client_id, client_secret


def _build_config_text(
    *,
    workspace_dir: Path,
    gateway_token: str,
    model_name: str,
    telegram_bot_token: str,
    telegram_allowed_user: str,
    brave_api_key: str,
    cron_jobs: list[dict[str, str]],
    google_client_id: str,
    google_client_secret: str,
    github_client_id: str,
    github_client_secret: str,
    enable_sandbox: bool,
) -> str:
    cron_jobs_json = json.dumps(cron_jobs)
    enabled_channels = ["telegram"] if telegram_bot_token else []
    allowed_user_ids = [int(telegram_allowed_user)] if telegram_allowed_user else []
    return (
        "[gateway]\n"
        'host = "127.0.0.1"\n'
        "port = 8765\n"
        f'token = "{gateway_token}"\n'
        "rate_limit_per_minute = 10\n\n"
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
        'gemini_api_key = ""\n'
        'openai_api_key = ""\n\n'
        "[channels]\n"
        f"enabled = {enabled_channels}\n\n"
        "[telegram]\n"
        f'bot_token = "{telegram_bot_token}"\n'
        f"allowed_user_ids = {allowed_user_ids}\n\n"
        "[memory]\n"
        "vector_enabled = true\n"
        "temporal_decay_lambda = 0.02\n"
        "mmr_lambda = 0.7\n"
        "multimodal_enabled = true\n\n"
        "[automation]\n"
        "heartbeat_interval_minutes = 15\n"
        f"cron_jobs = {cron_jobs_json}\n\n"
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


def _copy_workspace_templates(workspace_dir: Path) -> None:
    template_dir = Path(__file__).resolve().parents[1] / "workspace"
    for template_name in TEMPLATE_FILES:
        target = workspace_dir / template_name
        if target.exists():
            continue
        source = template_dir / template_name
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


def _write_persona_files(
    workspace_dir: Path,
    assistant_name: str,
    personality: str,
    user_name: str,
    timezone_name: str,
    preferences: str,
) -> None:
    (workspace_dir / "SOUL.md").write_text(
        f"# Persona\n\nYou are {assistant_name}.\n\n## Personality\n{personality}\n",
        encoding="utf-8",
    )
    (workspace_dir / "USER.md").write_text(
        f"# User Profile\n\nName: {user_name}\nTimezone: {timezone_name}\nPreferences: {preferences}\n",
        encoding="utf-8",
    )
    (workspace_dir / "IDENTITY.md").write_text(
        f"# Identity\n\nName: {assistant_name}\nTagline: A local-first autonomous assistant.\n",
        encoding="utf-8",
    )


def _write_env_file(updates: dict[str, str]) -> None:
    env_path = Path.cwd() / ".env"
    existing: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if not line or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            existing[key.strip()] = value
    for key, value in updates.items():
        if value:
            existing[key] = value
    lines = [f"{key}={value}" for key, value in sorted(existing.items())]
    env_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


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


async def _validate_gemini_api_key(api_key: str, model_name: str) -> tuple[bool, str]:
    url = "https://generativelanguage.googleapis.com/v1beta/models"
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(url, params={"key": api_key})
    if response.is_success:
        return True, f"Connected successfully. Model preference: {model_name}"
    return False, f"Validation failed with HTTP {response.status_code}"


async def _validate_telegram_token(token: str) -> tuple[bool, str]:
    try:
        from aiogram import Bot
    except Exception as exc:
        return False, f"aiogram is unavailable: {exc}"
    bot = Bot(token=token)
    try:
        profile = await bot.get_me()
        return True, f"Connected as @{profile.username or profile.first_name}"
    except Exception as exc:
        return False, str(exc)
    finally:
        await bot.session.close()


def _print_validation(label: str, ok: bool, detail: str) -> None:
    color = "green" if ok else "yellow"
    status = "PASS" if ok else "WARN"
    console.print(f"[{color}]{status}[/{color}] {label}: {detail}")
