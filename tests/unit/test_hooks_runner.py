from __future__ import annotations

import asyncio

from assistant.hooks.runner import HookRunner


def test_command_new_hook_handler_runs(app_config) -> None:
    hook_dir = app_config.agent.workspace_dir / "hooks" / "test_hook"
    hook_dir.mkdir(parents=True, exist_ok=True)
    (hook_dir / "HOOK.md").write_text("---\nevents:\n  - command:new\n---\n", encoding="utf-8")
    (hook_dir / "handler.py").write_text(
        "async def handle(event):\n    event.messages.append({'text': 'hook-ran'})\n",
        encoding="utf-8",
    )

    runner = HookRunner(app_config)
    runner.load_hooks()
    event = asyncio.run(runner.fire_event("command:new", {"session_key": "main"}))

    assert any((item.get("text") == "hook-ran") for item in event.messages)
