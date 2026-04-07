from __future__ import annotations

import base64

import pytest

from assistant.voice.service import GeminiVoiceService


@pytest.mark.asyncio
async def test_transcribe_audio_returns_structured_payload(app_config, monkeypatch) -> None:
    service = GeminiVoiceService(app_config)

    async def fake_generate(_models, _payload):
        return {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": '{"text":"open chrome","confidence":0.93,"detected_language":"en"}'
                            }
                        ]
                    }
                }
            ]
        }

    monkeypatch.setattr(service, "_generate_with_candidates", fake_generate)

    result = await service.transcribe_audio(b"fake-webm-audio", "audio/webm", duration_ms=1800)

    assert result["text"] == "open chrome"
    assert result["confidence"] == pytest.approx(0.93)
    assert result["duration_ms"] == 1800
    assert result["detected_language"] == "en"


@pytest.mark.asyncio
async def test_transcribe_audio_accepts_browser_codec_mime(app_config, monkeypatch) -> None:
    service = GeminiVoiceService(app_config)

    async def fake_generate(_models, _payload):
        return {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": '{"text":"take a screenshot","confidence":0.88,"detected_language":"en"}'
                            }
                        ]
                    }
                }
            ]
        }

    monkeypatch.setattr(service, "_generate_with_candidates", fake_generate)

    result = await service.transcribe_audio(b"fake-webm-audio", "audio/webm;codecs=opus", duration_ms=900)

    assert result["text"] == "take a screenshot"
    assert result["input_mime"] == "audio/webm"


@pytest.mark.asyncio
async def test_transcribe_audio_rejects_unsupported_mime(app_config) -> None:
    service = GeminiVoiceService(app_config)

    with pytest.raises(RuntimeError, match="Unsupported audio format"):
        await service.transcribe_audio(b"not-audio", "application/octet-stream")


@pytest.mark.asyncio
async def test_synthesize_speech_returns_wav_bytes(app_config, monkeypatch) -> None:
    service = GeminiVoiceService(app_config)
    pcm_bytes = (b"\x00\x01" * 64)

    async def fake_generate(_models, _payload):
        return {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "inlineData": {
                                    "mimeType": "audio/L16",
                                    "data": base64.b64encode(pcm_bytes).decode("ascii"),
                                }
                            }
                        ]
                    }
                }
            ]
        }

    monkeypatch.setattr(service, "_generate_with_candidates", fake_generate)

    wav_bytes = await service.synthesize_speech("Hello from SonarBot")

    assert wav_bytes[:4] == b"RIFF"
    assert b"WAVE" in wav_bytes[:16]


def test_tts_candidate_models_normalize_legacy_model_name(app_config) -> None:
    app_config.voice.tts_model = "gemini-2.5-flash-tts"
    service = GeminiVoiceService(app_config)

    assert service._tts_candidate_models() == ["gemini-2.5-flash-preview-tts"]
