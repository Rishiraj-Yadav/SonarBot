"""Device registry commands."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.table import Table

from assistant.config import load_config
from assistant.gateway.device_registry import DeviceRegistry

app = typer.Typer(help="Manage known gateway devices.")
console = Console()


@app.command("list")
def list_devices() -> None:
    asyncio.run(_list_devices())


@app.command("approve")
def approve_device(device_id: str) -> None:
    asyncio.run(_approve(device_id))


@app.command("revoke")
def revoke_device(device_id: str) -> None:
    asyncio.run(_revoke(device_id))


async def _list_devices() -> None:
    registry = DeviceRegistry(load_config())
    devices = await registry.list_devices()
    table = Table(title="Devices")
    table.add_column("Device ID")
    table.add_column("Approved")
    table.add_column("Last Seen")
    for device in devices:
        table.add_row(str(device["device_id"]), str(device["approved"]), str(device["last_seen"]))
    console.print(table)


async def _approve(device_id: str) -> None:
    registry = DeviceRegistry(load_config())
    await registry.approve(device_id)
    console.print(f"[green]Approved {device_id}[/green]")


async def _revoke(device_id: str) -> None:
    registry = DeviceRegistry(load_config())
    await registry.revoke(device_id)
    console.print(f"[yellow]Revoked {device_id}[/yellow]")
