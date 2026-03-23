"""Core models for host-system access."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import uuid4


ApprovalCategory = Literal["auto_allow", "ask_once", "always_ask", "deny"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class HostApprovalRequest:
    user_id: str
    session_id: str
    session_key: str
    action_kind: str
    target_summary: str
    category: ApprovalCategory
    payload: dict[str, Any]
    connection_id: str = ""
    channel_name: str = ""
    approval_id: str = field(default_factory=lambda: uuid4().hex)
    status: str = "pending"
    created_at: str = field(default_factory=utc_now_iso)
    decided_at: str = ""
    expires_at: str = ""

    def __post_init__(self) -> None:
        if not self.expires_at:
            expiry = datetime.fromisoformat(self.created_at) + timedelta(minutes=5)
            self.expires_at = expiry.isoformat()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class HostAuditEntry:
    user_id: str
    session_id: str
    tool: str
    action_kind: str
    target: str
    category: ApprovalCategory
    approval_mode: str
    outcome: str
    duration_ms: int
    exit_code: int | None = None
    backup_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    audit_id: str = field(default_factory=lambda: uuid4().hex)
    timestamp: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class HostActionResult:
    status: str
    action_kind: str
    target: str
    category: ApprovalCategory
    approval_mode: str
    audit_id: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "status": self.status,
            "action_kind": self.action_kind,
            "target": self.target,
            "approval_category": self.category,
            "approval_mode": self.approval_mode,
            "audit_id": self.audit_id,
        }
        payload.update(self.details)
        return payload
