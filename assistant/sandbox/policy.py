"""Sandbox policy definitions."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class SandboxPolicy:
    allowed_tools: list[str] = field(default_factory=lambda: ["exec_shell"])
    network_mode: str = "disabled"
    max_execution_time: int = 30
