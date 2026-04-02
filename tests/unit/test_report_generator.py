from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from assistant.automation.daily_digest import DailyDigestRunner
from assistant.automation.models import ReportFormat, ReportJob, ReportSource
from assistant.automation.report_generator import ReportGenerator
from assistant.gateway.router import GatewayRouter
from assistant.models.base import ModelResponse


class DummyModelProvider:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = []

    async def complete(self, messages, system, tools, stream=False):
        self.calls.append({"messages": messages, "system": system, "tools": tools, "stream": stream})
        yield ModelResponse(text=self.text, done=True)


class DummyToolRegistry:
    def __init__(self) -> None:
        self.calls = []

    def has(self, _tool_name: str) -> bool:
        return False

    async def dispatch(self, tool_name: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append((tool_name, payload))
        return {}


class DummyMemoryManager:
    def __init__(self) -> None:
        self.long_term_reads = 0
        self.daily_reads = 0
        self.long_term_writes = []

    async def read_long_term(self) -> str:
        self.long_term_reads += 1
        return "## Long-Term Memory\nImportant note."

    async def read_today_and_yesterday(self) -> str:
        self.daily_reads += 1
        return "### Yesterday\nDid work."

    async def write_long_term(self, key: str, value: str, image_path: str | None = None):
        self.long_term_writes.append((key, value, image_path))
        return {"key": key, "value": value}


class DummyDelivery:
    def __init__(self) -> None:
        self.sent_text = []
        self.sent_files = []

    async def send_text(self, user_id: str, text: str, *, channel_name: str = "telegram") -> bool:
        self.sent_text.append((user_id, channel_name, text))
        return True

    async def send_file(self, user_id: str, file_path: Path, *, channel_name: str = "telegram", caption: str = "") -> bool:
        self.sent_files.append((user_id, channel_name, str(file_path), caption))
        return True


class DummyAgentLoop:
    async def enqueue(self, request) -> None:  # pragma: no cover - not used in these tests
        raise AssertionError("Agent queue should not be used for report shortcut tests.")

    def status(self) -> dict[str, object]:
        return {"running": False, "pending": 0}


class DummyConnectionManager:
    def get_connection(self, _connection_id: str):
        return None

    def active_count(self) -> int:
        return 0

    def active_channels(self) -> list[str]:
        return []


class DummySessionManager:
    def __init__(self) -> None:
        self.session = SimpleNamespace(session_key="webchat_main")
        self.messages = []

    async def load_or_create(self, session_key: str):
        self.session.session_key = session_key
        return self.session

    async def append_message(self, session, message):
        self.messages.append((session.session_key, message))

    async def session_history(self, _session_key: str, limit: int = 20):
        return self.messages[-limit:]


class DummyHookRunner:
    async def fire_event(self, *_args, **_kwargs):
        return SimpleNamespace(messages=[])


class DummySkillRegistry:
    def list_enabled(self):
        return []

    def match_natural_language(self, _message: str):
        return []

    def find_user_invocable(self, _name: str):
        return None

    def load_skill_prompt(self, _name: str) -> str:
        return ""


class DummyPresenceRegistry:
    def snapshot(self):
        return []


class DummyOAuthFlowManager:
    @property
    def token_manager(self):
        return SimpleNamespace(list_connected=AsyncMock(return_value=[]))


class DummyUserProfiles:
    async def resolve_user_id(self, _channel: str, _identity: str, _metadata: dict[str, object]) -> str:
        return "default"


class DummyAutomationEngine:
    def __init__(self) -> None:
        self.report_jobs = [
            {
                "job_id": "report-1",
                "topic": "AI trends",
                "schedule": "0 18 * * *",
                "paused": False,
            }
        ]
        self.created_jobs: list[ReportJob] = []
        self.immediate_jobs: list[ReportJob] = []
        self.digest_runner = SimpleNamespace(run=AsyncMock(return_value="Digest"), schedule_daily=AsyncMock())

    async def list_report_jobs(self, user_id: str | None = None):
        return list(self.report_jobs)

    async def create_report_job(self, job: ReportJob) -> ReportJob:
        self.created_jobs.append(job)
        payload = job.model_copy(deep=True)
        if not payload.schedule and not payload.run_once_at:
            payload.schedule = "0 18 * * *"
        return payload

    async def run_report_job_now(self, job_id: str):
        raise AssertionError("Not used in this test.")

    async def generate_report_now(self, job: ReportJob, *, notify_channel: bool = False):
        self.immediate_jobs.append(job)
        return SimpleNamespace(
            model_dump=lambda: {
                "job_id": job.job_id,
                "topic": job.topic,
                "save_path": str(Path("workspace") / "reports" / "generated.md"),
                "format": job.output_format.value,
                "byte_size": 123,
                "generated_at": "2026-04-02T00:00:00+00:00",
                "summary_preview": "Generated summary preview.",
            }
        )

    async def delete_report_job(self, job_id: str, user_id: str | None = None) -> bool:
        return True

    async def pause_report_job(self, user_id: str, job_id: str):
        return {"job_id": job_id, "paused": True}

    async def resume_report_job(self, user_id: str, job_id: str):
        return {"job_id": job_id, "paused": False}


def make_router(app_config, automation_engine: DummyAutomationEngine | None = None) -> GatewayRouter:
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=DummyMemoryManager(),
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(),
        automation_engine=automation_engine or DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=SimpleNamespace(),  # unused in tests
    )
    router._nlp.rewrite_canonical = AsyncMock(side_effect=lambda message: message)
    router._nlp.classify = AsyncMock(
        return_value={
            "intent": "unknown",
            "target": "",
            "action": "",
            "time_expr": "",
            "corrected": "",
            "confidence": 0.1,
            "raw_slots": {},
        }
    )
    return router


def build_generator(app_config, text: str = "# Hello\n\nGenerated report.") -> ReportGenerator:
    return ReportGenerator(app_config, DummyModelProvider(text), DummyToolRegistry())


def test_resolve_save_path_defaults_to_workspace_reports(app_config):
    generator = build_generator(app_config)
    job = ReportJob(topic="AI Trends")
    path = generator._resolve_save_path(job)
    assert path.parent == (app_config.agent.workspace_dir / app_config.reports.reports_subdir).resolve()
    assert path.name.endswith("_ai_trends.md")


def test_resolve_save_path_uses_absolute_path_exactly(app_config, tmp_path):
    generator = build_generator(app_config)
    absolute_target = tmp_path / "custom" / "report.md"
    job = ReportJob(topic="AI Trends", save_path=str(absolute_target))
    path = generator._resolve_save_path(job)
    assert path == absolute_target.resolve()


def test_resolve_save_path_resolves_relative_path_under_reports(app_config):
    generator = build_generator(app_config)
    job = ReportJob(topic="AI Trends", save_path="weekly/summary.md")
    path = generator._resolve_save_path(job)
    assert path == (app_config.agent.workspace_dir / app_config.reports.reports_subdir / "weekly" / "summary.md").resolve()


def test_resolve_save_path_appends_suffix_for_existing_file(app_config):
    generator = build_generator(app_config)
    reports_dir = app_config.agent.workspace_dir / app_config.reports.reports_subdir
    reports_dir.mkdir(parents=True, exist_ok=True)
    existing = reports_dir / f"{datetime.now(timezone.utc).strftime('%Y%m%d')}_ai_trends.md"
    existing.write_text("existing", encoding="utf-8")
    job = ReportJob(topic="AI Trends")
    path = generator._resolve_save_path(job)
    assert path.name.endswith("_1.md")


@pytest.mark.asyncio
async def test_generate_folder_source_produces_non_empty_report(app_config, tmp_path):
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    for index in range(3):
        (source_dir / f"note_{index}.txt").write_text(f"content {index}", encoding="utf-8")
    generator = build_generator(app_config, "# Report\n\nFolder summary.")
    job = ReportJob(topic="Folder Summary", source_type=ReportSource.folder, source_path=str(source_dir))
    result = await generator.generate(job)
    saved = Path(result.save_path).read_text(encoding="utf-8")
    assert saved
    assert "Folder summary" in saved


@pytest.mark.asyncio
async def test_generate_memory_source_calls_memory_manager(app_config):
    generator = build_generator(app_config, "# Memory Report\n\nSummary.")
    memory_manager = DummyMemoryManager()
    generator.bind_runtime(memory_manager=memory_manager, delivery=DummyDelivery())
    job = ReportJob(topic="Memory Summary", source_type=ReportSource.memory)
    await generator.generate(job)
    assert memory_manager.long_term_reads == 1
    assert memory_manager.daily_reads == 1


@pytest.mark.asyncio
async def test_format_output_markdown_returns_content(app_config):
    generator = build_generator(app_config)
    assert await generator._format_output("# Hello", "markdown") == "# Hello"


@pytest.mark.asyncio
async def test_format_output_txt_strips_markdown(app_config):
    generator = build_generator(app_config)
    formatted = await generator._format_output("# Hello", "txt")
    assert formatted == "Hello"


def test_report_job_without_schedule_or_run_once_has_job_id():
    job = ReportJob(topic="AI Trends")
    assert job.job_id
    assert job.schedule is None
    assert job.run_once_at is None


@pytest.mark.asyncio
async def test_daily_digest_runner_returns_non_empty_string(app_config):
    memory_dir = app_config.agent.workspace_dir / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / "2026-03-31.md").write_text("Worked on SonarBot.", encoding="utf-8")
    memory_manager = DummyMemoryManager()
    delivery = DummyDelivery()
    runner = DailyDigestRunner(app_config, DummyModelProvider("## Digest\n\nUseful summary."), memory_manager, delivery)
    digest = await runner.run(user_id="default")
    assert digest
    assert memory_manager.long_term_writes


@pytest.mark.asyncio
async def test_report_list_slash_command_returns_formatted_list(app_config):
    router = make_router(app_config)
    response = await router.route_user_message(
        connection_id="conn",
        request_id="req-1",
        session_key="webchat_main",
        message="/report list",
    )
    assert response.ok
    assert "Report jobs:" in response.payload["command_response"]
    assert "report-1" in response.payload["command_response"]


@pytest.mark.asyncio
async def test_natural_language_report_request_creates_report_job(app_config):
    automation_engine = DummyAutomationEngine()
    router = make_router(app_config, automation_engine=automation_engine)
    response = await router.route_user_message(
        connection_id="conn",
        request_id="req-2",
        session_key="webchat_main",
        message="make a report on AI trends at 6pm",
        metadata={"user_id": "default"},
    )
    assert response.ok
    assert automation_engine.created_jobs
    created = automation_engine.created_jobs[0]
    assert created.topic == "AI trends"
    assert created.schedule is not None
    assert "18" in created.schedule


@pytest.mark.asyncio
async def test_immediate_report_request_runs_now_when_no_time_is_given(app_config):
    automation_engine = DummyAutomationEngine()
    router = make_router(app_config, automation_engine=automation_engine)
    response = await router.route_user_message(
        connection_id="conn",
        request_id="req-3",
        session_key="webchat_main",
        message="make a report on AI trends",
        metadata={"user_id": "default"},
    )
    assert response.ok
    assert automation_engine.immediate_jobs
    assert automation_engine.immediate_jobs[0].topic == "AI trends"
    assert "Generated report for AI trends." in response.payload["command_response"]


@pytest.mark.asyncio
async def test_immediate_report_request_preserves_explicit_save_path(app_config):
    automation_engine = DummyAutomationEngine()
    router = make_router(app_config, automation_engine=automation_engine)
    response = await router.route_user_message(
        connection_id="conn",
        request_id="req-4",
        session_key="webchat_main",
        message="create a report on AI trends save to weekly/ai_trends.md",
        metadata={"user_id": "default"},
    )
    assert response.ok
    assert automation_engine.immediate_jobs
    assert automation_engine.immediate_jobs[0].save_path == "weekly/ai_trends.md"
