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
    if process_name in {"whatsapp", "whatsapproot"} or "whatsapp" in title:
        return "whatsapp"
    if process_name in {"chrome", "msedge", "firefox"}:
        return "browser"
    if process_name in {"systemsettings", "settings"} or "bluetoothdevices" in title or title.endswith("settings"):
        return "settings"
    if any(token in title for token in {"saveas", "open", "confirm", "dialog", "properties"}):
        return "dialog"
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
    if attempts_used > 1:
        return None
    target_label = str(action.get("target_label", "")).strip()
    target_kind = str(action.get("target_kind", "")).strip().lower()
    surface = detect_surface(before)
    normalized_label = normalize_target_label(target_label)
    target_is_selected = _candidate_selected(after, target_label) or _candidate_selected(before, target_label)
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
        if target_kind in {"field", "combobox"}:
            return {
                "type": "press_hotkey",
                "hotkey": "tab" if attempts_used == 0 else "shift+tab",
                "reason": "Move keyboard focus to the dialog field before typing.",
                "target_label": target_label or "dialog-field",
                "target_kind": target_kind or "field",
                "confidence": 0.66,
                "goal_completed_if_verified": False,
            }
    if surface == "explorer" and normalized_label in {"desktop", "downloads", "documents", "pictures", "music", "videos"}:
        if not target_is_selected:
            return None
        return {
            "type": "press_hotkey",
            "hotkey": "enter",
            "reason": f"Use Enter to open the selected Explorer item '{target_label}'.",
            "target_label": target_label,
            "confidence": 0.7,
            "goal_completed_if_verified": True,
        }
    if surface == "explorer" and target_kind in {"row", "tree"} and target_label:
        if not target_is_selected:
            return None
        return {
            "type": "press_hotkey",
            "hotkey": "enter",
            "reason": f"Use Enter to activate the selected Explorer target '{target_label}'.",
            "target_label": target_label,
            "target_kind": target_kind,
            "confidence": 0.68,
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
        if attempts_used >= 1:
            return {
                "type": "press_hotkey",
                "hotkey": "space" if attempts_used == 1 else "enter",
                "reason": f"Use keyboard activation for the visible Bluetooth control near '{target_label or 'Bluetooth'}'.",
                "target_label": target_label or "Bluetooth",
                "confidence": 0.66,
                "goal_completed_if_verified": True,
            }
        return {
            "type": "press_hotkey",
            "hotkey": "tab",
            "reason": f"Use Tab to move focus to the visible Bluetooth control near '{target_label or 'Bluetooth'}'.",
            "target_label": target_label or "Bluetooth",
                "confidence": 0.62,
                "goal_completed_if_verified": False,
            }
    if surface == "settings" and target_kind in {"button", "tab", "menu"}:
        return {
            "type": "press_hotkey",
            "hotkey": "enter" if attempts_used >= 1 else "tab",
            "reason": f"Use keyboard activation for the visible Settings control '{target_label or target_kind}'.",
            "target_label": target_label or target_kind,
            "target_kind": target_kind,
            "confidence": 0.64,
            "goal_completed_if_verified": attempts_used >= 1,
        }
    if surface == "whatsapp":
        if target_kind in {"row", "tree", "tab"} and target_label:
            return {
                "type": "press_hotkey",
                "hotkey": "enter",
                "reason": f"Use Enter to activate the selected WhatsApp target '{target_label}'.",
                "target_label": target_label,
                "target_kind": target_kind,
                "confidence": 0.67,
                "goal_completed_if_verified": True,
            }
        if target_kind in {"field", "combobox"}:
            return {
                "type": "press_hotkey",
                "hotkey": "tab",
                "reason": "Use Tab to move focus into the current chat input field.",
                "target_label": target_label or "message-field",
                "target_kind": target_kind or "field",
                "confidence": 0.63,
                "goal_completed_if_verified": False,
            }
    if surface == "browser":
        if target_kind in {"tab", "menu", "button"}:
            return {
                "type": "press_hotkey",
                "hotkey": "enter" if attempts_used >= 1 else "tab",
                "reason": f"Use keyboard navigation for the visible browser control '{target_label or target_kind}'.",
                "target_label": target_label or target_kind,
                "target_kind": target_kind,
                "confidence": 0.62,
                "goal_completed_if_verified": attempts_used >= 1,
            }
        if target_kind in {"field", "combobox"}:
            return {
                "type": "press_hotkey",
                "hotkey": "ctrl+l",
                "reason": "Use Ctrl+L to focus the browser address or search field.",
                "target_label": target_label or "browser-field",
                "target_kind": target_kind or "field",
                "confidence": 0.62,
                "goal_completed_if_verified": False,
            }
    if surface == "dialog":
        if normalized_label in {"ok", "open", "save", "yes"}:
            return {
                "type": "press_hotkey",
                "hotkey": "enter",
                "reason": f"Use Enter to confirm the visible dialog action '{target_label or 'confirm'}'.",
                "target_label": target_label or "dialog-confirm",
                "target_kind": target_kind or "button",
                "confidence": 0.7,
                "goal_completed_if_verified": True,
            }
        if normalized_label in {"cancel", "close", "no"}:
            return {
                "type": "press_hotkey",
                "hotkey": "esc",
                "reason": f"Use Escape to dismiss the visible dialog action '{target_label or 'cancel'}'.",
                "target_label": target_label or "dialog-cancel",
                "target_kind": target_kind or "button",
                "confidence": 0.7,
                "goal_completed_if_verified": True,
            }
    if target_kind in {"field", "combobox"} and attempts_used == 0:
        return {
            "type": "focus_field",
            "target_label": target_label,
            "target_kind": target_kind or "field",
            "x": action.get("x"),
            "y": action.get("y"),
            "confidence": max(0.6, float(action.get("confidence", 0.0) or 0.0)),
            "reason": f"Retry by explicitly focusing the field '{target_label or 'input field'}' before typing.",
            "goal_completed_if_verified": False,
        }
    if str(action.get("type", "")).strip().lower() == "scroll":
        return {
            "type": "press_hotkey",
            "hotkey": "pagedown" if str(action.get("direction", "down")).strip().lower() == "down" else "pageup",
            "reason": "Use keyboard scrolling when wheel scrolling did not visibly move the surface.",
            "target_label": target_label or surface or "page",
            "target_kind": target_kind or "panel",
            "confidence": 0.61,
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
    action_type = str(action.get("type", "")).strip().lower()

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
    if surface == "explorer_dialog":
        execution_mode = str(tool_result.get("execution_mode", "")).strip().lower()
        if execution_mode == "dialog_open_known_path":
            resolved_path = str(tool_result.get("resolved_path", "")).replace("\\", "/").strip()
            resolved_name = normalize_target_label(Path(resolved_path).name if resolved_path else "")
            if resolved_name and resolved_name in after_text and after_text != before_text:
                return {"ok": True, "kind": "dialog_path", "message": ""}
            if expected_text_after and expected_text_after in after_text and after_text != before_text:
                return {"ok": True, "kind": "dialog_path", "message": ""}
            return {
                "ok": False,
                "kind": "dialog_path",
                "message": f"The Save/Open dialog did not visibly navigate to '{action.get('target_label', '')}'.",
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
    if surface == "whatsapp":
        if action_type == "type_text" and expected_text_after and expected_text_after in after_text and after_text != before_text:
            return {"ok": True, "kind": "whatsapp_message", "message": ""}
        if target_label and target_label in after_text and after_text != before_text:
            return {"ok": True, "kind": "whatsapp_chat", "message": ""}
    if surface == "browser":
        if expected_text_after and expected_text_after in after_text and after_text != before_text:
            return {"ok": True, "kind": "browser_state", "message": ""}
        if target_label and target_label in after_text and after_text != before_text:
            return {"ok": True, "kind": "browser_state", "message": ""}
        if expected_window_title and expected_window_title in after_title and after_title != before_title:
            return {"ok": True, "kind": "browser_state", "message": ""}
    if surface == "dialog":
        if expected_text_after and expected_text_after in after_text and after_text != before_text:
            return {"ok": True, "kind": "dialog_state", "message": ""}
        if expected_window_title and expected_window_title in after_title and after_title != before_title:
            return {"ok": True, "kind": "dialog_state", "message": ""}
    return None


def _candidate_selected(state: dict[str, Any], target_label: str) -> bool:
    normalized_target = normalize_target_label(target_label)
    if not normalized_target:
        return False
    candidates = state.get("target_candidates", [])
    if not isinstance(candidates, list):
        return False
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        normalized = normalize_target_label(str(candidate.get("normalized_label") or candidate.get("label") or ""))
        if normalized != normalized_target:
            continue
        return bool(candidate.get("selected", False))
    return False
