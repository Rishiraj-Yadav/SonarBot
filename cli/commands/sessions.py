"""Session inspection commands."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from assistant.agent.session_manager import SessionManager
from assistant.config import load_config

app = typer.Typer(help="Inspect stored sessions.")
console = Console()


@app.command("list")
def list_sessions() -> None:
    asyncio.run(_list_sessions())


@app.command("view")
def view_session(session_id: str) -> None:
    asyncio.run(_view_session(session_id))


@app.command("export")
def export_session(session_id: str, output: str | None = None) -> None:
    asyncio.run(_export_session(session_id, output))


async def _list_sessions() -> None:
    config = load_config()
    manager = SessionManager(config)
    rows = await manager.list_sessions()
    table = Table(title="Sessions")
    table.add_column("Session Key")
    table.add_column("Session ID")
    table.add_column("Tokens")
    table.add_column("Last Active")
    for row in rows:
        table.add_row(row["session_key"], row["session_id"], str(row["token_count"]), str(row["last_active"]))
    console.print(table)


async def _view_session(session_id: str) -> None:
    config = load_config()
    manager = SessionManager(config)
    session = await manager.get_session_by_id(session_id)
    table = Table(title=f"Session {session_id}")
    table.add_column("Role")
    table.add_column("Content")
    for message in session["messages"]:
        table.add_row(str(message.get("role", "")), str(message.get("content", "")))
    console.print(table)


async def _export_session(session_id: str, output: str | None) -> None:
    config = load_config()
    manager = SessionManager(config)
    session = await manager.get_session_by_id(session_id)
    output_path = Path(output).expanduser().resolve() if output else Path.cwd() / f"{session_id}.md"
    lines = [f"# Session {session_id}", ""]
    for message in session["messages"]:
        lines.append(f"## {message.get('role', 'unknown')}")
        lines.append(str(message.get("content", "")))
        lines.append("")
    output_path.write_text("\n".join(lines), encoding="utf-8")
    console.print(f"[green]Exported to {output_path}[/green]")
