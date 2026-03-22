"""Telegram media helpers."""

from __future__ import annotations

import asyncio
from pathlib import Path


async def transcribe_voice(audio_path: str, config) -> str:
    openai_key = getattr(config.llm, "openai_api_key", "")
    if openai_key:
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=openai_key)
            with Path(audio_path).open("rb") as handle:
                response = await client.audio.transcriptions.create(model="whisper-1", file=handle)
            return getattr(response, "text", "") or ""
        except Exception:
            pass

    try:
        import whisper  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "Voice transcription requires OPENAI_API_KEY or the local 'whisper' package."
        ) from exc

    model = await asyncio.to_thread(whisper.load_model, "base")
    result = await asyncio.to_thread(model.transcribe, audio_path)
    return str(result.get("text", "")).strip()
