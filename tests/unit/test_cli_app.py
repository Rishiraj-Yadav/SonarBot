from __future__ import annotations

import pytest
import typer

from cli import app as cli_app


def test_start_runs_uvicorn_when_port_is_free(app_config, monkeypatch) -> None:
    monkeypatch.setattr(cli_app, "load_config", lambda: app_config)
    monkeypatch.setattr(cli_app, "_probe_gateway", lambda host, port: (False, None))
    called: dict[str, object] = {}

    def fake_run(target, *, host: str, port: int, log_level: str) -> None:
        called["target"] = target
        called["host"] = host
        called["port"] = port
        called["log_level"] = log_level

    monkeypatch.setattr(cli_app.uvicorn, "run", fake_run)

    cli_app.start()

    assert called["host"] == app_config.gateway.host
    assert called["port"] == app_config.gateway.port
    assert called["log_level"] == "info"


def test_start_exits_cleanly_when_gateway_already_running(app_config, monkeypatch) -> None:
    monkeypatch.setattr(cli_app, "load_config", lambda: app_config)
    monkeypatch.setattr(
        cli_app,
        "_probe_gateway",
        lambda host, port: (True, {"active_sessions": 2, "started_at": "2026-04-02T00:00:00Z"}),
    )
    printed: list[str] = []
    monkeypatch.setattr(cli_app.console, "print", lambda message, *args, **kwargs: printed.append(str(message)))

    with pytest.raises(typer.Exit) as exc_info:
        cli_app.start()
    assert exc_info.value.exit_code == 0

    assert any("already running" in line.lower() for line in printed)


def test_start_fails_cleanly_when_port_is_occupied_by_other_process(app_config, monkeypatch) -> None:
    monkeypatch.setattr(cli_app, "load_config", lambda: app_config)
    monkeypatch.setattr(cli_app, "_probe_gateway", lambda host, port: (True, None))
    printed: list[str] = []
    monkeypatch.setattr(cli_app.console, "print", lambda message, *args, **kwargs: printed.append(str(message)))

    with pytest.raises(typer.Exit) as exc_info:
        cli_app.start()
    assert exc_info.value.exit_code == 1

    assert any("already in use" in line.lower() for line in printed)
