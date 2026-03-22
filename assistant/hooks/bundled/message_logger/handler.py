from __future__ import annotations

import json
from pathlib import Path


async def handle(event) -> None:
    log_path = Path(event.context["logs_dir"]) / "messages.log"
    preview = str(event.context.get("preview", ""))[:120]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle_file:
        handle_file.write(
            json.dumps(
                {
                    "timestamp": event.timestamp,
                    "session_key": event.session_key,
                    "sender": event.context.get("sender_id"),
                    "channel": event.context.get("channel"),
                    "preview": preview,
                },
                ensure_ascii=False,
            )
            + "\n"
        )
