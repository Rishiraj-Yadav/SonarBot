"""Shared image OCR helpers."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import httpx


def _mime_type_for_image(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    if suffix == ".bmp":
        return "image/bmp"
    return "image/png"


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
