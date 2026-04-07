"""Gemini-backed speech-to-text and text-to-speech helpers."""

from __future__ import annotations

import base64
import io
import json
import re
import shutil
import subprocess
import tempfile
import wave
from collections import OrderedDict
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from assistant.utils import get_logger


class GeminiVoiceService:
    """Gemini-backed voice helper for STT/TTS."""

    _transcription_fallback_models = ("gemini-2.5-flash", "gemini-2.0-flash")
    _tts_fallback_models = ("gemini-2.5-flash-preview-tts",)
    _legacy_tts_aliases = {
        "gemini-2.5-flash-tts": "gemini-2.5-flash-preview-tts",
    }
    _supported_mime_types = {
        "audio/webm": ".webm",
        "audio/ogg": ".ogg",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/mpeg": ".mp3",
        "audio/mp3": ".mp3",
    }
    _tts_sample_rate_hz = 24000
    _tts_channels = 1
    _tts_sample_width = 2

    def __init__(self, config) -> None:
        self.config = config
        self.voice_config = getattr(config, "voice", None)
        self.api_key = getattr(getattr(config, "llm", None), "gemini_api_key", "")
        self.logger = get_logger("voice_service")
        self._tts_cache: OrderedDict[str, bytes] = OrderedDict()

    async def transcribe_audio(
        self,
        audio_bytes: bytes,
        mime_type: str,
        *,
        duration_ms: int | None = None,
        source: str = "webchat",
    ) -> dict[str, Any]:
        if not getattr(self.voice_config, "enabled", True):
            raise RuntimeError("Voice support is disabled.")
        if not self.api_key:
            raise RuntimeError("Voice transcription requires llm.gemini_api_key.")
        normalized_mime = self._normalize_input_mime(mime_type)
        if normalized_mime is None:
            raise RuntimeError(f"Unsupported audio format '{mime_type}'.")
        max_upload_bytes = int(getattr(self.voice_config, "max_upload_bytes", 10 * 1024 * 1024))
        if len(audio_bytes) > max_upload_bytes:
            raise RuntimeError("Audio upload is too large.")

        if getattr(self.voice_config, "retain_audio", False):
            self._store_debug_audio(audio_bytes, normalized_mime, source=source)

        used_bytes = audio_bytes
        used_mime = normalized_mime
        try:
            transcript = await self._transcribe_with_models(used_bytes, used_mime)
        except RuntimeError:
            converted = self._convert_audio_to_wav(audio_bytes, normalized_mime)
            if converted is None:
                raise
            used_bytes = converted
            used_mime = "audio/wav"
            transcript = await self._transcribe_with_models(used_bytes, used_mime)

        text = str(transcript.get("text", "")).strip()
        confidence = self._normalize_confidence(transcript.get("confidence", 0.0))
        detected_language = str(transcript.get("detected_language", "")).strip() or "unknown"
        resolved_duration = self._resolve_duration_ms(used_bytes, used_mime, duration_ms)
        return {
            "text": text,
            "confidence": confidence,
            "duration_ms": resolved_duration,
            "detected_language": detected_language,
            "input_mime": normalized_mime,
        }

    async def transcribe_file(
        self,
        audio_path: str | Path,
        *,
        mime_type: str | None = None,
        duration_ms: int | None = None,
        source: str = "telegram",
    ) -> dict[str, Any]:
        path = Path(audio_path)
        if not path.exists():
            raise RuntimeError(f"Audio file does not exist: {path}")
        detected_mime = mime_type or self._guess_mime_type_from_path(path)
        return await self.transcribe_audio(
            path.read_bytes(),
            detected_mime,
            duration_ms=duration_ms,
            source=source,
        )

    async def synthesize_speech(self, text: str) -> bytes:
        if not getattr(self.voice_config, "enabled", True):
            raise RuntimeError("Voice support is disabled.")
        if not getattr(self.voice_config, "webchat_tts_enabled", True):
            raise RuntimeError("Voice replies are disabled.")
        if not self.api_key:
            raise RuntimeError("Speech synthesis requires llm.gemini_api_key.")
        normalized = text.strip()
        if not normalized:
            raise RuntimeError("Cannot synthesize empty text.")
        cache_key = self._tts_cache_key(normalized)
        cached = self._tts_cache.get(cache_key)
        if cached is not None:
            self._tts_cache.move_to_end(cache_key)
            return cached

        payload = {
            "contents": [{"parts": [{"text": normalized}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {
                            "voiceName": str(getattr(self.voice_config, "tts_voice_name", "Kore") or "Kore")
                        }
                    }
                },
            },
        }
        response = await self._generate_with_candidates(self._tts_candidate_models(), payload)
        pcm_bytes = self._extract_inline_audio_bytes(response)
        wav_bytes = self._pcm_to_wav_bytes(pcm_bytes)
        self._remember_tts(cache_key, wav_bytes)
        return wav_bytes

    async def _transcribe_with_models(self, audio_bytes: bytes, mime_type: str) -> dict[str, Any]:
        prompt = (
            "Transcribe the speech in this audio and return only raw JSON with keys "
            "text, confidence, detected_language. confidence must be a float between 0.0 and 1.0. "
            "If there is no speech, return an empty text string."
        )
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt},
                        {
                            "inlineData": {
                                "mimeType": mime_type,
                                "data": base64.b64encode(audio_bytes).decode("ascii"),
                            }
                        },
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
            },
        }
        response = await self._generate_with_candidates(self._transcription_candidate_models(), payload)
        raw_text = self._extract_text(response)
        parsed = self._parse_json_text(raw_text)
        if parsed is None:
            return {
                "text": raw_text.strip(),
                "confidence": 0.85 if raw_text.strip() else 0.0,
                "detected_language": "unknown",
            }
        return parsed

    async def _generate_with_candidates(self, candidate_models: list[str], payload: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        for index, model_name in enumerate(candidate_models):
            try:
                return await self._generate_content(model_name, payload)
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if exc.response.status_code not in {400, 404} or index == len(candidate_models) - 1:
                    raise RuntimeError(self._voice_error_message(exc.response)) from exc
                self.logger.warning(
                    "voice_model_fallback",
                    failed_model=model_name,
                    fallback_model=candidate_models[index + 1],
                    status_code=exc.response.status_code,
                )
            except Exception as exc:
                last_error = exc
                raise RuntimeError(str(exc)) from exc
        if last_error is not None:
            raise RuntimeError(str(last_error)) from last_error
        raise RuntimeError("Gemini voice request failed.")

    async def _generate_content(self, model_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                url,
                params={"key": self.api_key},
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
        return response.json()

    def _transcription_candidate_models(self) -> list[str]:
        configured = str(getattr(self.voice_config, "stt_model", "gemini-2.5-flash")).strip()
        return self._unique_models([configured, *self._transcription_fallback_models])

    def _tts_candidate_models(self) -> list[str]:
        configured = str(getattr(self.voice_config, "tts_model", "gemini-2.5-flash-preview-tts")).strip()
        configured = self._legacy_tts_aliases.get(configured, configured)
        return self._unique_models([configured, *self._tts_fallback_models])

    def _unique_models(self, models: list[str]) -> list[str]:
        unique: list[str] = []
        seen: set[str] = set()
        for model in models:
            normalized = model.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            unique.append(normalized)
        return unique

    def _normalize_input_mime(self, mime_type: str) -> str | None:
        normalized = mime_type.strip().lower().split(";", maxsplit=1)[0].strip()
        return normalized if normalized in self._supported_mime_types else None

    def _guess_mime_type_from_path(self, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix == ".ogg":
            return "audio/ogg"
        if suffix == ".wav":
            return "audio/wav"
        if suffix == ".mp3":
            return "audio/mpeg"
        return "audio/webm"

    def _voice_error_message(self, response: httpx.Response) -> str:
        try:
            data = response.json()
            return str(data.get("error", {}).get("message", "")).strip() or response.text[:500]
        except Exception:
            return response.text[:500] or f"Gemini request failed with status {response.status_code}."

    def _parse_json_text(self, value: str) -> dict[str, Any] | None:
        candidate = value.strip()
        if not candidate:
            return None
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate)
        candidate = re.sub(r"\s*```$", "", candidate)
        try:
            parsed = json.loads(candidate)
        except Exception:
            match = re.search(r"\{.*\}", candidate, flags=re.DOTALL)
            if match is None:
                return None
            try:
                parsed = json.loads(match.group(0))
            except Exception:
                return None
        if not isinstance(parsed, dict):
            return None
        return parsed

    def _extract_text(self, response: dict[str, Any]) -> str:
        candidates = response.get("candidates", [])
        for candidate in candidates:
            content = candidate.get("content", {})
            parts = content.get("parts", [])
            texts = [str(part.get("text", "")) for part in parts if isinstance(part, dict) and part.get("text")]
            if texts:
                return "".join(texts).strip()
        return ""

    def _extract_inline_audio_bytes(self, response: dict[str, Any]) -> bytes:
        candidates = response.get("candidates", [])
        for candidate in candidates:
            content = candidate.get("content", {})
            parts = content.get("parts", [])
            for part in parts:
                if not isinstance(part, dict):
                    continue
                inline = part.get("inlineData") or part.get("inline_data")
                if isinstance(inline, dict) and inline.get("data"):
                    return base64.b64decode(str(inline["data"]))
        raise RuntimeError("Gemini speech synthesis returned no audio data.")

    def _normalize_confidence(self, value: Any) -> float:
        try:
            confidence = float(value)
        except Exception:
            confidence = 0.0
        return max(0.0, min(confidence, 1.0))

    def _resolve_duration_ms(self, audio_bytes: bytes, mime_type: str, hinted_duration_ms: int | None) -> int:
        if hinted_duration_ms is not None and hinted_duration_ms >= 0:
            return int(hinted_duration_ms)
        if mime_type not in {"audio/wav", "audio/x-wav"}:
            return 0
        try:
            with wave.open(io.BytesIO(audio_bytes), "rb") as handle:
                frames = handle.getnframes()
                rate = handle.getframerate() or 1
                return int((frames / rate) * 1000)
        except Exception:
            return 0

    def _convert_audio_to_wav(self, audio_bytes: bytes, mime_type: str) -> bytes | None:
        ffmpeg_path = shutil.which("ffmpeg")
        if not ffmpeg_path:
            return None
        suffix = self._supported_mime_types.get(mime_type, ".bin")
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / f"input{suffix}"
            output_path = Path(temp_dir) / "output.wav"
            input_path.write_bytes(audio_bytes)
            result = subprocess.run(
                [ffmpeg_path, "-y", "-i", str(input_path), str(output_path)],
                capture_output=True,
                check=False,
            )
            if result.returncode != 0 or not output_path.exists():
                self.logger.warning(
                    "voice_ffmpeg_conversion_failed",
                    mime_type=mime_type,
                    returncode=result.returncode,
                    stderr=result.stderr.decode("utf-8", errors="ignore")[:500],
                )
                return None
            return output_path.read_bytes()

    def _pcm_to_wav_bytes(self, pcm_bytes: bytes) -> bytes:
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as handle:
            handle.setnchannels(self._tts_channels)
            handle.setsampwidth(self._tts_sample_width)
            handle.setframerate(self._tts_sample_rate_hz)
            handle.writeframes(pcm_bytes)
        return buffer.getvalue()

    def _tts_cache_key(self, text: str) -> str:
        voice_name = str(getattr(self.voice_config, "tts_voice_name", "Kore") or "Kore")
        return f"{voice_name}:{text}"

    def _remember_tts(self, cache_key: str, wav_bytes: bytes) -> None:
        self._tts_cache[cache_key] = wav_bytes
        self._tts_cache.move_to_end(cache_key)
        while len(self._tts_cache) > 32:
            self._tts_cache.popitem(last=False)

    def _store_debug_audio(self, audio_bytes: bytes, mime_type: str, *, source: str) -> None:
        target_dir = self.config.voice_dir
        suffix = self._supported_mime_types.get(mime_type, ".bin")
        target = target_dir / f"{source}-{uuid4().hex}{suffix}"
        try:
            target.write_bytes(audio_bytes)
        except Exception:
            self.logger.warning("voice_debug_audio_write_failed", path=str(target))
