"""Typer CLI for SonarBot."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer
import uvicorn
from websockets.exceptions import ConnectionClosed
from rich.console import Console

from assistant.config import load_config
from assistant.main import app as asgi_app
from cli.commands.devices import app as devices_app
from cli.commands.sessions import app as sessions_app
from cli.onboard import run_onboarding
from cli.ws_client import GatewayClient

app = typer.Typer(help="SonarBot Phase 4 CLI")
app.add_typer(devices_app, name="devices")
app.add_typer(sessions_app, name="sessions")
console = Console()


@app.command()
def onboard() -> None:
    """Create local config and workspace templates."""
    run_onboarding()


@app.command()
def start() -> None:
    """Start the gateway daemon."""
    config = load_config()
    uvicorn.run(asgi_app, host=config.gateway.host, port=config.gateway.port, log_level="info")


@app.command()
def status() -> None:
    """Query gateway health over WebSocket."""
    asyncio.run(_status())


@app.command()
def chat(session_key: str = typer.Option("main", help="Session key to use.")) -> None:
    """Open a local interactive chat REPL."""
    asyncio.run(_chat(session_key))


async def _status() -> None:
    config = load_config()
    try:
        async with GatewayClient(config) as client:
            request_id = await client.send_request("health")
            while True:
                frame = await client.recv()
                if frame.get("type") == "res" and frame.get("id") == request_id:
                    if frame.get("ok"):
                        console.print(frame.get("payload", {}))
                    else:
                        console.print(f"[red]{frame.get('error', 'Unknown error')}[/red]")
                    return
    except OSError as exc:
        console.print(f"[red]Could not connect to the gateway: {exc}[/red]")


async def _chat(session_key: str) -> None:
    config = load_config()
    try:
        async with GatewayClient(config) as client:
            console.print("[cyan]Connected. Type 'exit' to quit.[/cyan]")
            while True:
                user_input = console.input("[bold green]You[/bold green]: ")
                if user_input.lower() in {"exit", "quit"}:
                    return

                request_id = await client.send_request("agent.send", {"message": user_input, "session_key": session_key})
                started_stream = False

                console.print("[bold cyan]Assistant[/bold cyan]: ", end="")
                while True:
                    frame = await client.recv()
                    if frame.get("type") == "res" and frame.get("id") == request_id:
                        if not frame.get("ok"):
                            console.print(frame.get("error", "Unknown error"), style="red")
                            break
                        continue

                    if frame.get("type") != "event":
                        continue

                    if frame.get("event") == "agent.chunk":
                        started_stream = True
                        console.print(frame.get("payload", {}).get("text", ""), end="", soft_wrap=True, highlight=False)
                    elif frame.get("event") == "agent.done":
                        if not started_stream:
                            console.print("(no response)", end="")
                        break

                console.print("")
    except ConnectionClosed as exc:
        console.print("")
        console.print(
            f"[red]Gateway connection closed unexpectedly ({exc}). Restart `uv run assistant start` and reconnect.[/red]"
        )
    except OSError as exc:
        console.print(f"[red]Could not connect to the gateway: {exc}[/red]")


if __name__ == "__main__":
    app()
