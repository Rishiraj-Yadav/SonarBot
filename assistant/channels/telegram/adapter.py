"""Telegram adapter using aiogram v3."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from aiogram import Bot, Dispatcher
from aiogram.enums import ChatAction, ContentType
from aiogram.types import CallbackQuery, Document, InlineKeyboardButton, InlineKeyboardMarkup, Message, PhotoSize, Voice

from assistant.channels.base import Channel, ChannelMessage
from assistant.channels.telegram.media import transcribe_voice


@dataclass(slots=True)
class TelegramStreamState:
    recipient_id: str
    source_message: Message | None
    response_message: Message | None = None
    accumulated_text: str = ""


class TelegramChannel(Channel):
    name = "telegram"

    def __init__(
        self,
        config,
        inbound_handler,
        bot: Bot | None = None,
        dispatcher: Dispatcher | None = None,
        host_approval_handler=None,
    ) -> None:
        super().__init__(config, inbound_handler)
        self.bot = bot or Bot(token=self.config.telegram.bot_token)
        self.dispatcher = dispatcher or Dispatcher()
        self.host_approval_handler = host_approval_handler
        self._polling_task: asyncio.Task[None] | None = None
        self._stream_states: dict[str, TelegramStreamState] = {}
        self._pending_route_events: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        self._host_approval_messages: dict[str, Message | Any] = {}
        self._handlers_registered = False

    async def start(self) -> None:
        if not self.config.telegram.bot_token:
            raise RuntimeError("Telegram is enabled but telegram.bot_token is missing.")
        if not self._handlers_registered:
            self.dispatcher.message.register(self.handle_message)
            self.dispatcher.callback_query.register(self.handle_callback_query)
            self._handlers_registered = True
        if self._polling_task is None:
            self._polling_task = asyncio.create_task(self.dispatcher.start_polling(self.bot))

    async def stop(self) -> None:
        if self._polling_task is not None:
            self.dispatcher.stop_polling()
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
            self._polling_task = None
        await self.bot.session.close()

    async def send_message(self, recipient_id: str, text: str) -> None:
        await self.bot.send_message(chat_id=int(recipient_id), text=text or "(no response)")

    async def send_typing(self, recipient_id: str) -> None:
        await self.bot.send_chat_action(chat_id=int(recipient_id), action=ChatAction.TYPING)

    async def send_host_approval_request(self, recipient_id: str, approval: dict[str, Any]) -> None:
        approval_id = str(approval["approval_id"])
        text = self._format_host_approval_text(approval)
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Approve", callback_data=f"hostapprove:{approval_id}:approved"),
                    InlineKeyboardButton(text="Deny", callback_data=f"hostapprove:{approval_id}:rejected"),
                ]
            ]
        )
        sent = await self.bot.send_message(chat_id=int(recipient_id), text=text, reply_markup=keyboard)
        self._host_approval_messages[approval_id] = sent

    async def finalize_host_approval(self, approval_id: str, approval: dict[str, Any]) -> None:
        message = self._host_approval_messages.get(approval_id)
        if message is None:
            return
        try:
            await message.edit_text(self._format_host_approval_text(approval), reply_markup=None)
        except TypeError:
            await message.edit_text(self._format_host_approval_text(approval))

    async def handle_callback_query(self, callback_query: CallbackQuery) -> None:
        data = getattr(callback_query, "data", "") or ""
        if not data.startswith("hostapprove:") or self.host_approval_handler is None:
            return
        parts = data.split(":", maxsplit=2)
        if len(parts) != 3:
            return
        _, approval_id, decision = parts
        normalized = "approved" if decision == "approved" else "rejected"
        approval = await self.host_approval_handler(approval_id, normalized)
        if hasattr(callback_query, "answer"):
            await callback_query.answer(f"{normalized.title()} {approval_id}")
        if callback_query.message is not None:
            try:
                await callback_query.message.edit_text(self._format_host_approval_text(approval), reply_markup=None)
            except TypeError:
                await callback_query.message.edit_text(self._format_host_approval_text(approval))
        self._host_approval_messages[approval_id] = callback_query.message

    async def handle_message(self, message: Message) -> None:
        user = message.from_user
        if user is None or user.id not in set(self.config.telegram.allowed_user_ids):
            return

        normalized = await self._normalize_message(message)
        if normalized is None:
            return

        route_id = await self.inbound_handler(normalized)
        state = TelegramStreamState(
            recipient_id=normalized.metadata.get("chat_id", normalized.sender_id),
            source_message=message,
        )
        self._stream_states[route_id] = state
        pending_events = self._pending_route_events.pop(route_id, [])
        for event_name, payload in pending_events:
            await self._apply_event(route_id, state, event_name, payload)

    async def handle_event(self, route_id: str, event_name: str, payload: dict[str, Any]) -> None:
        state = self._stream_states.get(route_id)
        if state is None:
            self._pending_route_events.setdefault(route_id, []).append((event_name, dict(payload)))
            return
        await self._apply_event(route_id, state, event_name, payload)

    async def _apply_event(
        self,
        route_id: str,
        state: TelegramStreamState,
        event_name: str,
        payload: dict[str, Any],
    ) -> None:
        if event_name == "agent.chunk":
            state.accumulated_text += payload.get("text", "")
            if state.response_message is None:
                if state.source_message is not None:
                    state.response_message = await state.source_message.answer(state.accumulated_text or "...")
                else:
                    state.response_message = await self.bot.send_message(
                        chat_id=int(state.recipient_id), text=state.accumulated_text or "..."
                    )
            else:
                await state.response_message.edit_text(state.accumulated_text or "...")
            return

        if event_name == "agent.done":
            self._stream_states.pop(route_id, None)
            self._pending_route_events.pop(route_id, None)

    async def _normalize_message(self, message: Message) -> ChannelMessage | None:
        text = message.text or message.caption or ""
        media_type: str | None = None
        media_path: str | None = None

        if message.content_type == ContentType.TEXT:
            pass
        elif message.content_type == ContentType.VOICE and message.voice is not None:
            media_type = "voice"
            media_path = await self._download_voice(message.voice)
            text = await transcribe_voice(media_path, self.config)
        elif message.content_type == ContentType.PHOTO and message.photo:
            media_type = "image"
            media_path = await self._download_photo(message.photo[-1])
        elif message.content_type == ContentType.DOCUMENT and message.document is not None:
            media_type = "document"
            media_path = await self._download_document(message.document)
        else:
            return None

        return ChannelMessage(
            sender_id=str(message.from_user.id if message.from_user else message.chat.id),
            channel=self.name,
            text=text,
            media_type=media_type,
            media_path=media_path,
            raw_message=message,
            metadata={"chat_id": str(message.chat.id)},
        )

    async def _download_voice(self, voice: Voice) -> str:
        suffix = Path(voice.file_unique_id or uuid4().hex).stem
        target = self._inbox_path(f"{suffix}.ogg")
        await self._download_file(voice.file_id, target)
        return str(target)

    async def _download_photo(self, photo: PhotoSize) -> str:
        target = self._inbox_path(f"{photo.file_unique_id or uuid4().hex}.jpg")
        await self._download_file(photo.file_id, target)
        return str(target)

    async def _download_document(self, document: Document) -> str:
        filename = document.file_name or f"{document.file_unique_id or uuid4().hex}.bin"
        target = self._inbox_path(filename)
        await self._download_file(document.file_id, target)
        return str(target)

    async def _download_file(self, file_id: str, target: Path) -> None:
        telegram_file = await self.bot.get_file(file_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        await self.bot.download_file(telegram_file.file_path, destination=target)

    def _inbox_path(self, filename: str) -> Path:
        inbox_dir = self.config.agent.workspace_dir / "inbox"
        inbox_dir.mkdir(parents=True, exist_ok=True)
        return inbox_dir / filename

    def _format_host_approval_text(self, approval: dict[str, Any]) -> str:
        payload = approval.get("payload", {})
        lines = [
            "Host action approval required:",
            f"Action: {approval.get('action_kind', 'unknown')}",
            f"Target: {approval.get('target_summary', '')}",
            f"Category: {approval.get('category', '')}",
            f"Status: {approval.get('status', 'pending')}",
        ]
        if payload.get("command"):
            lines.append(f"Command: {payload['command']}")
        if payload.get("path"):
            lines.append(f"Path: {payload['path']}")
        lines.append("")
        lines.append(
            f"Fallback commands: /host-approve {approval.get('approval_id')} or /host-reject {approval.get('approval_id')}"
        )
        return "\n".join(lines)
