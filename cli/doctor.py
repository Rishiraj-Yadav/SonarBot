"""Diagnostic checks for SonarBot."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import httpx
from rich.console import Console
from rich.table import Table

from assistant.config import load_config
from cli.onboard import TEMPLATE_FILES
from cli.ws_client import GatewayClient

console = Console()


@dataclass(slots=True)
class DoctorResult:
    name: str
    status: str
    detail: str


def run_doctor() -> None:
    config = load_config()
    results = asyncio.run(_collect_results(config))
    table = Table(title="SonarBot Doctor")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail")
    for result in results:
        color = {"pass": "green", "warn": "yellow", "fail": "red"}.get(result.status, "white")
        table.add_row(result.name, f"[{color}]{result.status.upper()}[/{color}]", result.detail)
    console.print(table)


async def _collect_results(config) -> list[DoctorResult]:
    return [
        await _check_gateway(config),
        await _check_llm(config),
        _check_chromadb(config),
        await _check_telegram(config),
        _check_workspace_files(config.agent.workspace_dir),
        _check_docker(),
    ]


async def _check_gateway(config) -> DoctorResult:
    try:
        async with GatewayClient(config) as client:
            request_id = await client.send_request("health")
            while True:
                frame = await client.recv()
                if frame.get("type") == "res" and frame.get("id") == request_id:
                    if frame.get("ok"):
                        payload = frame.get("payload", {})
                        return DoctorResult(
                            "Gateway running",
                            "pass",
                            f"uptime={payload.get('uptime_seconds', 0)}s, model={payload.get('model', 'unknown')}",
                        )
                    return DoctorResult("Gateway running", "fail", str(frame.get("error", "Unknown error")))
    except Exception as exc:
        return DoctorResult("Gateway running", "fail", str(exc))


async def _check_llm(config) -> DoctorResult:
    if not config.llm.gemini_api_key:
        return DoctorResult("LLM API reachable", "fail", "Missing GEMINI_API_KEY.")
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                "https://generativelanguage.googleapis.com/v1beta/models",
                params={"key": config.llm.gemini_api_key},
            )
        if response.is_success:
            return DoctorResult("LLM API reachable", "pass", f"Gemini responded with HTTP {response.status_code}.")
        return DoctorResult("LLM API reachable", "fail", f"Gemini returned HTTP {response.status_code}.")
    except Exception as exc:
        return DoctorResult("LLM API reachable", "fail", str(exc))


def _check_chromadb(config) -> DoctorResult:
    if not config.memory.vector_enabled:
        return DoctorResult("ChromaDB initialized", "warn", "Vector memory disabled in config.")
    try:
        import chromadb  # type: ignore
    except Exception as exc:
        return DoctorResult("ChromaDB initialized", "warn", f"chromadb not installed: {exc}")
    try:
        client = chromadb.PersistentClient(path=str(config.chroma_dir))
        client.get_or_create_collection("assistant_memory")
        return DoctorResult("ChromaDB initialized", "pass", str(config.chroma_dir))
    except Exception as exc:
        return DoctorResult("ChromaDB initialized", "fail", str(exc))


async def _check_telegram(config) -> DoctorResult:
    if not config.telegram.bot_token:
        return DoctorResult("Telegram bot connected", "warn", "No Telegram bot token configured.")
    try:
        from aiogram import Bot
    except Exception as exc:
        return DoctorResult("Telegram bot connected", "fail", f"aiogram unavailable: {exc}")
    bot = Bot(token=config.telegram.bot_token)
    try:
        profile = await bot.get_me()
        return DoctorResult("Telegram bot connected", "pass", f"Connected as @{profile.username or profile.first_name}")
    except Exception as exc:
        return DoctorResult("Telegram bot connected", "fail", str(exc))
    finally:
        await bot.session.close()


def _check_workspace_files(workspace_dir: Path) -> DoctorResult:
    missing = [name for name in TEMPLATE_FILES if not (workspace_dir / name).exists()]
    if missing:
        return DoctorResult("Workspace files present", "fail", f"Missing: {', '.join(missing)}")
    return DoctorResult("Workspace files present", "pass", str(workspace_dir))


def _check_docker() -> DoctorResult:
    try:
        import docker  # type: ignore
    except Exception as exc:
        return DoctorResult("Docker available", "warn", f"docker SDK unavailable: {exc}")
    try:
        client = docker.from_env()
        client.ping()
        return DoctorResult("Docker available", "pass", "Docker daemon reachable.")
    except Exception as exc:
        return DoctorResult("Docker available", "warn", str(exc))
