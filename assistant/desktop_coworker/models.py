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


@dataclass(slots=True)
class DesktopRequestAnalysis:
    desktop_ui_task: bool
    task_kind: str = "non_desktop"
    summary: str = ""
    normalized_request: str = ""
    requires_visual_context: bool = False
    route_kind: str = "none"

    def to_dict(self) -> dict[str, Any]:
        return {
            "desktop_ui_task": bool(self.desktop_ui_task),
            "task_kind": str(self.task_kind),
            "summary": str(self.summary),
            "normalized_request": str(self.normalized_request),
            "requires_visual_context": bool(self.requires_visual_context),
            "route_kind": str(self.route_kind),
        }


@dataclass(slots=True)
class DesktopInteractionContext:
    session_key: str
    task_id: str = ""
    request_text: str = ""
    summary: str = ""
    route_kind: str = ""
    active_window: dict[str, Any] = field(default_factory=dict)
    last_candidates: list[dict[str, Any]] = field(default_factory=list)
    last_screenshot: str = ""
    last_action: dict[str, Any] = field(default_factory=dict)
    latest_state: dict[str, Any] = field(default_factory=dict)
    status: str = ""
    updated_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_key": self.session_key,
            "task_id": self.task_id,
            "request_text": self.request_text,
            "summary": self.summary,
            "route_kind": self.route_kind,
            "active_window": dict(self.active_window),
            "last_candidates": [dict(item) for item in self.last_candidates],
            "last_screenshot": self.last_screenshot,
            "last_action": dict(self.last_action),
            "latest_state": dict(self.latest_state),
            "status": self.status,
            "updated_at": self.updated_at,
        }


def build_artifact(*, path: str, kind: str, label: str = "", created_at: str | None = None) -> dict[str, Any]:
    return {
        "artifact_id": uuid4().hex[:12],
        "path": str(path),
        "kind": str(kind),
        "label": str(label).strip(),
        "created_at": created_at or utc_now_iso(),
    }
