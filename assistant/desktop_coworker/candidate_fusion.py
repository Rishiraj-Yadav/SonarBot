"""Merge UIA and OCR-box candidates into one ranked list."""

from __future__ import annotations

from typing import Any

from assistant.desktop_coworker.targeting import normalize_target_label, sanitize_candidate


_BACKEND_PRIORITY = {
    "uia": 0,
    "ocr_boxes": 1,
    "llm": 2,
    "unknown": 3,
}


def fuse_target_candidates(*candidate_lists: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for raw_candidates in candidate_lists:
        for candidate in raw_candidates:
            if not isinstance(candidate, dict):
                continue
            normalized = sanitize_candidate(candidate)
            if normalized is None:
                continue
            normalized["backend"] = str(candidate.get("backend", normalized.get("backend", "unknown"))).strip().lower() or "unknown"
            if isinstance(candidate.get("bbox"), dict):
                normalized["bbox"] = dict(candidate["bbox"])
            if "selected" in candidate:
                normalized["selected"] = bool(candidate.get("selected"))
            key = normalized.get("normalized_label") or normalize_target_label(normalized.get("label", ""))
            existing = deduped.get(key)
            if existing is None:
                deduped[key] = normalized
                continue
            existing_priority = _BACKEND_PRIORITY.get(str(existing.get("backend", "unknown")), 99)
            incoming_priority = _BACKEND_PRIORITY.get(str(normalized.get("backend", "unknown")), 99)
            if incoming_priority < existing_priority:
                deduped[key] = normalized
                continue
            if incoming_priority == existing_priority and float(normalized.get("confidence", 0.0)) > float(existing.get("confidence", 0.0)):
                deduped[key] = normalized
    ranked = sorted(
        deduped.values(),
        key=lambda item: (
            _BACKEND_PRIORITY.get(str(item.get("backend", "unknown")), 99),
            -float(item.get("confidence", 0.0)),
            str(item.get("label", "")).lower(),
        ),
    )
    return ranked[: max(1, limit)]
