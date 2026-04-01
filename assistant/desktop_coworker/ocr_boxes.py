"""OCR text boxes for coworker candidate extraction."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from assistant.desktop_coworker.targeting import clamp_confidence, clamp_normalized_coordinate, normalize_target_label
from assistant.tools.image_ocr import extract_text_boxes, ocr_box_backend_health


def _resolve_capture_path(config, capture_path: str) -> Path:
    raw_path = Path(capture_path).expanduser()
    candidates: list[Path] = []
    if raw_path.is_absolute():
        candidates.append(raw_path)
    else:
        workspace_dir = Path(config.agent.workspace_dir).expanduser()
        candidates.extend([raw_path, workspace_dir / raw_path, workspace_dir.parent / raw_path])
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.exists():
            return resolved
    return candidates[0].resolve() if candidates else raw_path.resolve()


def _infer_kind(line: str, active_window: dict[str, Any]) -> str:
    normalized = normalize_target_label(line)
    process_name = normalize_target_label(str(active_window.get("process_name", "")))
    title = normalize_target_label(str(active_window.get("title", "")))
    if process_name == "explorer" or "fileexplorer" in title:
        if normalized in {"desktop", "downloads", "documents", "pictures", "music", "videos"}:
            return "folder"
        if re.search(r"\.[a-z0-9]{2,5}$", line):
            return "file"
        return "row"
    if process_name in {"excel", "word"}:
        if re.search(r"\.(xlsx?|csv|docx?|txt|md)$", line, re.IGNORECASE):
            return "file"
        if normalized not in {
            "recent",
            "sharedwithme",
            "favorites",
            "blankworkbook",
            "new",
            "open",
            "home",
            "goodevening",
            "goodmorning",
            "goodafternoon",
        } and len(normalized) >= 4:
            return "file"
        return "row"
    if process_name in {"systemsettings", "settings"} or "bluetoothdevices" in title or title.endswith("settings"):
        if normalized in {"on", "off"}:
            return "toggle"
        if normalized in {"adddevice", "save", "open", "cancel", "done"}:
            return "button"
        if normalized in {"bluetooth", "devices", "audio", "input"}:
            return "row"
        return "button"
    return "unknown"


def extract_ocr_box_observation(state: dict[str, Any], *, limit: int = 8, config=None, max_chars: int = 12000) -> dict[str, Any]:
    capture_path = str(state.get("capture_path", "")).strip()
    if not capture_path or config is None:
        return {"screen_text": "", "candidates": []}
    health = ocr_box_backend_health()
    if not health.get("available", False):
        return {"screen_text": "", "candidates": []}
    resolved_path = _resolve_capture_path(config, capture_path)
    if not resolved_path.exists():
        return {"screen_text": "", "candidates": []}

    capture_width = max(1, int(state.get("capture_width", 0) or state.get("width", 0) or 1))
    capture_height = max(1, int(state.get("capture_height", 0) or state.get("height", 0) or 1))
    active_window = state.get("active_window", {}) if isinstance(state.get("active_window"), dict) else {}
    text_boxes = extract_text_boxes(resolved_path)
    if not text_boxes:
        return {"screen_text": "", "candidates": []}

    results: list[dict[str, Any]] = []
    text_lines: list[str] = []
    for entry in text_boxes:
        label = str(entry.get("text", "")).strip()
        bbox = entry.get("bbox", {}) if isinstance(entry.get("bbox"), dict) else {}
        if not label or not bbox:
            continue
        left = int(bbox.get("left", 0) or 0)
        top = int(bbox.get("top", 0) or 0)
        right = int(bbox.get("right", 0) or 0)
        bottom = int(bbox.get("bottom", 0) or 0)
        if right - left < 2 or bottom - top < 2:
            continue
        text_lines.append(label)
        center_x = left + (right - left) / 2
        center_y = top + (bottom - top) / 2
        kind = _infer_kind(label, active_window)
        results.append(
            {
                "label": label,
                "kind": kind,
                "confidence": clamp_confidence(float(entry.get("confidence", 0.0)) / 100.0, default=0.5),
                "x": clamp_normalized_coordinate(round((center_x / capture_width) * 1000)),
                "y": clamp_normalized_coordinate(round((center_y / capture_height) * 1000)),
                "click_action": "double_click" if kind == "file" else "click",
                "backend": "ocr_boxes",
                "bbox": {
                    "left": left,
                    "top": top,
                    "right": right,
                    "bottom": bottom,
                },
                "selected": False,
            }
        )
        if len(results) >= max(1, limit * 4):
            break
    screen_text = "\n".join(text_lines).strip()
    if max_chars > 0 and len(screen_text) > max_chars:
        screen_text = screen_text[:max_chars].rstrip()
    return {"screen_text": screen_text, "candidates": results}


def extract_ocr_box_candidates(state: dict[str, Any], *, limit: int = 8, config=None) -> list[dict[str, Any]]:
    observation = extract_ocr_box_observation(state, limit=limit, config=config)
    return list(observation.get("candidates", []))


def extract_ocr_box_text(state: dict[str, Any], *, config=None, max_chars: int = 12000) -> str:
    observation = extract_ocr_box_observation(state, limit=8, config=config, max_chars=max_chars)
    return str(observation.get("screen_text", ""))
