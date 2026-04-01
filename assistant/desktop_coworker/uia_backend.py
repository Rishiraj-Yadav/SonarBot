"""Windows UI Automation candidate extraction for coworker tasks."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from assistant.desktop_coworker.targeting import clamp_confidence, clamp_normalized_coordinate, normalize_target_label
from assistant.tools.windows_desktop import get_foreground_window_handle, get_window_rect, load_desktop_libraries


_COMMON_FOLDER_LABELS = {
    "desktop",
    "documents",
    "downloads",
    "music",
    "pictures",
    "videos",
}

_ROW_CONTROL_TYPES = {
    "dataitemcontrol",
    "listitemcontrol",
    "treeitemcontrol",
}

_INTERACTIVE_CONTROL_TYPES = {
    "buttoncontrol",
    "checkboxcontrol",
    "comboboxcontrol",
    "customcontrol",
    "dataitemcontrol",
    "documentcontrol",
    "editcontrol",
    "groupcontrol",
    "hyperlinkcontrol",
    "listitemcontrol",
    "menuitemcontrol",
    "radiobuttoncontrol",
    "splitbuttoncontrol",
    "tabitemcontrol",
    "textcontrol",
    "thumbcontrol",
    "togglebuttoncontrol",
    "treeitemcontrol",
}


@dataclass(slots=True)
class UIABackendHealth:
    available: bool
    backend: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "available": self.available,
            "detail": self.detail,
        }


class DesktopCoworkerUIABackend:
    """Best-effort UIA backend with safe runtime probing."""

    _MAX_DEPTH = 6
    _MAX_CHILDREN_PER_NODE = 64
    _MAX_SCANNED_NODES = 240

    def __init__(self, config) -> None:
        self.config = config
        self.user32, self.kernel32, self._desktop_error = load_desktop_libraries()
        self._library = None
        self._availability = self._probe_library()

    def _probe_library(self) -> UIABackendHealth:
        if not bool(getattr(self.config.desktop_coworker, "uia_enabled", True)):
            return UIABackendHealth(available=False, backend="uia", detail="disabled in config")
        if self.user32 is None:
            detail = self._desktop_error or "desktop APIs unavailable"
            return UIABackendHealth(available=False, backend="uia", detail=detail)
        try:
            import uiautomation as auto  # type: ignore
        except Exception as exc:
            return UIABackendHealth(available=False, backend="uia", detail=f"uiautomation unavailable: {exc}")
        if not hasattr(auto, "ControlFromHandle"):
            return UIABackendHealth(available=False, backend="uia", detail="uiautomation missing ControlFromHandle")
        self._library = auto
        return UIABackendHealth(available=True, backend="uia", detail="uiautomation")

    def health(self) -> dict[str, Any]:
        return self._availability.to_dict()

    def collect_candidates(self, state: dict[str, Any], *, limit: int = 8) -> list[dict[str, Any]]:
        if not self._availability.available or self._library is None or self.user32 is None:
            return []
        hwnd = self._resolve_window_handle(state)
        if hwnd <= 0:
            return []
        try:
            window_left, window_top, window_right, window_bottom = get_window_rect(self.user32, hwnd)
        except Exception:
            return []
        window_width = max(1, window_right - window_left)
        window_height = max(1, window_bottom - window_top)
        if window_width <= 4 or window_height <= 4:
            return []
        try:
            root = self._library.ControlFromHandle(hwnd)
        except Exception:
            return []
        if root is None:
            return []

        active_window = state.get("active_window", {}) if isinstance(state.get("active_window"), dict) else {}
        process_name = normalize_target_label(str(active_window.get("process_name", "")))
        title = normalize_target_label(str(active_window.get("title", "")))

        deduped: dict[str, dict[str, Any]] = {}
        queue: deque[tuple[Any, int]] = deque([(root, 0)])
        seen_controls: set[str] = set()
        scanned = 0

        while queue and scanned < self._MAX_SCANNED_NODES and len(deduped) < max(1, limit * 4):
            control, depth = queue.popleft()
            control_id = self._control_identifier(control)
            if control_id in seen_controls:
                continue
            seen_controls.add(control_id)
            scanned += 1

            candidate = self._candidate_from_control(
                control=control,
                process_name=process_name,
                title=title,
                window_left=window_left,
                window_top=window_top,
                window_right=window_right,
                window_bottom=window_bottom,
                window_width=window_width,
                window_height=window_height,
            )
            if candidate is not None:
                key = str(candidate.get("normalized_label", ""))
                existing = deduped.get(key)
                if existing is None or float(candidate.get("confidence", 0.0)) > float(existing.get("confidence", 0.0)):
                    deduped[key] = candidate

            if depth >= self._MAX_DEPTH:
                continue
            for child in self._iter_children(control)[: self._MAX_CHILDREN_PER_NODE]:
                queue.append((child, depth + 1))

        ranked = sorted(
            deduped.values(),
            key=lambda item: (
                -float(item.get("confidence", 0.0)),
                0 if bool(item.get("selected", False)) else 1,
                str(item.get("label", "")).lower(),
            ),
        )
        return ranked[: max(1, limit * 3)]

    def _resolve_window_handle(self, state: dict[str, Any]) -> int:
        active_window = state.get("active_window", {}) if isinstance(state.get("active_window"), dict) else {}
        raw_window_id = str(active_window.get("window_id", "")).strip()
        if raw_window_id.isdigit():
            try:
                return int(raw_window_id)
            except ValueError:
                pass
        if self.user32 is None:
            return 0
        try:
            return get_foreground_window_handle(self.user32)
        except Exception:
            return 0

    def _candidate_from_control(
        self,
        *,
        control: Any,
        process_name: str,
        title: str,
        window_left: int,
        window_top: int,
        window_right: int,
        window_bottom: int,
        window_width: int,
        window_height: int,
    ) -> dict[str, Any] | None:
        label = self._best_label(control)
        normalized_label = normalize_target_label(label)
        if len(normalized_label) < 2:
            return None

        control_type = self._normalize_control_type(control)
        if control_type not in _INTERACTIVE_CONTROL_TYPES and control_type not in _ROW_CONTROL_TYPES:
            return None

        bounds = self._extract_bounds(control)
        if bounds is None:
            return None
        left, top, right, bottom = bounds
        clipped_left = max(window_left, min(window_right, left))
        clipped_top = max(window_top, min(window_bottom, top))
        clipped_right = max(window_left, min(window_right, right))
        clipped_bottom = max(window_top, min(window_bottom, bottom))
        if clipped_right - clipped_left < 6 or clipped_bottom - clipped_top < 6:
            return None

        bbox_left = clipped_left - window_left
        bbox_top = clipped_top - window_top
        bbox_right = clipped_right - window_left
        bbox_bottom = clipped_bottom - window_top
        bbox_width = bbox_right - bbox_left
        bbox_height = bbox_bottom - bbox_top
        window_area = max(1, window_width * window_height)
        area_ratio = (bbox_width * bbox_height) / window_area
        if area_ratio > 0.75:
            return None

        kind = self._infer_kind(label=label, normalized_label=normalized_label, control_type=control_type, process_name=process_name, title=title)
        if kind == "unknown" and area_ratio > 0.35:
            return None

        center_x = bbox_left + bbox_width / 2
        center_y = bbox_top + bbox_height / 2
        selected = self._read_bool_property(control, ("IsSelected", "Selected", "CurrentIsSelected", "HasKeyboardFocus"))
        enabled = not self._read_bool_property(control, ("IsOffscreen", "CurrentIsOffscreen")) and self._read_bool_property(
            control,
            ("IsEnabled", "CurrentIsEnabled"),
            default=True,
        )
        confidence = self._candidate_confidence(
            control_type=control_type,
            kind=kind,
            label=label,
            area_ratio=area_ratio,
            selected=selected,
            enabled=enabled,
        )

        click_action = "double_click" if kind == "file" else "click"
        if control_type in {"buttoncontrol", "checkboxcontrol", "hyperlinkcontrol", "menuitemcontrol", "tabitemcontrol", "togglebuttoncontrol"}:
            click_action = "click"

        return {
            "label": label,
            "normalized_label": normalized_label,
            "kind": kind,
            "confidence": confidence,
            "x": clamp_normalized_coordinate((center_x / window_width) * 1000),
            "y": clamp_normalized_coordinate((center_y / window_height) * 1000),
            "click_action": click_action,
            "backend": "uia",
            "bbox": {
                "left": int(bbox_left),
                "top": int(bbox_top),
                "right": int(bbox_right),
                "bottom": int(bbox_bottom),
            },
            "selected": selected,
        }

    def _candidate_confidence(
        self,
        *,
        control_type: str,
        kind: str,
        label: str,
        area_ratio: float,
        selected: bool,
        enabled: bool,
    ) -> float:
        base = 0.72
        if kind in {"file", "folder"}:
            base = 0.96
        elif kind in {"button", "toggle"}:
            base = 0.92
        elif kind in {"tab", "menu"}:
            base = 0.9
        elif kind == "row":
            base = 0.86
        elif kind == "field":
            base = 0.82
        elif control_type == "customcontrol":
            base = 0.74
        if selected:
            base += 0.02
        if not enabled:
            base -= 0.08
        if len(label.strip()) >= 4:
            base += 0.01
        if area_ratio > 0.18:
            base -= 0.1
        elif area_ratio > 0.08:
            base -= 0.04
        return clamp_confidence(base, default=0.6)

    def _infer_kind(self, *, label: str, normalized_label: str, control_type: str, process_name: str, title: str) -> str:
        if control_type in {"checkboxcontrol", "radiobuttoncontrol", "togglebuttoncontrol"}:
            return "toggle"
        if control_type in {"buttoncontrol", "splitbuttoncontrol", "hyperlinkcontrol"}:
            return "button"
        if control_type == "tabitemcontrol":
            return "tab"
        if control_type == "menuitemcontrol":
            return "menu"
        if control_type in {"editcontrol", "documentcontrol"}:
            return "field"
        if process_name == "explorer" or "fileexplorer" in title:
            if normalized_label in _COMMON_FOLDER_LABELS:
                return "folder"
            if Path(label).suffix:
                return "file"
            if control_type in _ROW_CONTROL_TYPES:
                return "row"
        if process_name in {"excel", "word"}:
            if Path(label).suffix.lower() in {".csv", ".doc", ".docx", ".md", ".txt", ".xls", ".xlsx", ".xlsm"}:
                return "file"
            if control_type in _ROW_CONTROL_TYPES:
                return "row"
        if control_type in _ROW_CONTROL_TYPES:
            if Path(label).suffix:
                return "file"
            return "row"
        return "unknown"

    def _best_label(self, control: Any) -> str:
        for attr_name in ("Name", "LegacyIAccessibleName", "AutomationId", "LocalizedControlType"):
            value = self._read_attribute(control, attr_name)
            if isinstance(value, str) and value.strip():
                return value.strip()
        legacy_pattern = self._read_attribute(control, "GetLegacyIAccessiblePattern")
        if callable(legacy_pattern):
            try:
                pattern = legacy_pattern()
                if pattern is not None:
                    value = self._read_attribute(pattern, "Name")
                    if isinstance(value, str) and value.strip():
                        return value.strip()
            except Exception:
                pass
        return ""

    def _normalize_control_type(self, control: Any) -> str:
        for attr_name in ("ControlTypeName", "LocalizedControlType"):
            value = self._read_attribute(control, attr_name)
            if isinstance(value, str) and value.strip():
                return normalize_target_label(value)
        return ""

    def _extract_bounds(self, control: Any) -> tuple[int, int, int, int] | None:
        rect = self._read_attribute(control, "BoundingRectangle")
        if rect is None:
            return None
        if isinstance(rect, (tuple, list)) and len(rect) >= 4:
            try:
                return int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])
            except (TypeError, ValueError):
                return None
        names = (
            ("left", "top", "right", "bottom"),
            ("Left", "Top", "Right", "Bottom"),
        )
        for left_name, top_name, right_name, bottom_name in names:
            left = self._read_attribute(rect, left_name)
            top = self._read_attribute(rect, top_name)
            right = self._read_attribute(rect, right_name)
            bottom = self._read_attribute(rect, bottom_name)
            if all(value is not None for value in (left, top, right, bottom)):
                try:
                    return int(left), int(top), int(right), int(bottom)
                except (TypeError, ValueError):
                    continue
        return None

    def _iter_children(self, control: Any) -> list[Any]:
        get_children = getattr(control, "GetChildren", None)
        if callable(get_children):
            try:
                children = get_children()
            except Exception:
                children = None
            if isinstance(children, (list, tuple)):
                return [child for child in children if child is not None]

        first_child_getter = getattr(control, "GetFirstChildControl", None)
        next_sibling_name = "GetNextSiblingControl"
        if callable(first_child_getter):
            children: list[Any] = []
            try:
                current = first_child_getter()
            except Exception:
                current = None
            sibling_safety = 0
            while current is not None and sibling_safety < self._MAX_CHILDREN_PER_NODE:
                children.append(current)
                sibling_getter = self._read_attribute(current, next_sibling_name)
                if not callable(sibling_getter):
                    break
                try:
                    current = sibling_getter()
                except Exception:
                    break
                sibling_safety += 1
            return children
        return []

    def _control_identifier(self, control: Any) -> str:
        runtime_id = self._read_attribute(control, "RuntimeId")
        if isinstance(runtime_id, (tuple, list)) and runtime_id:
            return "runtime:" + ",".join(str(item) for item in runtime_id)
        native_handle = self._read_attribute(control, "NativeWindowHandle")
        if native_handle:
            return f"hwnd:{native_handle}"
        return f"obj:{id(control)}"

    def _read_bool_property(self, control: Any, names: tuple[str, ...], default: bool = False) -> bool:
        for name in names:
            value = self._read_attribute(control, name)
            if value is None:
                continue
            try:
                return bool(value)
            except Exception:
                continue
        return default

    def _read_attribute(self, obj: Any, name: str) -> Any:
        if obj is None or not hasattr(obj, name):
            return None
        try:
            value = getattr(obj, name)
        except Exception:
            return None
        if callable(value):
            try:
                return value()
            except TypeError:
                return value
            except Exception:
                return None
        return value
