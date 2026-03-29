from __future__ import annotations

import pytest

from assistant.agent.session_manager import SessionManager
from assistant.automation import AutomationEngine, AutomationStore, NotificationDispatcher, StandingOrdersManager
from assistant.automation.models import Notification
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


class FakeScheduler:
    def __init__(self) -> None:
        self.registered: list[dict[str, object]] = []
        self.paused: list[str] = []
        self.resumed: list[str] = []
        self.removed: list[str] = []

    async def register_dynamic_job(self, job: dict[str, object]) -> None:
        self.registered.append(dict(job))

    async def pause_dynamic_job(self, cron_id: str) -> None:
        self.paused.append(cron_id)

    async def resume_dynamic_job(self, job: dict[str, object]) -> None:
        self.resumed.append(str(job["cron_id"]))

    async def remove_dynamic_job(self, cron_id: str) -> None:
        self.removed.append(cron_id)


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

    result = await engine.handle_cron_job("cron:0", "Send me a briefing.", mode="ai")

    assert result["status"] == "completed"
    notifications = await store.list_notifications(app_config.users.default_user_id)
    assert len(notifications) == 1
    assert notifications[0]["title"] == "Automation summary ready."
    runs = await store.list_runs(app_config.users.default_user_id)
    assert len(runs) == 1


@pytest.mark.asyncio
async def test_automation_engine_direct_cron_delivery_hits_telegram(app_config) -> None:
    app_config.users.primary_channel = "telegram"
    session_manager = SessionManager(app_config)
    user_profiles = UserProfileStore(app_config)
    await user_profiles.initialize()
    await user_profiles.resolve_user_id("telegram", "123", {"channel": "telegram", "chat_id": "123"})
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

    result = await engine.handle_cron_job("cron:0", "Reminder: go to college")

    assert result["status"] == "completed"
    assert result["mode"] == "direct"
    assert connection_manager.channel_messages == [("telegram", "123", "[Automation] Reminder: go to college")]
    notifications = await store.list_notifications(app_config.users.default_user_id)
    assert notifications[0]["title"] == "Reminder: go to college"


@pytest.mark.asyncio
async def test_notification_dispatcher_prefixes_context_engine_messages_for_telegram(app_config) -> None:
    app_config.users.primary_channel = "telegram"
    user_profiles = UserProfileStore(app_config)
    await user_profiles.initialize()
    await user_profiles.resolve_user_id("telegram", "123", {"channel": "telegram", "chat_id": "123"})
    store = AutomationStore(app_config)
    await store.initialize()
    connection_manager = FakeConnectionManager()
    dispatcher = NotificationDispatcher(app_config, store, user_profiles, connection_manager)

    notification = Notification(
        notification_id="notif-1",
        user_id=app_config.users.default_user_id,
        title="Project follow-up",
        body="You may want to follow up on the proposal.",
        source="context-engine",
        severity="info",
        delivery_mode="primary",
        status="queued",
        target_channels=[],
    )

    await dispatcher.dispatch(notification)

    assert connection_manager.channel_messages == [
        ("telegram", "123", "[Life Context] You may want to follow up on the proposal.")
    ]


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


@pytest.mark.asyncio
async def test_automation_engine_manages_dynamic_cron_jobs(app_config) -> None:
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
    scheduler = FakeScheduler()
    engine.set_scheduler(scheduler)

    created = await engine.create_dynamic_cron_job(app_config.users.default_user_id, "0 8 * * *", "Study reminder")
    listed = await engine.list_dynamic_cron_jobs(app_config.users.default_user_id)
    rules = await engine.list_rules(app_config.users.default_user_id)
    paused = await engine.pause_dynamic_cron_job(app_config.users.default_user_id, str(created["cron_id"]))
    resumed = await engine.resume_dynamic_cron_job(app_config.users.default_user_id, str(created["cron_id"]))
    deleted = await engine.delete_dynamic_cron_job(app_config.users.default_user_id, str(created["cron_id"]))

    assert created["schedule"] == "0 8 * * *"
    assert created["mode"] == "direct"
    assert listed and listed[0]["cron_id"] == created["cron_id"]
    assert any(item.get("dynamic") and item.get("cron_id") == created["cron_id"] for item in rules)
    assert paused["paused"] is True
    assert resumed["paused"] is False
    assert deleted is True
    assert scheduler.registered and scheduler.registered[0]["cron_id"] == created["cron_id"]
    assert scheduler.paused == [str(created["cron_id"])]
    assert scheduler.resumed == [str(created["cron_id"])]
    assert scheduler.removed == [str(created["cron_id"])]
