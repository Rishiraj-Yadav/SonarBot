"""App-aware verification helpers and keyboard fallback recipes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from assistant.desktop_coworker.targeting import normalize_target_label


def detect_surface(state: dict[str, Any]) -> str:
    active_window = state.get("active_window", {}) if isinstance(state.get("active_window"), dict) else {}
    title = normalize_target_label(str(active_window.get("title", "")))
    process_name = normalize_target_label(str(active_window.get("process_name", "")))
    if process_name == "explorer" or "fileexplorer" in title:
        if "saveas" in title or "open" in title:
            return "explorer_dialog"
        return "explorer"
    if process_name == "excel":
        return "excel"
    if process_name == "word":
        return "word"
    if process_name in {"systemsettings", "settings"} or "bluetoothdevices" in title or title.endswith("settings"):
        return "settings"
    return "generic"


def verification_hint_for_path(path: str, *, opener_alias: str = "") -> dict[str, str]:
    normalized_path = Path(path.replace("\\", "/"))
    stem = normalized_path.stem
    name = normalized_path.name
    if normalized_path.is_dir():
        return {
            "expected_window_title": normalized_path.name,
            "expected_process_name": "explorer",
            "expected_text_after": normalized_path.name,
        }
    if opener_alias == "excel" or normalized_path.suffix.lower() in {".xlsx", ".xls", ".xlsm", ".csv"}:
        return {
            "expected_window_title": stem,
            "expected_process_name": "excel",
            "expected_text_after": stem,
        }
    if opener_alias == "word" or normalized_path.suffix.lower() in {".doc", ".docx"}:
        return {
            "expected_window_title": stem,
            "expected_process_name": "word",
            "expected_text_after": stem,
        }
    return {
        "expected_window_title": stem or name,
        "expected_text_after": stem or name,
    }


def keyboard_fallback_recipe(
    *,
    action: dict[str, Any],
    before: dict[str, Any],
    after: dict[str, Any],
    attempts_used: int,
) -> dict[str, Any] | None:
    if attempts_used > 0:
        return None
    target_label = str(action.get("target_label", "")).strip()
    surface = detect_surface(before)
    normalized_label = normalize_target_label(target_label)
    if surface == "explorer_dialog":
        if normalized_label in {"save", "open"}:
            return {
                "type": "press_hotkey",
                "hotkey": "enter",
                "reason": f"Use Enter to confirm the visible {target_label or 'dialog action'}.",
                "target_label": target_label or "dialog-confirm",
                "confidence": 0.74,
                "goal_completed_if_verified": True,
            }
        if normalized_label in {"cancel", "close"}:
            return {
                "type": "press_hotkey",
                "hotkey": "esc",
                "reason": f"Use Escape to dismiss the visible {target_label or 'dialog action'}.",
                "target_label": target_label or "dialog-cancel",
                "confidence": 0.74,
                "goal_completed_if_verified": True,
            }
    if surface == "explorer" and normalized_label in {"desktop", "downloads", "documents", "pictures", "music", "videos"}:
        return {
            "type": "press_hotkey",
            "hotkey": "enter",
            "reason": f"Use Enter to open the selected Explorer item '{target_label}'.",
            "target_label": target_label,
            "confidence": 0.7,
            "goal_completed_if_verified": True,
        }
    if surface in {"excel", "word"} and target_label:
        # Prefer Enter on the selected recent file row when the click did not verify.
        return {
            "type": "press_hotkey",
            "hotkey": "enter",
            "reason": f"Use Enter to open the selected recent item '{target_label}'.",
            "target_label": target_label,
            "confidence": 0.68,
            "goal_completed_if_verified": True,
        }
    if surface == "settings" and normalized_label in {"on", "off", "bluetooth"}:
        return {
            "type": "press_hotkey",
            "hotkey": "tab",
            "reason": f"Use Tab to move focus to the visible Bluetooth control near '{target_label or 'Bluetooth'}'.",
            "target_label": target_label or "Bluetooth",
            "confidence": 0.62,
            "goal_completed_if_verified": False,
        }
    return None


def verify_surface_transition(
    *,
    action: dict[str, Any],
    before: dict[str, Any],
    after: dict[str, Any],
    tool_result: dict[str, Any],
) -> dict[str, Any] | None:
    surface = detect_surface(after or before)
    target_label = normalize_target_label(str(action.get("target_label", "")))
    expected_window_title = normalize_target_label(str(action.get("expected_window_title", "")))
    expected_process_name = normalize_target_label(str(action.get("expected_process_name", "")))
    expected_text_after = normalize_target_label(str(action.get("expected_text_after", "")))
    before_window = before.get("active_window", {}) if isinstance(before.get("active_window"), dict) else {}
    after_window = after.get("active_window", {}) if isinstance(after.get("active_window"), dict) else {}
    after_title = normalize_target_label(str(after_window.get("title", "")))
    after_process = normalize_target_label(str(after_window.get("process_name", "")))
    before_title = normalize_target_label(str(before_window.get("title", "")))
    after_text = normalize_target_label(str(after.get("screen_text", "")))
    before_text = normalize_target_label(str(before.get("screen_text", "")))

    if surface == "explorer":
        if expected_window_title and expected_window_title in after_title and after_title != before_title:
            return {"ok": True, "kind": "explorer_path", "message": ""}
        if target_label and target_label in after_text and after_text != before_text and target_label != normalize_target_label(str(before_window.get("title", ""))):
            return {"ok": True, "kind": "explorer_path", "message": ""}
        if str(tool_result.get("execution_mode", "")).strip().lower() in {"open_known_folder", "open_known_path"}:
            return {
                "ok": False,
                "kind": "explorer_path",
                "message": f"The Explorer surface did not visibly navigate to '{action.get('target_label', '')}'.",
            }
    if surface in {"excel", "word"}:
        if expected_window_title and expected_window_title in after_title and after_title != before_title:
            return {"ok": True, "kind": f"{surface}_window", "message": ""}
        if expected_process_name and expected_process_name == after_process and expected_text_after and expected_text_after in after_text:
            return {"ok": True, "kind": f"{surface}_window", "message": ""}
        if target_label and target_label in after_text and after_text != before_text:
            return {"ok": True, "kind": f"{surface}_window", "message": ""}
    if surface == "settings":
        if expected_text_after and expected_text_after in after_text and after_text != before_text:
            return {"ok": True, "kind": "settings_state", "message": ""}
        if target_label in {"on", "off"} and target_label not in after_text and after_text != before_text:
            return {"ok": True, "kind": "settings_state", "message": ""}
        if expected_window_title and expected_window_title in after_title:
            return {"ok": True, "kind": "settings_window", "message": ""}
    return None
