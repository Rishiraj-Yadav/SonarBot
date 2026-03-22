from __future__ import annotations

import json
from pathlib import Path


async def handle(event) -> None:
    log_path = Path(event.context["logs_dir"]) / "commands.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle_file:
        handle_file.write(
            json.dumps(
                {
                    "timestamp": event.timestamp,
                    "action": event.action,
                    "session_key": event.session_key,
                    "context": event.context,
                },
                ensure_ascii=False,
            )
            + "\n"
        )
