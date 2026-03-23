"""System-access audit inspection commands."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.table import Table

from assistant.config import load_config
from assistant.system_access import SystemAccessManager
from assistant.users import UserProfileStore

app = typer.Typer(help="Inspect host-system access audit entries.")
console = Console()


@app.command("list")
def list_audit(today: bool = typer.Option(False, "--today"), session: str | None = typer.Option(None, "--session")) -> None:
    asyncio.run(_list_audit(today=today, session_id=session))


@app.command("restore")
def restore_backup(backup_id: str) -> None:
    asyncio.run(_restore_backup(backup_id))


async def _list_audit(*, today: bool, session_id: str | None) -> None:
    config = load_config()
    manager = SystemAccessManager(config)
    await manager.initialize()
    rows = await manager.list_audit(today_only=today, session_id=session_id, limit=200)
    table = Table(title="System Access Audit")
    table.add_column("Time")
    table.add_column("Action")
    table.add_column("Target")
    table.add_column("Outcome")
    table.add_column("Approval")
    table.add_column("Backup")
    for row in rows:
        table.add_row(
            str(row.get("timestamp", "")),
            str(row.get("action_kind", "")),
            str(row.get("target", "")),
            str(row.get("outcome", "")),
            str(row.get("approval_mode", "")),
            str(row.get("backup_id", "") or ""),
        )
    console.print(table)


async def _restore_backup(backup_id: str) -> None:
    config = load_config()
    profiles = UserProfileStore(config)
    await profiles.initialize()
    manager = SystemAccessManager(config)
    await manager.initialize()
    result = await manager.restore_backup(backup_id, user_id=config.users.default_user_id)
    console.print(f"[green]Restored {result['restored_path']} from backup {backup_id}[/green]")
