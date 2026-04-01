from __future__ import annotations

import sys
import types
from pathlib import Path

from assistant.desktop_coworker.candidate_fusion import fuse_target_candidates
from assistant.desktop_coworker.ocr_boxes import extract_ocr_box_candidates
from assistant.desktop_coworker.targeting import build_click_payload, sanitize_candidates
from assistant.desktop_coworker.uia_backend import DesktopCoworkerUIABackend


class _FakeRect:
    def __init__(self, left: int, top: int, right: int, bottom: int) -> None:
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom


class _FakeControl:
    def __init__(
        self,
        *,
        name: str,
        control_type: str,
        rect: tuple[int, int, int, int],
        children: list["_FakeControl"] | None = None,
        offscreen: bool = False,
        enabled: bool = True,
        selected: bool = False,
    ) -> None:
        self.Name = name
        self.ControlTypeName = control_type
        self.BoundingRectangle = _FakeRect(*rect)
        self._children = list(children or [])
        self.IsOffscreen = offscreen
        self.IsEnabled = enabled
        self.IsSelected = selected
        self.RuntimeId = (name, control_type, rect[0], rect[1])

    def GetChildren(self) -> list["_FakeControl"]:
        return list(self._children)


def test_visual_targeting_sanitizes_and_dedupes_candidates() -> None:
    candidates = sanitize_candidates(
        [
            {"label": "hindi_english_parallel", "kind": "file", "confidence": 0.95, "x": 180, "y": 610},
            {"label": "Hindi English Parallel", "kind": "file", "confidence": 0.82, "x": 182, "y": 612},
            {"label": "", "kind": "file", "confidence": 0.2},
        ],
        limit=8,
    )

    assert len(candidates) == 1
    assert candidates[0]["normalized_label"] == "hindienglishparallel"
    assert candidates[0]["confidence"] == 0.95


def test_visual_targeting_builds_active_window_click_payload() -> None:
    payload = build_click_payload(
        x_norm=250,
        y_norm=500,
        count=2,
        state={"capture_target": "window", "capture_width": 1200, "capture_height": 800},
        expected_window_title="Excel",
        expected_process_name="EXCEL",
    )

    assert payload["coordinate_space"] == "active_window"
    assert payload["count"] == 2
    assert payload["x"] == 300
    assert payload["y"] == 400
    assert payload["coworker_low_risk_visual"] is True


def test_visual_targeting_fuses_uia_before_ocr_candidates() -> None:
    fused = fuse_target_candidates(
        [
            {"label": "Desktop", "kind": "folder", "confidence": 0.91, "x": 100, "y": 200, "backend": "uia"},
        ],
        [
            {"label": "Desktop", "kind": "row", "confidence": 0.66, "x": 130, "y": 220, "backend": "ocr_boxes"},
            {"label": "Downloads", "kind": "folder", "confidence": 0.61, "x": 140, "y": 260, "backend": "ocr_boxes"},
        ],
        limit=8,
    )

    assert len(fused) == 2
    assert fused[0]["label"] == "Desktop"
    assert fused[0]["backend"] == "uia"
    assert fused[1]["label"] == "Downloads"


def test_uia_backend_collects_real_candidates_from_accessibility_tree(monkeypatch, app_config) -> None:
    fake_tree = _FakeControl(
        name="Home - File Explorer",
        control_type="WindowControl",
        rect=(100, 100, 900, 700),
        children=[
            _FakeControl(name="Desktop", control_type="TreeItemControl", rect=(120, 180, 320, 230)),
            _FakeControl(name="report.xlsx", control_type="ListItemControl", rect=(320, 360, 760, 420)),
            _FakeControl(name="Add device", control_type="ButtonControl", rect=(760, 210, 920, 260)),
        ],
    )
    fake_uia_module = types.SimpleNamespace(ControlFromHandle=lambda _hwnd: fake_tree)
    monkeypatch.setitem(sys.modules, "uiautomation", fake_uia_module)
    monkeypatch.setattr(
        "assistant.desktop_coworker.uia_backend.load_desktop_libraries",
        lambda: (object(), object(), ""),
    )
    monkeypatch.setattr(
        "assistant.desktop_coworker.uia_backend.get_window_rect",
        lambda _user32, _hwnd: (100, 100, 900, 700),
    )

    backend = DesktopCoworkerUIABackend(app_config)
    candidates = backend.collect_candidates(
        {
            "active_window": {
                "window_id": "12345",
                "title": "Home - File Explorer",
                "process_name": "explorer",
            }
        },
        limit=8,
    )

    labels = {item["label"]: item for item in candidates}
    assert backend.health()["available"] is True
    assert "Desktop" in labels
    assert "report.xlsx" in labels
    assert labels["Desktop"]["backend"] == "uia"
    assert labels["Desktop"]["kind"] == "folder"
    assert labels["report.xlsx"]["kind"] == "file"
    assert labels["report.xlsx"]["click_action"] == "double_click"
    assert labels["Desktop"]["x"] == 150
    assert labels["Desktop"]["y"] == 175


def test_uia_backend_gracefully_reports_missing_library(monkeypatch, app_config) -> None:
    monkeypatch.setitem(sys.modules, "uiautomation", types.SimpleNamespace())
    monkeypatch.setattr(
        "assistant.desktop_coworker.uia_backend.load_desktop_libraries",
        lambda: (object(), object(), ""),
    )

    backend = DesktopCoworkerUIABackend(app_config)

    assert backend.health()["available"] is False
    assert backend.collect_candidates({"active_window": {"window_id": "321"}}, limit=4) == []


def test_ocr_box_candidates_use_real_bounding_boxes(monkeypatch, app_config) -> None:
    capture_dir = app_config.agent.workspace_dir / "desktop"
    capture_dir.mkdir(parents=True, exist_ok=True)
    capture_path = capture_dir / "window-real-bbox.png"
    capture_path.write_bytes(b"fake")

    monkeypatch.setattr(
        "assistant.desktop_coworker.ocr_boxes.ocr_box_backend_health",
        lambda: {"backend": "ocr_boxes", "available": True, "detail": "mock"},
    )
    monkeypatch.setattr(
        "assistant.desktop_coworker.ocr_boxes.extract_text_boxes",
        lambda image_path: [
            {
                "text": "hindi_english_parallel",
                "confidence": 91.0,
                "bbox": {"left": 120, "top": 240, "right": 420, "bottom": 292},
            },
            {
                "text": "Add device",
                "confidence": 78.0,
                "bbox": {"left": 980, "top": 210, "right": 1210, "bottom": 270},
            },
        ],
    )

    candidates = extract_ocr_box_candidates(
        {
            "capture_path": str(capture_path),
            "capture_width": 1280,
            "capture_height": 720,
            "active_window": {"title": "Excel", "process_name": "EXCEL"},
        },
        limit=8,
        config=app_config,
    )

    labels = {item["label"]: item for item in candidates}
    assert "hindi_english_parallel" in labels
    assert labels["hindi_english_parallel"]["kind"] == "file"
    assert labels["hindi_english_parallel"]["backend"] == "ocr_boxes"
    assert labels["hindi_english_parallel"]["bbox"] == {"left": 120, "top": 240, "right": 420, "bottom": 292}
    assert labels["hindi_english_parallel"]["x"] == 211
    assert labels["hindi_english_parallel"]["y"] == 369
    assert labels["Add device"]["x"] == 855


def test_ocr_box_candidates_return_empty_when_bbox_engine_unavailable(monkeypatch, app_config) -> None:
    capture_path = Path("workspace/desktop/window-missing-engine.png")
    monkeypatch.setattr(
        "assistant.desktop_coworker.ocr_boxes.ocr_box_backend_health",
        lambda: {"backend": "ocr_boxes", "available": False, "detail": "missing"},
    )

    candidates = extract_ocr_box_candidates(
        {
            "capture_path": str(capture_path),
            "capture_width": 1200,
            "capture_height": 800,
            "active_window": {"title": "Home - File Explorer", "process_name": "explorer"},
        },
        limit=8,
        config=app_config,
    )

    assert candidates == []
