"""Minimal ACP client for interoperating with local external agents."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


@dataclass(slots=True)
class ACPAgent:
    name: str
    url: str
    capabilities: list[str]


class ACPClient:
    def __init__(self, config) -> None:
        self.config = config
        self.registry_path: Path = config.acp_registry_path

    def discover_agents(self) -> list[ACPAgent]:
        if not self.registry_path.exists():
            return []
        try:
            payload = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        agents: list[ACPAgent] = []
        if not isinstance(payload, list):
            return agents
        for item in payload:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            url = str(item.get("url", "")).strip()
            if not name or not url:
                continue
            capabilities = [str(capability) for capability in item.get("capabilities", [])]
            agents.append(ACPAgent(name=name, url=url, capabilities=capabilities))
        return agents

    async def send_task(self, agent_name: str, task: str) -> dict[str, Any]:
        target = next((agent for agent in self.discover_agents() if agent.name == agent_name), None)
        if target is None:
            raise RuntimeError(f"ACP agent '{agent_name}' is not registered in {self.registry_path}.")

        endpoint = f"{target.url.rstrip('/')}/tasks"
        chunks: list[str] = []
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream("POST", endpoint, json={"task": task}) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    text = self._extract_text(line)
                    if text:
                        chunks.append(text)
                if not chunks:
                    raw_body = await response.aread()
                    if raw_body:
                        chunks.append(raw_body.decode("utf-8", errors="replace"))

        return {
            "agent_name": target.name,
            "capabilities": target.capabilities,
            "result": "".join(chunks).strip(),
        }

    def _extract_text(self, line: str) -> str:
        normalized = line.removeprefix("data:").strip()
        try:
            payload = json.loads(normalized)
        except json.JSONDecodeError:
            return normalized
        if isinstance(payload, dict):
            for key in ("text", "delta", "chunk", "content", "message"):
                if key in payload and payload[key]:
                    return str(payload[key])
        return ""
