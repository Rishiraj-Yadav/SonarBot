from __future__ import annotations

import pytest

from assistant.agent.compaction import CompactionManager
from assistant.agent.session import create_message
from assistant.agent.session_manager import SessionManager
from assistant.models.base import ModelResponse
from tests.helpers import FakeProvider


@pytest.mark.asyncio
async def test_compaction_replaces_old_messages(app_config) -> None:
    app_config.agent.context_window = 16
    provider = FakeProvider([[ModelResponse(text="short summary", done=True)]])
    session_manager = SessionManager(app_config)
    session = await session_manager.create_session("main")
    for index in range(6):
        await session_manager.append_message(session, create_message("user", f"message {index} " * 30))

    compaction = CompactionManager(app_config, session_manager, provider)
    changed = await compaction.maybe_compact(session, "system prompt")

    assert changed is True
    assert session.messages[0]["content"].startswith("[SUMMARY]: short summary")
