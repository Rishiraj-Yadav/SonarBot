from __future__ import annotations

import pytest

from assistant.agent.session_manager import SessionManager
from assistant.automation import AutomationEngine, AutomationStore, NotificationDispatcher, StandingOrdersManager
from assistant.config.schema import AutomationRuleConfig
from assistant.users import UserProfileStore


class FakeAgentLoop:
    async def enqueue(self, request) -> None:
        assert request.result_future is not None
        request.result_future.set_result(
            {
                "assistant_text": "Automation summary ready.",
                "status": "completed",
                "session_key": request.session_key,
            }
        )

    def is_idle(self) -> bool:
        return True


class FakeConnectionManager:
    def __init__(self) -> None:
        self.user_events: list[tuple[str, str, dict[str, object], str | None]] = []
        self.channel_messages: list[tuple[str, str, str]] = []

    async def send_user_event(self, user_id: str, event_name: str, payload: dict[str, object], *, channel_name: str | None = None) -> int:
        self.user_events.append((user_id, event_name, payload, channel_name))
        return 1 if channel_name == "webchat" else 0

    async def send_channel_message(self, channel_name: str, recipient_id: str, text: str) -> bool:
        self.channel_messages.append((channel_name, recipient_id, text))
        return True

    def active_user_connections(self, _user_id: str, channel_name: str | None = None) -> list[str]:
        return ["webchat-connection"] if channel_name == "webchat" else []


@pytest.mark.asyncio
async def test_automation_engine_creates_notification_for_cron_run(app_config) -> None:
    session_manager = SessionManager(app_config)
    user_profiles = UserProfileStore(app_config)
    await user_profiles.initialize()
    store = AutomationStore(app_config)
    await store.initialize()
    connection_manager = FakeConnectionManager()
    dispatcher = NotificationDispatcher(app_config, store, user_profiles, connection_manager)
    engine = AutomationEngine(
        app_config,
        FakeAgentLoop(),
        session_manager,
        StandingOrdersManager(app_config.agent.workspace_dir),
        user_profiles,
        store,
        dispatcher,
    )

    result = await engine.handle_cron_job("cron:0", "Send me a briefing.")

    assert result["status"] == "completed"
    notifications = await store.list_notifications(app_config.users.default_user_id)
    assert len(notifications) == 1
    assert notifications[0]["title"] == "Automation summary ready."
    runs = await store.list_runs(app_config.users.default_user_id)
    assert len(runs) == 1


@pytest.mark.asyncio
async def test_automation_engine_dedupes_heartbeat_runs(app_config) -> None:
    session_manager = SessionManager(app_config)
    user_profiles = UserProfileStore(app_config)
    await user_profiles.initialize()
    store = AutomationStore(app_config)
    await store.initialize()
    connection_manager = FakeConnectionManager()
    dispatcher = NotificationDispatcher(app_config, store, user_profiles, connection_manager)
    engine = AutomationEngine(
        app_config,
        FakeAgentLoop(),
        session_manager,
        StandingOrdersManager(app_config.agent.workspace_dir),
        user_profiles,
        store,
        dispatcher,
    )

    first = await engine.handle_heartbeat()
    second = await engine.handle_heartbeat()

    assert first["status"] == "completed"
    assert second["status"] == "skipped"
    assert second["reason"] in {"dedupe", "cooldown"}


@pytest.mark.asyncio
async def test_automation_engine_does_not_hijack_cron_message_with_generic_rule(app_config) -> None:
    app_config.automation.rules = [
        AutomationRuleConfig(
            name="daily-briefing",
            trigger="cron",
            prompt_or_skill="Good morning briefing",
        )
    ]
    session_manager = SessionManager(app_config)
    user_profiles = UserProfileStore(app_config)
    await user_profiles.initialize()
    store = AutomationStore(app_config)
    await store.initialize()
    connection_manager = FakeConnectionManager()
    dispatcher = NotificationDispatcher(app_config, store, user_profiles, connection_manager)
    engine = AutomationEngine(
        app_config,
        FakeAgentLoop(),
        session_manager,
        StandingOrdersManager(app_config.agent.workspace_dir),
        user_profiles,
        store,
        dispatcher,
    )

    result = await engine.handle_cron_job("cron:0", "Cron test message from SonarBot")

    assert result["status"] == "completed"
    runs = await store.list_runs(app_config.users.default_user_id)
    assert runs[0]["rule_name"] == "cron:0"
