"""Docker-backed sandbox execution."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class SandboxResult:
    stdout: str
    stderr: str
    exit_code: int


class SandboxContainer:
    def __init__(self, session_key: str, workspace_dir: Path, container: Any) -> None:
        self.session_key = session_key
        self.workspace_dir = workspace_dir
        self.container = container

    async def run(self, command: str, timeout: int) -> SandboxResult:
        def _exec() -> SandboxResult:
            result = self.container.exec_run(
                ["/bin/sh", "-lc", command],
                workdir="/workspace",
                demux=True,
            )
            exit_code = getattr(result, "exit_code", result[0] if isinstance(result, tuple) else 1)
            output = getattr(result, "output", result[1] if isinstance(result, tuple) and len(result) > 1 else (b"", b""))
            stdout_bytes, stderr_bytes = output if isinstance(output, tuple) else (output, b"")
            return SandboxResult(
                stdout=(stdout_bytes or b"").decode("utf-8", errors="replace"),
                stderr=(stderr_bytes or b"").decode("utf-8", errors="replace"),
                exit_code=int(exit_code),
            )

        return await asyncio.wait_for(asyncio.to_thread(_exec), timeout=timeout)

    async def close(self) -> None:
        await asyncio.to_thread(self.container.stop)
        await asyncio.to_thread(self.container.remove)


class SandboxRuntime:
    def __init__(self, config) -> None:
        self.config = config
        self._containers: dict[str, SandboxContainer] = {}

    async def spawn_sandbox(self, session_key: str) -> SandboxContainer:
        if session_key in self._containers:
            return self._containers[session_key]

        try:
            import docker  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("docker SDK is not installed.") from exc

        client = docker.from_env()
        host_workspace = self.config.sandbox_dir / session_key
        host_workspace.mkdir(parents=True, exist_ok=True)

        def _create():
            client.images.pull(self.config.sandbox.image)
            return client.containers.run(
                self.config.sandbox.image,
                command="sleep infinity",
                detach=True,
                tty=True,
                network_disabled=True,
                mem_limit=f"{self.config.sandbox.memory_limit_mb}m",
                nano_cpus=int(self.config.sandbox.cpu_limit * 1_000_000_000),
                volumes={str(host_workspace): {"bind": "/workspace", "mode": "rw"}},
            )

        container = await asyncio.to_thread(_create)
        sandbox = SandboxContainer(session_key=session_key, workspace_dir=host_workspace, container=container)
        self._containers[session_key] = sandbox
        return sandbox

    async def close(self) -> None:
        for sandbox in list(self._containers.values()):
            try:
                await sandbox.close()
            except Exception:
                pass
        self._containers.clear()
