# Hooks Guide

## Overview

Hooks are small async Python handlers discovered from bundled hooks, `~/.assistant/hooks`, and `workspace/hooks`.

Each hook directory contains:

- `HOOK.md` with YAML frontmatter declaring the events
- `handler.py` with `async def handle(event: HookEvent) -> None`

## Hook Event Shape

`HookEvent` includes:

- `type`
- `action`
- `session_key`
- `timestamp`
- `context`
- `messages`

Handlers can append items to `event.messages` to send follow-up text back through the runtime.

## Supported Events

Built-in events currently used by the runtime:

- `gateway:startup`
- `message:received`
- `command:new`
- `command:reset`
- `command:stop`
- `command:memory`
- `command:status`
- `command:skills`

## Example

`HOOK.md`

```md
---
events:
  - message:received
---
```

`handler.py`

```python
from __future__ import annotations

async def handle(event):
    preview = event.context.get("preview", "")
    event.messages.append({"text": f"Hook saw: {preview}"})
```

## Bundled Hooks

- `boot_md`
- `command_logger`
- `message_logger`
- `session_memory`

## Guidelines

- keep hooks fast
- catch and handle side-effect errors where possible
- treat `event.context` as runtime-owned input
- only append user-visible messages when that is actually useful
