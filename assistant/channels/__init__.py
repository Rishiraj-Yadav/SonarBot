"""Channel registry helpers."""

from assistant.channels.base import Channel, ChannelMessage
from assistant.channels.telegram.adapter import TelegramChannel

__all__ = ["Channel", "ChannelMessage", "TelegramChannel"]
