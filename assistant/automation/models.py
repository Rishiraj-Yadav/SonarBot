"""Typed automation runtime models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class AutomationEvent:
    event_type: str
    user_id: str
    source: str
    payload: dict[str, Any]
    dedupe_key: str
    priority: int = 50
    created_at: str = field(default_factory=utc_now_iso)
    event_id: str = field(default_factory=lambda: uuid4().hex)


@dataclass(slots=True)
class AutomationRule:
    name: str
    trigger: str
    prompt_or_skill: str
    enabled: bool = True
    conditions: dict[str, Any] = field(default_factory=dict)
    action_policy: str = "notify_first"
    delivery_policy: str = "primary"
    cooldown_seconds: int = 0
    dedupe_window_seconds: int = 300
    quiet_hours_behavior: str = "queue"
    severity: str = "info"


@dataclass(slots=True)
class AutomationRun:
    run_id: str
    event_id: str
    user_id: str
    rule_name: str
    session_key: str
    status: str
    prompt: str
    created_at: str
    updated_at: str
    result_text: str = ""
    notification_id: str | None = None
    approval_state: str = "not_required"
    error: str = ""


@dataclass(slots=True)
class Notification:
    notification_id: str
    user_id: str
    title: str
    body: str
    source: str
    severity: str
    delivery_mode: str
    status: str
    target_channels: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)


@dataclass(slots=True)
class ApprovalRequest:
    approval_id: str
    user_id: str
    run_id: str
    action: str
    status: str
    payload: dict[str, Any]
    created_at: str = field(default_factory=utc_now_iso)
    decided_at: str = ""


@dataclass(slots=True)
class DynamicCronJob:
    cron_id: str
    user_id: str
    schedule: str
    message: str
    paused: bool = False
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
