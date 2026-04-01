"""Shared image OCR helpers."""

from __future__ import annotations

import asyncio
import base64
import os
import shutil
from pathlib import Path
from typing import Any

import httpx
from PIL import Image


def _mime_type_for_image(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    if suffix == ".bmp":
        return "image/bmp"
    return "image/png"


def ocr_box_backend_health() -> dict[str, Any]:
    pytesseract_module, detail = _load_pytesseract_with_binary()
    return {
        "backend": "ocr_boxes",
        "available": pytesseract_module is not None,
        "detail": detail,
    }


def extract_text_boxes(image_path: Path, *, min_confidence: float = 35.0) -> list[dict[str, Any]]:
    pytesseract_module, _detail = _load_pytesseract_with_binary()
    if pytesseract_module is None:
        return []
    try:
        image = Image.open(image_path)
    except Exception:
        return []
    try:
        data = pytesseract_module.image_to_data(
            image,
            output_type=pytesseract_module.Output.DICT,
            config="--psm 11",
        )
    except Exception:
        return []

    texts = list(data.get("text", []))
    lefts = list(data.get("left", []))
    tops = list(data.get("top", []))
    widths = list(data.get("width", []))
    heights = list(data.get("height", []))
    confidences = list(data.get("conf", []))
    blocks = list(data.get("block_num", []))
    paragraphs = list(data.get("par_num", []))
    lines = list(data.get("line_num", []))
    word_nums = list(data.get("word_num", []))

    grouped: dict[tuple[int, int, int], list[dict[str, Any]]] = {}
    total = min(
        len(texts),
        len(lefts),
        len(tops),
        len(widths),
        len(heights),
        len(confidences),
        len(blocks),
        len(paragraphs),
        len(lines),
    )
    for index in range(total):
        text = str(texts[index]).strip()
        if not text:
            continue
        try:
            confidence = float(confidences[index])
        except (TypeError, ValueError):
            continue
        if confidence < min_confidence:
            continue
        try:
            left = int(lefts[index])
            top = int(tops[index])
            width = int(widths[index])
            height = int(heights[index])
        except (TypeError, ValueError):
            continue
        if width < 2 or height < 2:
            continue
        key = (
            int(blocks[index] or 0),
            int(paragraphs[index] or 0),
            int(lines[index] or 0),
        )
        grouped.setdefault(key, []).append(
            {
                "text": text,
                "confidence": confidence,
                "left": left,
                "top": top,
                "right": left + width,
                "bottom": top + height,
                "word_num": int(word_nums[index] or 0) if index < len(word_nums) else 0,
            }
        )

    results: list[dict[str, Any]] = []
    for entries in grouped.values():
        if not entries:
            continue
        ordered = sorted(entries, key=lambda item: (int(item.get("word_num", 0)), int(item["left"])))
        label = " ".join(str(item["text"]).strip() for item in ordered).strip()
        if not label:
            continue
        bbox_left = min(int(item["left"]) for item in ordered)
        bbox_top = min(int(item["top"]) for item in ordered)
        bbox_right = max(int(item["right"]) for item in ordered)
        bbox_bottom = max(int(item["bottom"]) for item in ordered)
        confidence = sum(float(item["confidence"]) for item in ordered) / max(1, len(ordered))
        results.append(
            {
                "text": label,
                "confidence": confidence,
                "bbox": {
                    "left": bbox_left,
                    "top": bbox_top,
                    "right": bbox_right,
                    "bottom": bbox_bottom,
                },
            }
        )
    results.sort(
        key=lambda item: (
            int(item["bbox"]["top"]),
            int(item["bbox"]["left"]),
            -float(item.get("confidence", 0.0)),
        )
    )
    return results


async def ocr_image_with_gemini(config, image_path: Path, *, prompt: str) -> str:
    if not config.llm.gemini_api_key:
        raise RuntimeError("Missing GEMINI_API_KEY.")

    image_bytes = await asyncio.to_thread(image_path.read_bytes)
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": _mime_type_for_image(image_path),
                            "data": base64.b64encode(image_bytes).decode("ascii"),
                        }
                    },
                ],
            }
        ]
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        data: dict[str, object] | None = None
        last_error: Exception | None = None
        candidate_models = _candidate_models(str(config.agent.model))
        for index, model_name in enumerate(candidate_models):
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
            response = await client.post(url, params={"key": config.llm.gemini_api_key}, json=payload)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if exc.response.status_code not in {400, 404} or index == len(candidate_models) - 1:
                    raise
                continue
            data = response.json()
            break

    if data is None:
        if last_error is not None:
            raise last_error
        raise RuntimeError("OCR request failed before a Gemini model could be selected.")

    chunks: list[str] = []
    for candidate in data.get("candidates", []):
        parts = candidate.get("content", {}).get("parts", [])
        for part in parts:
            if "text" in part:
                chunks.append(part["text"])
    return "\n".join(chunks).strip()


def _load_pytesseract_with_binary() -> tuple[Any | None, str]:
    try:
        import pytesseract  # type: ignore
    except Exception as exc:
        return None, f"pytesseract unavailable: {exc}"
    binary = _discover_tesseract_binary()
    if binary is None:
        return None, "tesseract executable not found"
    try:
        pytesseract.pytesseract.tesseract_cmd = binary
    except Exception as exc:
        return None, f"unable to configure tesseract: {exc}"
    return pytesseract, f"tesseract: {binary}"


def _discover_tesseract_binary() -> str | None:
    candidates: list[str] = []
    env_binary = os.getenv("TESSERACT_CMD", "").strip()
    if env_binary:
        candidates.append(env_binary)
    discovered = shutil.which("tesseract")
    if discovered:
        candidates.append(discovered)
    candidates.extend(
        [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ]
    )
    seen: set[str] = set()
    for candidate in candidates:
        normalized = str(candidate).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        if Path(normalized).exists():
            return normalized
    return None


def _candidate_models(primary_model: str) -> list[str]:
    candidates = [primary_model, "gemini-2.0-flash"]
    unique: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        normalized = str(item).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique
