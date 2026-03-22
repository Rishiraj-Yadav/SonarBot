from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return slug or "session-summary"


async def handle(event) -> None:
    session_path = event.context.get("session_path")
    workspace_dir = event.context.get("workspace_dir")
    if not session_path or not workspace_dir:
        return

    path = Path(session_path)
    if not path.exists():
        return

    lines = path.read_text(encoding="utf-8").splitlines()[-15:]
    if not lines:
        return

    preview = []
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("record_type") == "message":
            preview.append(f"{record.get('role', 'user')}: {record.get('content', '')}")

    if not preview:
        return

    title = _slugify(" ".join(preview[:2])[:80])
    target = Path(workspace_dir) / "memory" / f"{datetime.now(timezone.utc).date().isoformat()}-{title}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(preview) + "\n", encoding="utf-8")
