from __future__ import annotations

import json

import pytest

from assistant.agent.session import create_message
from assistant.agent.session_manager import SessionManager
from assistant.automation import AutomationStore
from assistant.context_engine import ContextEngine
from assistant.memory import MemoryManager
from assistant.oauth import OAuthTokenManager
from assistant.users import UserProfileStore
from tests.helpers import FakeProvider
from assistant.models.base import ModelResponse


class FakeNotificationDispatcher:
    def __init__(self) -> None:
        self.notifications = []

    async def dispatch(self, notification):
        self.notifications.append(notification)
        notification.status = "delivered"
        return notification


@pytest.mark.asyncio
async def test_context_engine_creates_snapshot_and_sends_high_confidence_notification(app_config) -> None:
    app_config.context_engine.enabled = True
    memory_manager = MemoryManager(app_config)
    session_manager = SessionManager(app_config)
    oauth_token_manager = OAuthTokenManager(app_config)
    automation_store = AutomationStore(app_config)
    user_profiles = UserProfileStore(app_config)
    dispatcher = FakeNotificationDispatcher()
    provider = FakeProvider(
        [
            [
                ModelResponse(
                    text=json.dumps(
                        {
                            "insights": [
                                {
                                    "title": "Project follow-up",
                                    "body": "You recently talked about a proposal. It may be worth following up on the next step.",
                                    "confidence": 0.93,
                                    "urgency": 0.74,
                                    "category": "follow_up",
                                    "fingerprint": "proposal-follow-up",
                                }
                            ]
                        }
                    ),
                    done=True,
                )
            ]
        ]
    )

    await oauth_token_manager.initialize()
    await automation_store.initialize()
    await user_profiles.initialize()

    session = await session_manager.create_session("webchat_main")
    await session_manager.append_message(session, create_message("user", "I need to finish the proposal this week."))
    await session_manager.append_message(session, create_message("assistant", "I can remind you about the proposal."))

    engine = ContextEngine(
        app_config,
        model_provider=provider,
        memory_manager=memory_manager,
        session_manager=session_manager,
        oauth_token_manager=oauth_token_manager,
        automation_store=automation_store,
        notification_dispatcher=dispatcher,
        user_profiles=user_profiles,
    )

    result = await engine.run_once()

    assert result["status"] == "completed"
    assert result["notifications_sent"] == 1
    snapshot_path = app_config.agent.workspace_dir / app_config.context_engine.snapshot_subdir / "default.json"
    assert snapshot_path.exists()
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert snapshot["recent_sessions"][0]["session_key"] == "webchat_main"
    assert dispatcher.notifications[0].title == "Project follow-up"


@pytest.mark.asyncio
async def test_context_engine_respects_quiet_hours(app_config) -> None:
    app_config.context_engine.enabled = True
    app_config.users.quiet_hours_start = "00:00"
    app_config.users.quiet_hours_end = "23:59"
    memory_manager = MemoryManager(app_config)
    session_manager = SessionManager(app_config)
    oauth_token_manager = OAuthTokenManager(app_config)
    automation_store = AutomationStore(app_config)
    user_profiles = UserProfileStore(app_config)
    dispatcher = FakeNotificationDispatcher()
    provider = FakeProvider(
        [
            [
                ModelResponse(
                    text=json.dumps(
                        {
                            "insights": [
                                {
                                    "title": "Inbox follow-up",
                                    "body": "You may want to review a recent thread.",
                                    "confidence": 0.9,
                                    "urgency": 0.7,
                                    "category": "email",
                                    "fingerprint": "email-follow-up",
                                }
                            ]
                        }
                    ),
                    done=True,
                )
            ]
        ]
    )

    await oauth_token_manager.initialize()
    await automation_store.initialize()
    await user_profiles.initialize()

    engine = ContextEngine(
        app_config,
        model_provider=provider,
        memory_manager=memory_manager,
        session_manager=session_manager,
        oauth_token_manager=oauth_token_manager,
        automation_store=automation_store,
        notification_dispatcher=dispatcher,
        user_profiles=user_profiles,
    )

    result = await engine.run_once()

    assert result["notifications_sent"] == 0
    assert dispatcher.notifications == []
