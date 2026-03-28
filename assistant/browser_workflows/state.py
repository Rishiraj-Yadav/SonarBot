"""Helpers for structured browser task state."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


BROWSER_TASK_STATE_KEY = "browser_task_state"
LEGACY_BROWSER_WORKFLOW_STATE_KEY = "browser_workflow_state"


def empty_browser_task_state() -> dict[str, Any]:
    return {
        "active_task": {},
        "pending_confirmation": {},
        "pending_login": {},
        "next_task_mode_override": "",
    }


def normalize_browser_task_state(raw: dict[str, Any] | None) -> dict[str, Any]:
    normalized = empty_browser_task_state()
    if not isinstance(raw, dict):
        return normalized

    if any(key in raw for key in normalized):
        for key in normalized:
            value = raw.get(key)
            if key == "next_task_mode_override":
                normalized[key] = str(value or "").strip().lower()
            else:
                normalized[key] = deepcopy(value) if isinstance(value, dict) else {}
    else:
        normalized["active_task"] = deepcopy(raw)

    if not normalized["active_task"] and isinstance(raw.get(LEGACY_BROWSER_WORKFLOW_STATE_KEY), dict):
        normalized["active_task"] = deepcopy(raw[LEGACY_BROWSER_WORKFLOW_STATE_KEY])

    if not normalized["pending_confirmation"]:
        active = normalized["active_task"]
        if str(active.get("awaiting_followup", "")).strip().lower() == "confirmation":
            normalized["pending_confirmation"] = {
                "action_type": str(active.get("action_type", "") or "submit"),
                "selector": str(active.get("selector", "")),
                "target": str(active.get("target_url", "") or active.get("active_url", "")),
                "site_name": str(active.get("site_name", "")),
            }
    if not normalized["pending_login"]:
        active = normalized["active_task"]
        if str(active.get("blocked_reason", "")).strip().lower() in {"login_required", "login"}:
            normalized["pending_login"] = {
                "site_name": str(active.get("site_name", "")),
                "target_url": str(active.get("target_url", "") or active.get("active_url", "")),
                "execution_mode": str(active.get("execution_mode", "")),
            }

    return normalized


def active_browser_task(state: dict[str, Any] | None) -> dict[str, Any]:
    return dict(normalize_browser_task_state(state).get("active_task", {}))


def browser_task_state_update(
    *,
    active_task: dict[str, Any] | None = None,
    pending_confirmation: dict[str, Any] | None = None,
    pending_login: dict[str, Any] | None = None,
    next_task_mode_override: str | None = None,
) -> dict[str, Any]:
    state = empty_browser_task_state()
    if active_task:
        state["active_task"] = deepcopy(active_task)
    if pending_confirmation:
        state["pending_confirmation"] = deepcopy(pending_confirmation)
    if pending_login:
        state["pending_login"] = deepcopy(pending_login)
    if next_task_mode_override:
        state["next_task_mode_override"] = str(next_task_mode_override).strip().lower()
    return {
        BROWSER_TASK_STATE_KEY: state,
        LEGACY_BROWSER_WORKFLOW_STATE_KEY: deepcopy(state["active_task"]),
    }


def browser_task_state_clear_keys() -> list[str]:
    return [BROWSER_TASK_STATE_KEY, LEGACY_BROWSER_WORKFLOW_STATE_KEY]
