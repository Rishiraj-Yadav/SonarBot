from __future__ import annotations

from types import SimpleNamespace

import pytest

from assistant.sandbox import SandboxRuntime
from assistant.tools.exec_tool import build_exec_tool


class FakeExecResult:
    def __init__(self) -> None:
        self.exit_code = 0
        self.output = (b"sandbox stdout", b"")


class FakeContainer:
    def __init__(self) -> None:
        self.exec_commands = []
        self.stopped = False
        self.removed = False

    def exec_run(self, command, workdir=None, demux=None):
        self.exec_commands.append({"command": command, "workdir": workdir, "demux": demux})
        return FakeExecResult()

    def stop(self):
        self.stopped = True

    def remove(self):
        self.removed = True


class FakeDockerClient:
    def __init__(self) -> None:
        self.images = SimpleNamespace(pull=lambda image: image)
        self.container = FakeContainer()
        self.containers = SimpleNamespace(run=lambda *args, **kwargs: self.container)


@pytest.mark.asyncio
async def test_sandboxed_exec_routes_through_container_api(app_config, monkeypatch) -> None:
    fake_client = FakeDockerClient()
    fake_module = SimpleNamespace(from_env=lambda: fake_client)
    monkeypatch.setitem(__import__("sys").modules, "docker", fake_module)

    runtime = SandboxRuntime(app_config)
    tool = build_exec_tool(app_config.agent.workspace_dir, sandbox_runtime=runtime, sandbox_enabled=True)
    result = await tool.handler({"command": "echo hi", "timeout": 5, "sandbox": True, "session_key": "main"})

    assert result["exit_code"] == 0
    assert result["sandbox"] is True
    assert fake_client.container.exec_commands
