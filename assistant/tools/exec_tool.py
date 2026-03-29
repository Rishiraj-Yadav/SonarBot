"""Async shell execution tool."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from assistant.tools.registry import ToolDefinition


def build_exec_tool(
    workspace_dir: Path,
    sandbox_runtime=None,
    sandbox_enabled: bool = False,
    system_access_manager=None,
) -> ToolDefinition:
    async def exec_shell(payload: dict[str, Any]) -> dict[str, Any]:
        command = str(payload["command"]).strip()
        timeout = int(payload.get("timeout", 30))
        use_sandbox = bool(payload.get("sandbox", False))
        host_mode = bool(payload.get("host", False))
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

        if host_mode:
            if system_access_manager is None:
                return {"stdout": "", "stderr": "Host system access is not configured.", "exit_code": 1, "host": True}
            try:
                return await system_access_manager.run_host_command(
                    command=command,
                    session_key=session_key,
                    session_id=str(payload.get("session_id", session_key)),
                    user_id=str(payload.get("user_id", "default")),
                    connection_id=str(payload.get("connection_id", "")),
                    channel_name=str(payload.get("channel_name", "")),
                    timeout=timeout,
                    workdir=str(payload.get("workdir", "")).strip() or None,
                )
            except RuntimeError as exc:
                return {"stdout": "", "stderr": str(exc), "exit_code": 1, "host": True}

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
        description="Execute a shell command in the workspace directory, or on the host when host=true.",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "integer", "minimum": 1, "default": 30},
                "sandbox": {"type": "boolean", "default": False},
                "host": {"type": "boolean", "default": False},
                "workdir": {"type": "string"},
            },
            "required": ["command"],
        },
        handler=exec_shell,
        persistence_policy="redacted",
        redactor=(
            (lambda tool_input, tool_result: system_access_manager.redact_tool_result("exec_shell", tool_input, tool_result))
            if system_access_manager is not None
            else None
        ),
    )
