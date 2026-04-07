from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import aiosqlite

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


class FailingAgentLoop:
    async def enqueue(self, request) -> None:  # noqa: ARG002
        raise AssertionError("Direct reminder notifications should not enqueue the agent loop.")

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
        self.desktop_registered: list[dict[str, object]] = []
        self.desktop_removed: list[str] = []
        self.routine_registered: list[dict[str, object]] = []
        self.routine_removed: list[str] = []

    async def register_dynamic_job(self, job: dict[str, object]) -> None:
        self.registered.append(dict(job))

    async def pause_dynamic_job(self, cron_id: str) -> None:
        self.paused.append(cron_id)

    async def resume_dynamic_job(self, job: dict[str, object]) -> None:
        self.resumed.append(str(job["cron_id"]))

    async def remove_dynamic_job(self, cron_id: str) -> None:
        self.removed.append(cron_id)

    async def register_desktop_rule(self, rule: dict[str, object]) -> None:
        self.desktop_registered.append(dict(rule))

    async def remove_desktop_rule(self, rule_id: str) -> None:
        self.desktop_removed.append(rule_id)

    async def register_desktop_routine(self, routine: dict[str, object]) -> None:
        self.routine_registered.append(dict(routine))

    async def remove_desktop_routine(self, routine_id: str) -> None:
        self.routine_removed.append(routine_id)


class FakeSystemAccessManager:
    def __init__(self) -> None:
        self.moves: list[tuple[str, str]] = []

    async def move_host_file(self, *, source: str, destination: str, session_key: str, session_id: str, user_id: str, connection_id: str = "", channel_name: str = "") -> dict[str, object]:
        self.moves.append((source, destination))
        return {"status": "completed", "source": source, "destination": destination}

    async def copy_host_file(self, *, source: str, destination: str, session_key: str, session_id: str, user_id: str, connection_id: str = "", channel_name: str = "") -> dict[str, object]:
        return {"status": "completed", "source": source, "destination": destination}

    async def delete_host_file(self, *, path: str, session_key: str, session_id: str, user_id: str, connection_id: str = "", channel_name: str = "") -> dict[str, object]:
        return {"status": "completed", "path": path}

    async def write_host_file(self, *, path: str, content: str, session_key: str, session_id: str, user_id: str, connection_id: str = "", channel_name: str = "") -> dict[str, object]:
        return {"status": "completed", "path": path, "content": content}


class FakeToolRegistry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.dir_entries_by_path: dict[str, list[dict[str, object]]] = {}

    async def dispatch(self, tool_name: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append((tool_name, dict(payload)))
        if tool_name == "apps_open":
            return {"status": "completed", "alias": payload.get("target", "app"), "path": "C:/Program Files/app.exe"}
        if tool_name == "desktop_keyboard_hotkey":
            return {"status": "completed", "hotkey": payload.get("hotkey", "")}
        if tool_name == "desktop_screenshot":
            return {"status": "completed", "path": "workspace/desktop/desktop-1.png"}
        if tool_name == "desktop_read_screen":
            return {"status": "completed", "path": "workspace/desktop/desktop-1.png", "content": "Visible text"}
        if tool_name == "move_host_file":
            return {
                "status": "completed",
                "source": payload.get("source", ""),
                "destination": payload.get("destination", ""),
            }
        if tool_name == "copy_host_file":
            return {
                "status": "completed",
                "source": payload.get("source", ""),
                "destination": payload.get("destination", ""),
            }
        if tool_name == "delete_host_file":
            return {"status": "completed", "path": payload.get("path", "")}
        if tool_name == "write_host_file":
            return {"status": "completed", "path": payload.get("path", ""), "content": payload.get("content", "")}
        if tool_name == "list_host_dir":
            path = str(payload.get("path", ""))
            return {"status": "completed", "entries": list(self.dir_entries_by_path.get(path, [])), "path": path}
        if tool_name == "read_host_file":
            return {"status": "completed", "path": payload.get("path", ""), "content": "hello"}
        if tool_name == "exec_shell":
            return {"status": "completed", "stdout": "", "stderr": "", "exit_code": 0}
        if tool_name == "desktop_keyboard_type":
            return {"status": "completed", "characters_typed": len(str(payload.get("text", "")))}
        if tool_name == "desktop_mouse_move":
            return {"status": "completed", "x": payload.get("x", 0), "y": payload.get("y", 0)}
        if tool_name == "desktop_mouse_click":
            return {"status": "completed", "x": payload.get("x", 0), "y": payload.get("y", 0)}
        if tool_name == "desktop_clipboard_read":
            return {"status": "completed", "content": "copied"}
        if tool_name == "desktop_clipboard_write":
            return {"status": "completed", "char_count": len(str(payload.get("text", "")))}
        raise AssertionError(f"Unexpected tool call: {tool_name}")


@pytest.mark.asyncio
async def test_automation_engine_creates_notification_for_cron_run(app_config) -> None:
    app_config.telegram.allowed_user_ids = [8616242206]
    app_config.users.primary_channel = "telegram"
    app_config.users.fallback_channels = ["webchat"]
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
    assert notifications[0]["target_channels"] == ["telegram", "webchat"]
    assert connection_manager.channel_messages == [("telegram", "8616242206", "Automation summary ready.")]
    assert any(event_name == "notification.created" and channel_name == "webchat" for _, event_name, _, channel_name in connection_manager.user_events)
    runs = await store.list_runs(app_config.users.default_user_id)
    assert len(runs) == 1


@pytest.mark.asyncio
async def test_notification_dispatcher_uses_default_telegram_recipient_without_linked_identity(app_config) -> None:
    app_config.telegram.allowed_user_ids = [8616242206]
    app_config.users.primary_channel = "telegram"
    app_config.users.fallback_channels = ["webchat"]
    user_profiles = UserProfileStore(app_config)
    await user_profiles.initialize()
    store = AutomationStore(app_config)
    await store.initialize()
    connection_manager = FakeConnectionManager()
    dispatcher = NotificationDispatcher(app_config, store, user_profiles, connection_manager)

    notification = Notification(
        notification_id="notif-1",
        user_id=app_config.users.default_user_id,
        title="Cron summary",
        body="Cron summary body",
        source="cron:0",
        severity="info",
        delivery_mode="primary",
        status="queued",
        target_channels=[],
    )

    delivered = await dispatcher.dispatch(notification)

    assert delivered.status == "delivered"
    assert connection_manager.channel_messages == [("telegram", "8616242206", "Cron summary body")]
    async with aiosqlite.connect(app_config.data_db_path) as db:
        async with db.execute(
            "SELECT channel, recipient, status FROM notification_deliveries WHERE notification_id = ? ORDER BY id",
            ("notif-1",),
        ) as cursor:
            rows = await cursor.fetchall()
    assert rows == [
        ("telegram", "8616242206", "delivered"),
        ("webchat", "webchat-connection", "delivered"),
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
async def test_automation_engine_sends_plain_reminder_without_agent_roundtrip(app_config) -> None:
    app_config.telegram.allowed_user_ids = [8616242206]
    app_config.users.primary_channel = "telegram"
    app_config.users.fallback_channels = ["webchat"]
    session_manager = SessionManager(app_config)
    user_profiles = UserProfileStore(app_config)
    await user_profiles.initialize()
    store = AutomationStore(app_config)
    await store.initialize()
    connection_manager = FakeConnectionManager()
    dispatcher = NotificationDispatcher(app_config, store, user_profiles, connection_manager)
    engine = AutomationEngine(
        app_config,
        FailingAgentLoop(),
        session_manager,
        StandingOrdersManager(app_config.agent.workspace_dir),
        user_profiles,
        store,
        dispatcher,
    )

    result = await engine.handle_cron_job("dynamic-cron:reminder", "Reminder: go to VNPS")

    assert result["status"] == "completed"
    notifications = await store.list_notifications(app_config.users.default_user_id)
    assert len(notifications) == 1
    assert notifications[0]["body"] == "Reminder: go to VNPS"
    assert connection_manager.channel_messages == [("telegram", "8616242206", "Reminder: go to VNPS")]


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
    assert listed and listed[0]["cron_id"] == created["cron_id"]
    assert any(item.get("dynamic") and item.get("cron_id") == created["cron_id"] for item in rules)
    assert paused["paused"] is True
    assert resumed["paused"] is False
    assert deleted is True
    assert scheduler.registered and scheduler.registered[0]["cron_id"] == created["cron_id"]
    assert scheduler.paused == [str(created["cron_id"])]
    assert scheduler.resumed == [str(created["cron_id"])]
    assert scheduler.removed == [str(created["cron_id"])]


@pytest.mark.asyncio
async def test_automation_engine_creates_and_fires_one_time_reminder(app_config) -> None:
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

    future_time = datetime.now(timezone.utc) + timedelta(hours=2)
    created = await engine.create_one_time_reminder(app_config.users.default_user_id, future_time, "Reminder: file taxes")
    listed = await engine.list_all_one_time_reminders()
    result = await engine.handle_one_time_reminder(
        str(created["reminder_id"]),
        str(created["message"]),
        user_id=app_config.users.default_user_id,
        run_at=str(created["run_at"]),
    )
    fired = await store.get_one_time_reminder(app_config.users.default_user_id, str(created["reminder_id"]))

    assert created["message"] == "Reminder: file taxes"
    assert listed and listed[0]["reminder_id"] == created["reminder_id"]
    assert result["status"] == "completed"
    assert fired is not None and fired["fired"] is True


@pytest.mark.asyncio
async def test_automation_engine_creates_desktop_rules_and_executes_watch_event(app_config) -> None:
    app_config.automation.desktop.enabled = True
    session_manager = SessionManager(app_config)
    user_profiles = UserProfileStore(app_config)
    await user_profiles.initialize()
    store = AutomationStore(app_config)
    await store.initialize()
    connection_manager = FakeConnectionManager()
    dispatcher = NotificationDispatcher(app_config, store, user_profiles, connection_manager)
    system_access_manager = FakeSystemAccessManager()
    engine = AutomationEngine(
        app_config,
        FakeAgentLoop(),
        session_manager,
        StandingOrdersManager(app_config.agent.workspace_dir),
        user_profiles,
        store,
        dispatcher,
        system_access_manager=system_access_manager,
    )
    scheduler = FakeScheduler()
    engine.set_scheduler(scheduler)

    created = await engine.create_desktop_automation_rule(
        app_config.users.default_user_id,
        name="Move PDFs from Download2",
        trigger_type="file_watch",
        watch_path="R:/Download2",
        event_types=["file_created"],
        file_extensions=["pdf"],
        action_type="move",
        destination_path="R:/Documents/PDFs",
    )
    scheduled = await engine.create_desktop_automation_rule(
        app_config.users.default_user_id,
        name="Organize Desktop",
        trigger_type="schedule",
        watch_path="C:/Users/Ritesh/OneDrive/Desktop",
        schedule="0 9 * * 1-5",
        action_type="organize",
    )
    rules = await engine.list_rules(app_config.users.default_user_id)
    result = await engine.handle_desktop_watch_event(
        str(created["rule_id"]),
        app_config.users.default_user_id,
        "file_created",
        "R:/Download2/report.pdf",
    )

    assert any(item["name"] == f"desktop:{created['rule_id']}" for item in rules)
    assert any(item["name"] == f"desktop:{scheduled['rule_id']}" for item in rules)
    assert result["status"] == "completed"
    assert [(source.replace("\\", "/"), destination.replace("\\", "/")) for source, destination in system_access_manager.moves] == [
        ("R:/Download2/report.pdf", "R:/Documents/PDFs/report.pdf")
    ]
    assert scheduler.desktop_registered and scheduler.desktop_registered[0]["rule_id"] == scheduled["rule_id"]


@pytest.mark.asyncio
async def test_automation_engine_creates_manual_desktop_routine_and_runs_it(app_config) -> None:
    app_config.automation.desktop.enabled = True
    session_manager = SessionManager(app_config)
    user_profiles = UserProfileStore(app_config)
    await user_profiles.initialize()
    store = AutomationStore(app_config)
    await store.initialize()
    connection_manager = FakeConnectionManager()
    dispatcher = NotificationDispatcher(app_config, store, user_profiles, connection_manager)
    tool_registry = FakeToolRegistry()
    engine = AutomationEngine(
        app_config,
        FakeAgentLoop(),
        session_manager,
        StandingOrdersManager(app_config.agent.workspace_dir),
        user_profiles,
        store,
        dispatcher,
        tool_registry=tool_registry,
    )

    created = await engine.create_desktop_routine_rule(
        app_config.users.default_user_id,
        name="Study mode",
        trigger_type="manual",
        steps=[
            {"type": "open_app", "target": "chrome"},
            {"type": "notify", "text": "Study mode ready."},
        ],
    )
    rules = await engine.list_rules(app_config.users.default_user_id)
    result = await engine.run_desktop_routine_now(
        app_config.users.default_user_id,
        str(created["routine_id"]),
        notify=False,
    )

    assert any(item["name"] == f"routine:{created['routine_id']}" for item in rules)
    assert result["status"] == "completed"
    assert tool_registry.calls[0][0] == "apps_open"


@pytest.mark.asyncio
async def test_automation_engine_registers_scheduled_desktop_routine_with_scheduler(app_config) -> None:
    app_config.automation.desktop.enabled = True
    session_manager = SessionManager(app_config)
    user_profiles = UserProfileStore(app_config)
    await user_profiles.initialize()
    store = AutomationStore(app_config)
    await store.initialize()
    connection_manager = FakeConnectionManager()
    dispatcher = NotificationDispatcher(app_config, store, user_profiles, connection_manager)
    tool_registry = FakeToolRegistry()
    engine = AutomationEngine(
        app_config,
        FakeAgentLoop(),
        session_manager,
        StandingOrdersManager(app_config.agent.workspace_dir),
        user_profiles,
        store,
        dispatcher,
        tool_registry=tool_registry,
    )
    scheduler = FakeScheduler()
    engine.set_scheduler(scheduler)

    created = await engine.create_desktop_routine_rule(
        app_config.users.default_user_id,
        name="Morning setup",
        trigger_type="schedule",
        schedule="0 9 * * 1-5",
        steps=[{"type": "open_app", "target": "chrome"}],
    )
    paused = await engine.pause_desktop_routine(app_config.users.default_user_id, str(created["routine_id"]))
    resumed = await engine.resume_desktop_routine(app_config.users.default_user_id, str(created["routine_id"]))

    assert created["schedule"] == "0 9 * * 1-5"
    assert scheduler.routine_registered and scheduler.routine_registered[0]["routine_id"] == created["routine_id"]
    assert paused["paused"] is True
    assert resumed["paused"] is False
    assert scheduler.routine_removed == [str(created["routine_id"])]


@pytest.mark.asyncio
async def test_automation_engine_runs_scheduled_directory_move_routine(app_config) -> None:
    app_config.automation.desktop.enabled = True
    session_manager = SessionManager(app_config)
    user_profiles = UserProfileStore(app_config)
    await user_profiles.initialize()
    store = AutomationStore(app_config)
    await store.initialize()
    connection_manager = FakeConnectionManager()
    dispatcher = NotificationDispatcher(app_config, store, user_profiles, connection_manager)
    tool_registry = FakeToolRegistry()
    tool_registry.dir_entries_by_path["R:/Download2"] = [
        {"name": "note.txt", "path": "R:/Download2/note.txt", "is_dir": False, "size": 12},
        {"name": "nested", "path": "R:/Download2/nested", "is_dir": True, "size": 0},
    ]
    engine = AutomationEngine(
        app_config,
        FakeAgentLoop(),
        session_manager,
        StandingOrdersManager(app_config.agent.workspace_dir),
        user_profiles,
        store,
        dispatcher,
        tool_registry=tool_registry,
    )

    created = await engine.create_desktop_routine_rule(
        app_config.users.default_user_id,
        name="Move Download2 to Documents",
        trigger_type="schedule",
        schedule="0 21 * * *",
        steps=[
            {
                "type": "move_host_dir_contents",
                "source_dir": "R:/Download2",
                "destination_dir": "C:/Users/Ritesh/OneDrive/Documents",
            }
        ],
    )
    result = await engine.run_desktop_routine_now(
        app_config.users.default_user_id,
        str(created["routine_id"]),
        notify=False,
    )

    move_calls = [payload for tool_name, payload in tool_registry.calls if tool_name == "move_host_file"]
    assert result["status"] == "completed"
    assert len(move_calls) == 1
    assert move_calls[0]["source"] == "R:/Download2/note.txt"
    assert move_calls[0]["destination"] == "C:/Users/Ritesh/OneDrive/Documents/note.txt"
    assert move_calls[0]["session_key"] == f"automation:{app_config.users.default_user_id}:move-download2-to-documents"
    assert move_calls[0]["user_id"] == app_config.users.default_user_id
    assert move_calls[0]["connection_id"] == ""
    assert move_calls[0]["channel_name"] == ""
    assert str(move_calls[0]["session_id"])
