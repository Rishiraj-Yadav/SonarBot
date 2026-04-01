"""Helpers for normalizing visual target candidates and actions."""

from __future__ import annotations

import re
from typing import Any


def normalize_target_label(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def clamp_confidence(value: Any, default: float = 0.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, numeric))


def clamp_normalized_coordinate(value: Any, default: int = 500) -> int:
    try:
        numeric = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(0, min(1000, numeric))


def sanitize_candidate(candidate: dict[str, Any]) -> dict[str, Any] | None:
    label = str(candidate.get("label") or candidate.get("text") or "").strip()
    if not label:
        return None
    kind = str(candidate.get("kind") or candidate.get("type") or "unknown").strip().lower() or "unknown"
    click_action = str(candidate.get("click_action") or candidate.get("action") or "click").strip().lower()
    if click_action not in {"click", "double_click"}:
        click_action = "click"
    sanitized = {
        "label": label,
        "normalized_label": normalize_target_label(label),
        "kind": kind,
        "confidence": clamp_confidence(candidate.get("confidence"), default=0.5),
        "x": clamp_normalized_coordinate(candidate.get("x"), default=500),
        "y": clamp_normalized_coordinate(candidate.get("y"), default=500),
        "click_action": click_action,
        "backend": str(candidate.get("backend", "unknown")).strip().lower() or "unknown",
    }
    if isinstance(candidate.get("bbox"), dict):
        sanitized["bbox"] = {
            "left": int(candidate["bbox"].get("left", 0) or 0),
            "top": int(candidate["bbox"].get("top", 0) or 0),
            "right": int(candidate["bbox"].get("right", 0) or 0),
            "bottom": int(candidate["bbox"].get("bottom", 0) or 0),
        }
    if "selected" in candidate:
        sanitized["selected"] = bool(candidate.get("selected"))
    return sanitized


def sanitize_candidates(candidates: list[dict[str, Any]] | Any, *, limit: int) -> list[dict[str, Any]]:
    if not isinstance(candidates, list):
        return []
    sanitized: list[dict[str, Any]] = []
    seen_labels: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        normalized = sanitize_candidate(candidate)
        if normalized is None:
            continue
        key = normalized["normalized_label"]
        if key in seen_labels:
            continue
        seen_labels.add(key)
        sanitized.append(normalized)
        if len(sanitized) >= max(1, limit):
            break
    return sanitized


def build_click_payload(
    *,
    x_norm: Any,
    y_norm: Any,
    count: int,
    state: dict[str, Any],
    expected_window_title: str = "",
    expected_process_name: str = "",
) -> dict[str, Any]:
    width = max(1, int(state.get("capture_width", 0) or state.get("width", 0) or 1))
    height = max(1, int(state.get("capture_height", 0) or state.get("height", 0) or 1))
    x = round((clamp_normalized_coordinate(x_norm) / 1000.0) * width)
    y = round((clamp_normalized_coordinate(y_norm) / 1000.0) * height)
    coordinate_space = "active_window" if str(state.get("capture_target", "window")).lower() == "window" else "screen"
    payload: dict[str, Any] = {
        "x": x,
        "y": y,
        "count": max(1, min(int(count), 2)),
        "coordinate_space": coordinate_space,
        "coworker_low_risk_visual": True,
    }
    if expected_window_title:
        payload["expected_window_title"] = expected_window_title
    if expected_process_name:
        payload["expected_process_name"] = expected_process_name
    return payload
