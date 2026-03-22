"""Async shell execution tool."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from assistant.tools.registry import ToolDefinition


def build_exec_tool(workspace_dir: Path, sandbox_runtime=None, sandbox_enabled: bool = False) -> ToolDefinition:
    async def exec_shell(payload: dict[str, Any]) -> dict[str, Any]:
        command = str(payload["command"]).strip()
        timeout = int(payload.get("timeout", 30))
        use_sandbox = bool(payload.get("sandbox", False))
        session_key = str(payload.get("session_key", "default"))
        if not command:
            return {"stdout": "", "stderr": "Command cannot be empty.", "exit_code": 1}

        if use_sandbox and sandbox_enabled and sandbox_runtime is not None:
            sandbox = await sandbox_runtime.spawn_sandbox(session_key)
            try:
                result = await sandbox.run(command, timeout)
                return {
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "exit_code": result.exit_code,
                    "sandbox": True,
                }
            except asyncio.TimeoutError:
                return {"stdout": "", "stderr": f"Sandbox command timed out after {timeout} seconds.", "exit_code": -1, "sandbox": True}

        process = await asyncio.create_subprocess_shell(
            command,
            cwd=str(workspace_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return {"stdout": "", "stderr": f"Command timed out after {timeout} seconds.", "exit_code": -1}

        return {
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
            "exit_code": process.returncode,
        }

    return ToolDefinition(
        name="exec_shell",
        description="Execute a shell command in the workspace directory.",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "integer", "minimum": 1, "default": 30},
                "sandbox": {"type": "boolean", "default": False},
            },
            "required": ["command"],
        },
        handler=exec_shell,
    )
