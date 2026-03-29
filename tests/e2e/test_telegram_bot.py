from __future__ import annotations

from types import SimpleNamespace

import pytest
from aiogram.enums import ContentType

from assistant.channels.telegram.adapter import TelegramChannel, TelegramStreamState


class FakeSession:
    async def close(self) -> None:
        return None


class FakeSentMessage:
    def __init__(self, text: str) -> None:
        self.text = text
        self.edits: list[str] = []
        self.reply_markup = None

    async def edit_text(self, text: str, reply_markup=None) -> "FakeSentMessage":
        self.text = text
        self.reply_markup = reply_markup
        self.edits.append(text)
        return self


class FakeBot:
    def __init__(self) -> None:
        self.session = FakeSession()
        self.sent_messages: list[FakeSentMessage] = []

    async def send_message(self, chat_id: int, text: str, reply_markup=None) -> FakeSentMessage:
        message = FakeSentMessage(text)
        message.reply_markup = reply_markup
        self.sent_messages.append(message)
        return message

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


class FakeCallbackQuery:
    def __init__(self, *, data: str, message: FakeSentMessage) -> None:
        self.data = data
        self.message = message
        self.answered: list[str] = []

    async def answer(self, text: str) -> None:
        self.answered.append(text)


class FakeNotModifiedError(Exception):
    pass


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


@pytest.mark.asyncio
async def test_telegram_host_approval_inline_buttons_round_trip(app_config) -> None:
    app_config.telegram.allowed_user_ids = [123]
    app_config.telegram.bot_token = "test-token"
    fake_bot = FakeBot()
    decisions: list[tuple[str, str]] = []

    async def inbound_handler(message):
        return "route-inline"

    async def host_approval_handler(approval_id: str, decision: str):
        decisions.append((approval_id, decision))
        return {
            "approval_id": approval_id,
            "action_kind": "write_host_file",
            "target_summary": "C:/Users/Test/Desktop/note.txt",
            "category": "ask_once",
            "status": decision,
            "payload": {"path": "C:/Users/Test/Desktop/note.txt"},
        }

    channel = TelegramChannel(
        config=app_config,
        inbound_handler=inbound_handler,
        bot=fake_bot,
        host_approval_handler=host_approval_handler,
    )

    approval = {
        "approval_id": "approval-1",
        "action_kind": "write_host_file",
        "target_summary": "C:/Users/Test/Desktop/note.txt",
        "category": "ask_once",
        "status": "pending",
        "payload": {"path": "C:/Users/Test/Desktop/note.txt"},
    }
    await channel.send_host_approval_request("456", approval)

    sent = fake_bot.sent_messages[-1]
    assert sent.reply_markup is not None

    callback = FakeCallbackQuery(data="hostapprove:approval-1:approved", message=sent)
    await channel.handle_callback_query(callback)

    assert decisions == [("approval-1", "approved")]
    assert "Status: approved" in sent.text
    assert callback.answered


@pytest.mark.asyncio
async def test_telegram_buffers_immediate_command_responses(app_config) -> None:
    app_config.telegram.allowed_user_ids = [123]
    app_config.telegram.bot_token = "test-token"
    fake_bot = FakeBot()
    channel: TelegramChannel | None = None

    async def inbound_handler(message):
        assert channel is not None
        await channel.handle_event("route-command", "agent.chunk", {"text": "Known skills"})
        await channel.handle_event("route-command", "agent.done", {"session_key": "telegram:123"})
        return "route-command"

    channel = TelegramChannel(config=app_config, inbound_handler=inbound_handler, bot=fake_bot)
    message = FakeMessage(user_id=123, chat_id=456, text="/skills")

    await channel.handle_message(message)

    assert message.answers
    assert message.answers[0].text == "Known skills"


@pytest.mark.asyncio
async def test_telegram_ignores_not_modified_edit_errors(app_config, monkeypatch) -> None:
    app_config.telegram.allowed_user_ids = [123]
    app_config.telegram.bot_token = "test-token"

    class NotModifiedMessage(FakeSentMessage):
        async def edit_text(self, text: str, reply_markup=None, disable_web_page_preview=None):
            raise FakeNotModifiedError("message is not modified")

    monkeypatch.setattr("assistant.channels.telegram.adapter.TelegramBadRequest", FakeNotModifiedError)

    channel = TelegramChannel(config=app_config, inbound_handler=lambda _message: None, bot=FakeBot())
    message = NotModifiedMessage("already set")

    result = await channel._edit_text(message, "already set")

    assert result is message


@pytest.mark.asyncio
async def test_telegram_sanitizes_raw_model_error_chunks(app_config) -> None:
    app_config.telegram.allowed_user_ids = [123]
    app_config.telegram.bot_token = "test-token"
    channel = TelegramChannel(config=app_config, inbound_handler=lambda _message: None, bot=FakeBot())
    source_message = FakeMessage(user_id=123, chat_id=456, text="hello")
    state = TelegramStreamState(recipient_id="456", source_message=source_message)

    await channel._apply_event(
        "route-error",
        state,
        "agent.chunk",
        {
            "text": (
                "[Model error] Client error '400 Bad Request' for url "
                "'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent' "
                "For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/400"
            )
        },
    )

    assert state.response_message is not None
    assert "generativelanguage.googleapis.com" not in state.response_message.text
    assert "The model request could not be completed right now. Please try again." == state.response_message.text
