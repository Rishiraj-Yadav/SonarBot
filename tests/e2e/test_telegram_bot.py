from __future__ import annotations

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

    async def send_message(self, chat_id: int, text: str) -> FakeSentMessage:
        return FakeSentMessage(text)

    async def send_chat_action(self, chat_id: int, action) -> None:
        return None

    async def get_file(self, file_id: str):
        return SimpleNamespace(file_path=f"{file_id}.bin")

    async def download_file(self, file_path: str, destination) -> None:
        destination.write_text(file_path, encoding="utf-8")


class FakeMessage:
    def __init__(self, *, user_id: int, chat_id: int, text: str) -> None:
        self.from_user = SimpleNamespace(id=user_id)
        self.chat = SimpleNamespace(id=chat_id)
        self.content_type = ContentType.TEXT
        self.text = text
        self.caption = None
        self.voice = None
        self.photo = []
        self.document = None
        self.answers: list[FakeSentMessage] = []

    async def answer(self, text: str) -> FakeSentMessage:
        sent = FakeSentMessage(text)
        self.answers.append(sent)
        return sent


@pytest.mark.asyncio
async def test_telegram_round_trip_streams_back_to_message(app_config) -> None:
    app_config.telegram.allowed_user_ids = [123]
    app_config.telegram.bot_token = "test-token"
    fake_bot = FakeBot()
    queued_messages = []

    async def inbound_handler(message):
        queued_messages.append(message)
        return "route-tele"

    channel = TelegramChannel(config=app_config, inbound_handler=inbound_handler, bot=fake_bot)
    message = FakeMessage(user_id=123, chat_id=456, text="hello from telegram")

    await channel.handle_message(message)
    await channel.handle_event("route-tele", "agent.chunk", {"text": "Hello"})
    await channel.handle_event("route-tele", "agent.chunk", {"text": " there"})
    await channel.handle_event("route-tele", "agent.done", {"session_key": "telegram:123"})

    assert len(queued_messages) == 1
    assert queued_messages[0].text == "hello from telegram"
    assert message.answers
    assert message.answers[0].text == "Hello there"
    assert message.answers[0].edits[-1] == "Hello there"
