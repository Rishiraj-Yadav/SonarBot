"""Telegram media helpers."""

from __future__ import annotations

from pathlib import Path

from assistant.voice import GeminiVoiceService


async def transcribe_voice(
    audio_path: str,
    config,
    *,
    voice_service: GeminiVoiceService | None = None,
    duration_seconds: int | None = None,
) -> str:
    service = voice_service or GeminiVoiceService(config)
    result = await service.transcribe_file(
        Path(audio_path),
        duration_ms=(duration_seconds * 1000) if duration_seconds is not None else None,
        source="telegram",
    )
    return str(result.get("text", "")).strip()
