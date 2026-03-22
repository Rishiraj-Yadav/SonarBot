from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from aiogram.enums import ContentType

from assistant.channels.telegram.adapter import TelegramChannel


class FakeSession:
    async def close(self) -> None:
        return None


class FakeSentMessage:
    def __init__(self, text: str) -> None:
        self.text = text
        self.edits: list[str] = []

    async def edit_text(self, text: str) -> "FakeSentMessage":
        self.text = text
        self.edits.append(text)
        return self


class FakeBot:
    def __init__(self) -> None:
        self.session = FakeSession()
        self.downloaded: list[str] = []
        self.sent_messages: list[tuple[int, str]] = []
        self.chat_actions: list[tuple[int, object]] = []

    async def send_message(self, chat_id: int, text: str) -> FakeSentMessage:
        self.sent_messages.append((chat_id, text))
        return FakeSentMessage(text)

    async def send_chat_action(self, chat_id: int, action) -> None:
        self.chat_actions.append((chat_id, action))

    async def get_file(self, file_id: str):
        return SimpleNamespace(file_path=f"{file_id}.bin")

    async def download_file(self, file_path: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(f"downloaded:{file_path}", encoding="utf-8")
        self.downloaded.append(file_path)


class FakeMessage:
    def __init__(self, *, user_id: int, chat_id: int, content_type, text: str | None = None, voice=None) -> None:
        self.from_user = SimpleNamespace(id=user_id)
        self.chat = SimpleNamespace(id=chat_id)
        self.content_type = content_type
        self.text = text
        self.caption = None
        self.voice = voice
        self.photo = []
        self.document = None
        self.answers: list[str] = []

    async def answer(self, text: str) -> FakeSentMessage:
        self.answers.append(text)
        return FakeSentMessage(text)


@pytest.mark.asyncio
async def test_allowed_text_message_is_pushed_to_queue(app_config) -> None:
    app_config.telegram.allowed_user_ids = [123]
    app_config.telegram.bot_token = "test-token"
    fake_bot = FakeBot()
    queued_messages = []

    async def inbound_handler(message):
        queued_messages.append(message)
        return "route-1"

    channel = TelegramChannel(config=app_config, inbound_handler=inbound_handler, bot=fake_bot)
    message = FakeMessage(user_id=123, chat_id=123, content_type=ContentType.TEXT, text="hello from telegram")

    await channel.handle_message(message)

    assert len(queued_messages) == 1
    assert queued_messages[0].text == "hello from telegram"
    assert queued_messages[0].channel == "telegram"


@pytest.mark.asyncio
async def test_blocked_user_is_ignored(app_config) -> None:
    app_config.telegram.allowed_user_ids = [999]
    app_config.telegram.bot_token = "test-token"
    fake_bot = FakeBot()
    queued_messages = []

    async def inbound_handler(message):
        queued_messages.append(message)
        return "route-2"

    channel = TelegramChannel(config=app_config, inbound_handler=inbound_handler, bot=fake_bot)
    message = FakeMessage(user_id=123, chat_id=123, content_type=ContentType.TEXT, text="blocked")

    await channel.handle_message(message)

    assert queued_messages == []


@pytest.mark.asyncio
async def test_voice_message_is_transcribed_before_queueing(app_config, monkeypatch) -> None:
    app_config.telegram.allowed_user_ids = [123]
    app_config.telegram.bot_token = "test-token"
    fake_bot = FakeBot()
    queued_messages = []

    async def inbound_handler(message):
        queued_messages.append(message)
        return "route-3"

    async def fake_transcribe(path: str, _config) -> str:
        assert Path(path).exists()
        return "transcribed voice note"

    monkeypatch.setattr("assistant.channels.telegram.adapter.transcribe_voice", fake_transcribe)

    channel = TelegramChannel(config=app_config, inbound_handler=inbound_handler, bot=fake_bot)
    voice = SimpleNamespace(file_id="voice-file", file_unique_id="voice-unique")
    message = FakeMessage(user_id=123, chat_id=123, content_type=ContentType.VOICE, voice=voice)

    await channel.handle_message(message)

    assert len(queued_messages) == 1
    assert queued_messages[0].text == "transcribed voice note"
    assert queued_messages[0].media_type == "voice"
    assert queued_messages[0].media_path is not None
