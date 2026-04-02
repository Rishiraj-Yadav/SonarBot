"""Heuristic non-text object candidate extraction for visual coworker fallback."""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter

from assistant.desktop_coworker.targeting import clamp_confidence, clamp_normalized_coordinate


def object_detection_backend_health() -> dict[str, Any]:
    try:
        Image
    except Exception as exc:  # pragma: no cover - Pillow is a hard dependency in normal installs
        return {"backend": "object_detection", "available": False, "detail": str(exc)}
    return {"backend": "object_detection", "available": True, "detail": "heuristic screenshot component detection"}


def extract_object_candidates(state: dict[str, Any], *, limit: int = 6, config=None) -> list[dict[str, Any]]:
    capture_path = str(state.get("capture_path", "")).strip()
    if not capture_path or config is None:
        return []
    health = object_detection_backend_health()
    if not health.get("available", False):
        return []
    resolved_path = _resolve_capture_path(config, capture_path)
    if not resolved_path.exists():
        return []
    try:
        with Image.open(resolved_path) as image:
            source = image.convert("L")
            original_width, original_height = source.size
            if original_width < 8 or original_height < 8:
                return []
            max_width = 420
            scale = 1.0
            if original_width > max_width:
                scale = max_width / float(original_width)
                resized = source.resize((max_width, max(1, int(original_height * scale))))
            else:
                resized = source
            edge_image = resized.filter(ImageFilter.FIND_EDGES)
            pixels = edge_image.load()
            width, height = edge_image.size
            threshold = 26
            visited: set[tuple[int, int]] = set()
            detections: list[dict[str, Any]] = []
            for y in range(height):
                for x in range(width):
                    if (x, y) in visited or int(pixels[x, y]) < threshold:
                        continue
                    bounds, pixel_count = _component_bounds(pixels, width, height, start=(x, y), visited=visited, threshold=threshold)
                    if bounds is None:
                        continue
                    left, top, right, bottom = bounds
                    box_width = right - left + 1
                    box_height = bottom - top + 1
                    if box_width < 8 or box_height < 8:
                        continue
                    area = box_width * box_height
                    if pixel_count < 14 or area < 140:
                        continue
                    if area > (width * height) * 0.38:
                        continue
                    scale_x = original_width / float(width)
                    scale_y = original_height / float(height)
                    bbox = {
                        "left": max(0, int(left * scale_x)),
                        "top": max(0, int(top * scale_y)),
                        "right": min(original_width, int((right + 1) * scale_x)),
                        "bottom": min(original_height, int((bottom + 1) * scale_y)),
                    }
                    kind = _classify_component(
                        bbox_width=max(1, bbox["right"] - bbox["left"]),
                        bbox_height=max(1, bbox["bottom"] - bbox["top"]),
                        image_width=original_width,
                        image_height=original_height,
                    )
                    detections.append(
                        {
                            "kind": kind,
                            "bbox": bbox,
                            "confidence": _confidence_for_component(kind=kind, pixel_count=pixel_count, area_ratio=area / max(1, width * height)),
                        }
                    )
                    if len(detections) >= max(1, limit * 6):
                        break
                if len(detections) >= max(1, limit * 6):
                    break
    except Exception:
        return []

    ranked = sorted(
        detections,
        key=lambda item: (-float(item.get("confidence", 0.0)), int(item["bbox"]["top"]), int(item["bbox"]["left"])),
    )
    kind_counts: dict[str, int] = {}
    results: list[dict[str, Any]] = []
    for detection in ranked:
        kind = str(detection.get("kind", "object")).strip().lower() or "object"
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
        bbox = dict(detection.get("bbox", {}))
        center_x = bbox["left"] + (bbox["right"] - bbox["left"]) / 2
        center_y = bbox["top"] + (bbox["bottom"] - bbox["top"]) / 2
        label = f"{kind} {kind_counts[kind]}"
        results.append(
            {
                "label": label,
                "kind": kind,
                "confidence": clamp_confidence(detection.get("confidence"), default=0.36),
                "x": clamp_normalized_coordinate(round((center_x / max(1, original_width)) * 1000)),
                "y": clamp_normalized_coordinate(round((center_y / max(1, original_height)) * 1000)),
                "click_action": "click",
                "backend": "object_detection",
                "bbox": bbox,
                "selected": False,
            }
        )
        if len(results) >= max(1, limit):
            break
    return results


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


def _component_bounds(
    pixels,
    width: int,
    height: int,
    *,
    start: tuple[int, int],
    visited: set[tuple[int, int]],
    threshold: int,
) -> tuple[tuple[int, int, int, int] | None, int]:
    queue: deque[tuple[int, int]] = deque([start])
    visited.add(start)
    left = right = start[0]
    top = bottom = start[1]
    pixel_count = 0
    while queue:
        x, y = queue.popleft()
        if int(pixels[x, y]) < threshold:
            continue
        pixel_count += 1
        left = min(left, x)
        top = min(top, y)
        right = max(right, x)
        bottom = max(bottom, y)
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if nx < 0 or ny < 0 or nx >= width or ny >= height:
                continue
            if (nx, ny) in visited:
                continue
            visited.add((nx, ny))
            if int(pixels[nx, ny]) >= threshold:
                queue.append((nx, ny))
    if pixel_count <= 0:
        return None, 0
    return (left, top, right, bottom), pixel_count


def _classify_component(*, bbox_width: int, bbox_height: int, image_width: int, image_height: int) -> str:
    aspect = bbox_width / max(1.0, float(bbox_height))
    width_ratio = bbox_width / max(1.0, float(image_width))
    height_ratio = bbox_height / max(1.0, float(image_height))
    if width_ratio > 0.22 and height_ratio > 0.12:
        return "dialog"
    if 1.7 <= aspect <= 7.5 and bbox_height <= max(96, int(image_height * 0.12)):
        return "button"
    if 0.65 <= aspect <= 1.35 and max(bbox_width, bbox_height) <= max(112, int(image_height * 0.18)):
        return "icon"
    if aspect >= 4.5 and bbox_height <= max(80, int(image_height * 0.09)):
        return "row"
    if bbox_height >= max(120, int(image_height * 0.18)):
        return "panel"
    return "object"


def _confidence_for_component(*, kind: str, pixel_count: int, area_ratio: float) -> float:
    base = {
        "dialog": 0.58,
        "button": 0.54,
        "icon": 0.46,
        "row": 0.44,
        "panel": 0.42,
        "object": 0.38,
    }.get(kind, 0.38)
    if pixel_count > 80:
        base += 0.03
    if area_ratio > 0.18:
        base -= 0.08
    elif area_ratio > 0.08:
        base -= 0.04
    return clamp_confidence(base, default=0.35)
