"""Session structures and message helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    total_chars = sum(len(str(message.get("content", ""))) for message in messages)
    return max(1, total_chars // 4) if messages else 0


def create_message(role: str, content: str, **extra: Any) -> dict[str, Any]:
    message = {
        "record_type": "message",
        "id": uuid4().hex,
        "role": role,
        "content": content,
        "created_at": utc_now_iso(),
    }
    message.update(extra)
    return message


@dataclass(slots=True)
class Session:
    session_id: str
    session_key: str
    messages: list[dict[str, Any]]
    persisted_messages: list[dict[str, Any]]
    token_count: int
    created_at: str
    updated_at: str
    storage_path: Path
    snapshot_path: Path
    metadata: dict[str, Any]

    def snapshot(self) -> None:
        payload = {
            "session_id": self.session_id,
            "session_key": self.session_key,
            "messages": self.persisted_messages,
            "token_count": self.token_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "snapshot_created_at": utc_now_iso(),
            "metadata": self.metadata,
        }
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        self.snapshot_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
