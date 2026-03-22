"""Hook discovery and execution."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from assistant.hooks.types import HookEvent
from assistant.skills.loader import parse_frontmatter


@dataclass(slots=True)
class LoadedHook:
    name: str
    path: Path
    events: list[str]
    handler: Callable[[HookEvent], Awaitable[None]]


class HookRunner:
    def __init__(self, config) -> None:
        self.config = config
        self._hooks_by_event: dict[str, list[LoadedHook]] = {}

    @property
    def hook_dirs(self) -> list[Path]:
        return [
            Path(__file__).resolve().parent / "bundled",
            self.config.hooks_home,
            self.config.agent.workspace_dir / "hooks",
        ]

    def load_hooks(self) -> None:
        hooks_by_event: dict[str, list[LoadedHook]] = {}
        for base_dir in self.hook_dirs:
            base_dir.mkdir(parents=True, exist_ok=True)
            for child in sorted(base_dir.iterdir()):
                if not child.is_dir():
                    continue
                loaded_hook = self._load_hook(child)
                if loaded_hook is None:
                    continue
                for event_name in loaded_hook.events:
                    hooks_by_event.setdefault(event_name, []).append(loaded_hook)
        self._hooks_by_event = hooks_by_event

    async def fire_event(self, event_key: str, context: dict[str, Any] | None = None) -> HookEvent:
        event_type, _, action = event_key.partition(":")
        hook_event = HookEvent(
            type=event_type or event_key,
            action=action or event_key,
            session_key=str((context or {}).get("session_key", "main")),
            context=context or {},
        )
        for hook in self._hooks_by_event.get(event_key, []) + self._hooks_by_event.get(event_type, []):
            try:
                await hook.handler(hook_event)
            except Exception:
                continue
        return hook_event

    def _load_hook(self, hook_dir: Path) -> LoadedHook | None:
        metadata_file = hook_dir / "HOOK.md"
        handler_file = hook_dir / "handler.py"
        if not metadata_file.exists() or not handler_file.exists():
            return None

        frontmatter, _body = parse_frontmatter(metadata_file.read_text(encoding="utf-8"))
        events = frontmatter.get("events", []) or []
        if not events:
            return None

        module_name = f"assistant_hook_{hook_dir.name}_{hash(handler_file)}"
        spec = importlib.util.spec_from_file_location(module_name, handler_file)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        handler = getattr(module, "handle", None)
        if handler is None:
            return None

        return LoadedHook(name=hook_dir.name, path=hook_dir, events=list(events), handler=handler)
