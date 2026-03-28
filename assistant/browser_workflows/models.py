"""Models used by browser autonomous workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


WorkflowStatus = Literal["completed", "blocked", "needs_followup", "error"]


@dataclass(slots=True)
class WorkflowPlanStep:
    name: str
    detail: str
    status: Literal["pending", "completed", "blocked"] = "pending"


@dataclass(slots=True)
class BlockingState:
    kind: str
    message: str
    url: str | None = None


@dataclass(slots=True)
class BrowserWorkflowMatch:
    recipe_name: str
    confidence: float
    site_name: str | None = None
    query: str | None = None
    action: str | None = None
    open_first_result: bool = False
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class BrowserWorkflowResult:
    recipe_name: str
    status: WorkflowStatus
    response_text: str
    progress_lines: list[str] = field(default_factory=list)
    steps: list[WorkflowPlanStep] = field(default_factory=list)
    state_update: dict[str, Any] | None = None
    clear_state: bool = False
    payload: dict[str, Any] = field(default_factory=dict)
