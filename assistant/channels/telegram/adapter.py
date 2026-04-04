"""Telegram adapter using aiogram v3."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from aiogram import Bot, Dispatcher
from aiogram.enums import ChatAction, ContentType
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Document, InlineKeyboardButton, InlineKeyboardMarkup, Message, PhotoSize, Voice

from assistant.channels.base import Channel, ChannelMessage
from assistant.channels.telegram.media import transcribe_voice
from assistant.voice import GeminiVoiceService


@dataclass(slots=True)
class TelegramStreamState:
    recipient_id: str
    source_message: Message | None
    response_message: Message | None = None
    accumulated_text: str = ""


class TelegramChannel(Channel):
    name = "telegram"
    _max_message_chars = 3800

    def __init__(
        self,
        config,
        inbound_handler,
        bot: Bot | None = None,
        dispatcher: Dispatcher | None = None,
        host_approval_handler=None,
        voice_service: GeminiVoiceService | None = None,
    ) -> None:
        super().__init__(config, inbound_handler)
        self.bot = bot or Bot(token=self.config.telegram.bot_token)
        self.dispatcher = dispatcher or Dispatcher()
        self.host_approval_handler = host_approval_handler
        self.voice_service = voice_service
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
            stop_result = self.dispatcher.stop_polling()
            if asyncio.iscoroutine(stop_result):
                await stop_result
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
            self._polling_task = None
        await self.bot.session.close()

    async def send_message(self, recipient_id: str, text: str) -> None:
        message_text = text or "(no response)"
        for chunk in self._chunk_text(message_text):
            await self._send_text(int(recipient_id), chunk)

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
        sent = await self._send_text(int(recipient_id), text, reply_markup=keyboard)
        self._host_approval_messages[approval_id] = sent

    async def finalize_host_approval(self, approval_id: str, approval: dict[str, Any]) -> None:
        message = self._host_approval_messages.get(approval_id)
        if message is None:
            return
        try:
            await self._edit_text(message, self._format_host_approval_text(approval))
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
        try:
            approval = await self.host_approval_handler(approval_id, normalized)
        except KeyError:
            message_text = (
                f"This host approval is no longer active.\n"
                f"Approval id: {approval_id}\n"
                f"Status: expired or already resolved"
            )
            if hasattr(callback_query, "answer"):
                await callback_query.answer(f"Approval {approval_id} is no longer active.")
            if callback_query.message is not None:
                try:
                    await self._edit_text(callback_query.message, message_text)
                except TypeError:
                    await callback_query.message.edit_text(message_text)
            self._host_approval_messages.pop(approval_id, None)
            return
        if hasattr(callback_query, "answer"):
            await callback_query.answer(f"{normalized.title()} {approval_id}")
        if callback_query.message is not None:
            try:
                await self._edit_text(callback_query.message, self._format_host_approval_text(approval))
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
                    state.response_message = await self._reply_text(state.source_message, state.accumulated_text or "...")
                else:
                    state.response_message = await self._send_text(int(state.recipient_id), state.accumulated_text or "...")
            else:
                await self._edit_text(state.response_message, state.accumulated_text or "...")
            return

        if event_name == "agent.done":
            self._stream_states.pop(route_id, None)
            self._pending_route_events.pop(route_id, None)

    async def _normalize_message(self, message: Message) -> ChannelMessage | None:
        text = message.text or message.caption or ""
        media_type: str | None = None
        media_path: str | None = None
        voice_confidence: float | None = None

        if message.content_type == ContentType.TEXT:
            pass
        elif message.content_type == ContentType.VOICE and message.voice is not None:
            media_type = "voice"
            media_path = await self._download_voice(message.voice)
            if self.voice_service is not None:
                transcription = await self.voice_service.transcribe_file(
                    media_path,
                    duration_ms=(getattr(message.voice, "duration", 0) or 0) * 1000,
                    source="telegram",
                )
                text = str(transcription.get("text", "")).strip()
                voice_confidence = float(transcription.get("confidence", 0.0))
            else:
                text = await transcribe_voice(
                    media_path,
                    self.config,
                )
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
            metadata={
                "chat_id": str(message.chat.id),
                "input_mode": "voice" if media_type == "voice" else "text",
                "voice_source": "telegram" if media_type == "voice" else "",
                "voice_confidence": voice_confidence,
            },
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

    async def _send_text(self, chat_id: int, text: str, reply_markup=None):
        try:
            return await self.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
                disable_web_page_preview=True,
            )
        except TypeError:
            return await self.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)

    async def _reply_text(self, message: Message, text: str):
        try:
            return await message.answer(text, disable_web_page_preview=True)
        except TypeError:
            return await message.answer(text)

    async def _edit_text(self, message: Message | Any, text: str):
        try:
            return await message.edit_text(text, reply_markup=None, disable_web_page_preview=True)
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return message
            raise
        except TypeError:
            try:
                return await message.edit_text(text, reply_markup=None)
            except TelegramBadRequest as exc:
                if "message is not modified" in str(exc).lower():
                    return message
                raise

    def _chunk_text(self, text: str) -> list[str]:
        normalized = text.strip() or "(no response)"
        if len(normalized) <= self._max_message_chars:
            return [normalized]
        chunks: list[str] = []
        remaining = normalized
        while remaining:
            if len(remaining) <= self._max_message_chars:
                chunks.append(remaining)
                break
            split_at = remaining.rfind("\n", 0, self._max_message_chars)
            if split_at < max(remaining.rfind(" ", 0, self._max_message_chars), 0):
                split_at = remaining.rfind(" ", 0, self._max_message_chars)
            if split_at <= 0:
                split_at = self._max_message_chars
            chunks.append(remaining[:split_at].rstrip())
            remaining = remaining[split_at:].lstrip()
        return [chunk for chunk in chunks if chunk]
