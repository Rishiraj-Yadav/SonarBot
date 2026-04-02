from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from assistant.channels.telegram.adapter import TelegramChannel
from assistant.gateway.server import create_app
from tests.helpers import FakeProvider


class FakeVoiceService:
    async def transcribe_audio(self, _audio: bytes, _mime_type: str, *, duration_ms: int | None = None, source: str = "webchat"):
        return {
            "text": "open chrome",
            "confidence": 0.91,
            "duration_ms": duration_ms or 1000,
            "detected_language": "en",
            "input_mime": "audio/webm",
        }

    async def transcribe_file(self, _audio_path: str | Path, *, duration_ms: int | None = None, source: str = "telegram"):
        return {
            "text": "take a screenshot",
            "confidence": 0.89,
            "duration_ms": duration_ms or 2000,
            "detected_language": "en",
            "input_mime": "audio/ogg",
        }

    async def synthesize_speech(self, _text: str) -> bytes:
        return b"RIFFdemoWAVE"


def test_webchat_voice_endpoints_accept_transcription_and_tts(app_config) -> None:
    provider = FakeProvider([])
    app = create_app(config=app_config, model_provider=provider)

    with TestClient(app) as client:
        app.state.services.voice_service = FakeVoiceService()

        transcribe = client.post(
            "/webchat/voice/transcribe",
            content=b"voice-data",
            headers={"content-type": "audio/webm", "x-audio-duration-ms": "1400"},
        )
        synth = client.post("/webchat/voice/synthesize", json={"text": "Hello from SonarBot"})

        assert transcribe.status_code == 200
        assert transcribe.json()["text"] == "open chrome"
        assert transcribe.json()["metadata"]["input_mode"] == "voice"
        assert synth.status_code == 200
        assert synth.headers["content-type"].startswith("audio/wav")
        assert synth.content[:4] == b"RIFF"


def test_webchat_voice_endpoint_accepts_browser_codec_content_type(app_config) -> None:
    provider = FakeProvider([])
    app = create_app(config=app_config, model_provider=provider)

    with TestClient(app) as client:
        app.state.services.voice_service = FakeVoiceService()
        response = client.post(
            "/webchat/voice/transcribe",
            content=b"voice-data",
            headers={"content-type": "audio/webm;codecs=opus", "x-audio-duration-ms": "800"},
        )

        assert response.status_code == 200
        assert response.json()["text"] == "open chrome"


def test_websocket_agent_send_accepts_voice_metadata_without_breaking_text_flow(app_config) -> None:
    provider = FakeProvider([])
    app = create_app(config=app_config, model_provider=provider)

    with TestClient(app) as client:
        with client.websocket_connect("/webchat/ws") as websocket:
            websocket.send_json(
                {
                    "type": "req",
                    "id": "voice-meta-1",
                    "method": "agent.send",
                    "params": {
                        "message": "open chrome",
                        "metadata": {
                            "input_mode": "voice",
                            "voice_confidence": 0.92,
                            "voice_source": "webchat",
                        },
                    },
                }
            )
            ack = websocket.receive_json()
            assert ack["type"] == "res"
            assert ack["ok"] is True


class FakeSession:
    async def close(self) -> None:
        return None


class FakeBot:
    def __init__(self) -> None:
        self.session = FakeSession()

    async def get_file(self, file_id: str):
        return SimpleNamespace(file_path=f"{file_id}.bin")

    async def download_file(self, file_path: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(f"downloaded:{file_path}", encoding="utf-8")


class FakeMessage:
    def __init__(self) -> None:
        self.from_user = SimpleNamespace(id=123)
        self.chat = SimpleNamespace(id=123)
        self.content_type = "voice"
        self.text = None
        self.caption = None
        self.voice = SimpleNamespace(file_id="voice-file", file_unique_id="voice-unique", duration=3)
        self.photo = []
        self.document = None


async def _inbound_passthrough(message):
    return message


import pytest


@pytest.mark.asyncio
async def test_telegram_voice_uses_gemini_service_and_sets_voice_metadata(app_config) -> None:
    app_config.telegram.allowed_user_ids = [123]
    app_config.telegram.bot_token = "test-token"
    captured = []

    async def inbound_handler(message):
        captured.append(message)
        return "route-voice"

    channel = TelegramChannel(
        config=app_config,
        inbound_handler=inbound_handler,
        bot=FakeBot(),
        voice_service=FakeVoiceService(),
    )

    await channel.handle_message(FakeMessage())

    assert captured[0].text == "take a screenshot"
    assert captured[0].metadata["input_mode"] == "voice"
    assert captured[0].metadata["voice_source"] == "telegram"
    assert captured[0].metadata["voice_confidence"] == pytest.approx(0.89)
