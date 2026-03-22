from __future__ import annotations

from pathlib import Path


async def handle(event) -> None:
    workspace_dir = Path(event.context["workspace_dir"])
    boot_path = workspace_dir / "BOOT.md"
    if not boot_path.exists():
        return
    event.messages.append(
        {
            "message": boot_path.read_text(encoding="utf-8").strip(),
            "session_key": "main",
            "silent": True,
        }
    )
