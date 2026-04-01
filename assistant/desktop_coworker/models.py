"""Typed models for Phase 6 desktop coworker tasks."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class DesktopCoworkerStep:
    type: str
    title: str
    payload: dict[str, Any] = field(default_factory=dict)
    verification: dict[str, Any] = field(default_factory=dict)
    risky: bool = False
    continue_on_error: bool = False
    retryable: bool = True


@dataclass(slots=True)
class DesktopCoworkerTask:
    task_id: str
    user_id: str
    session_key: str
    request_text: str
    status: str
    summary: str
    steps: list[dict[str, Any]]
    current_step_index: int = 0
    latest_state: dict[str, Any] = field(default_factory=dict)
    transcript: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    completed_at: str = ""

    @classmethod
    def new(
        cls,
        *,
        user_id: str,
        session_key: str,
        request_text: str,
        summary: str,
        steps: list[dict[str, Any]],
        status: str = "planned",
    ) -> "DesktopCoworkerTask":
        return cls(
            task_id=uuid4().hex[:12],
            user_id=user_id,
            session_key=session_key,
            request_text=request_text,
            status=status,
            summary=summary,
            steps=steps,
        )
