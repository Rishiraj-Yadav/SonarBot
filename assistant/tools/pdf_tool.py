"""PDF extraction tools."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any

import httpx

from assistant.tools.registry import ToolDefinition


def build_pdf_tools(config) -> list[ToolDefinition]:
    workspace_dir = config.agent.workspace_dir

    async def pdf_extract(payload: dict[str, Any]) -> dict[str, Any]:
        path = Path(str(payload["path"])).expanduser().resolve()
        text = await asyncio.to_thread(_extract_text, path)
        if len(text.strip()) >= 100:
            return {"path": str(path), "content": text[:12000]}

        try:
            images = await asyncio.to_thread(_render_pdf_to_images, path, workspace_dir / "pdf_pages")
        except Exception as exc:  # pragma: no cover - optional dependency
            return {"path": str(path), "error": f"Scanned PDF fallback unavailable: {exc}"}

        ocr_chunks = []
        for image_path in images[:5]:
            try:
                chunk = await _ocr_image_with_gemini(config, image_path)
            except Exception as exc:  # pragma: no cover - best effort path
                chunk = f"[OCR unavailable for {image_path.name}: {exc}]"
            ocr_chunks.append(chunk)

        return {
            "path": str(path),
            "content": "\n\n".join(ocr_chunks)[:12000],
            "images": [str(item) for item in images],
        }

    return [
        ToolDefinition(
            name="pdf_extract",
            description="Extract text from a PDF. Falls back to OCR for scanned PDFs when possible.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            handler=pdf_extract,
        )
    ]


def _extract_text(path: Path) -> str:
    try:
        import pdfplumber  # type: ignore
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("pdfplumber is not installed.") from exc

    parts: list[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            parts.append(page.extract_text() or "")
    return "\n\n".join(part for part in parts if part.strip())


def _render_pdf_to_images(path: Path, output_dir: Path) -> list[Path]:
    try:
        from pdf2image import convert_from_path  # type: ignore
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("pdf2image is not installed.") from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    pages = convert_from_path(str(path))
    image_paths: list[Path] = []
    for index, page in enumerate(pages):
        target = output_dir / f"{path.stem}-{index + 1}.png"
        page.save(target, "PNG")
        image_paths.append(target)
    return image_paths


async def _ocr_image_with_gemini(config, image_path: Path) -> str:
    if not config.llm.gemini_api_key:
        raise RuntimeError("Missing GEMINI_API_KEY.")

    image_bytes = await asyncio.to_thread(image_path.read_bytes)
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": "Extract the text from this scanned PDF page. Return plain text only."},
                    {
                        "inline_data": {
                            "mime_type": "image/png",
                            "data": base64.b64encode(image_bytes).decode("ascii"),
                        }
                    },
                ],
            }
        ]
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.agent.model}:generateContent"
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(url, params={"key": config.llm.gemini_api_key}, json=payload)
        response.raise_for_status()

    data = response.json()
    chunks: list[str] = []
    for candidate in data.get("candidates", []):
        parts = candidate.get("content", {}).get("parts", [])
        for part in parts:
            if "text" in part:
                chunks.append(part["text"])
    return "\n".join(chunks).strip()
