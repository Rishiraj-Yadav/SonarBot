from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import pytest

from assistant.agent.queue import QueueMode
from assistant.browser_workflows.state import BROWSER_TASK_STATE_KEY, browser_task_state_update
from assistant.gateway.router import GatewayRouter


class DummyAgentLoop:
    def __init__(self) -> None:
        self.enqueued = []
        self.queue = type("Queue", (), {"pending_count": lambda self: 0})()

    async def enqueue(self, request) -> None:
        self.enqueued.append(request)

    def status(self) -> dict[str, object]:
        return {"running": False, "pending": 0, "current_session_key": None}


class DummyConnectionManager:
    def active_count(self) -> int:
        return 0

    def active_channels(self) -> list[str]:
        return []

    def get_connection(self, _connection_id: str):
        return None


class DummySessionManager:
    def __init__(self) -> None:
        self.messages = []
        self.session = type("Session", (), {"session_key": "webchat_main", "session_id": "sess-dummy"})()

    async def load_or_create(self, session_key: str):
        self.session.session_key = session_key
        return self.session

    async def append_message(self, session, message):
        self.messages.append(message)

    async def session_history(self, _session_key: str, limit: int = 20):
        if limit <= 0:
            return []
        return self.messages[-limit:]

    def active_count(self) -> int:
        return 1


class DummyHookRunner:
    async def fire_event(self, *_args, **_kwargs):
        return type("HookEvent", (), {"messages": []})()


class DummySkillRegistry:
    def __init__(self, enabled=None, matches=None) -> None:
        self._enabled = enabled or []
        self._matches = matches or []

    def active_count(self) -> int:
        return len(self._enabled)

    def find_user_invocable(self, _name: str):
        return None

    def list_enabled(self) -> list[object]:
        return list(self._enabled)

    def match_natural_language(self, _message: str):
        return list(self._matches)

    def load_skill_prompt(self, name: str) -> str:
        return f"Skill prompt for {name}"


class DummyPresenceRegistry:
    def snapshot(self) -> list[object]:
        return []


class DummyOAuthFlowManager:
    async def start_oauth_flow(self, provider: str):
        return {"provider": provider, "authorize_url": "https://example.com", "redirect_uri": "http://127.0.0.1"}

    @property
    def token_manager(self):
        class _TokenManager:
            async def list_connected(self):
                return []

        return _TokenManager()


class DummyToolRegistry:
    def __init__(
        self,
        *,
        llm_task_response: dict[str, object] | None = None,
        has_llm_task: bool = True,
        host_tools_enabled: bool = False,
    ) -> None:
        self.calls = []
        self.llm_task_response = llm_task_response or {"content": json.dumps({"skill": "daily-briefing", "confidence": 0.91})}
        self.has_llm_task = has_llm_task
        self.host_tools_enabled = host_tools_enabled
        self.browser_runtime = type(
            "BrowserRuntimeStub",
            (),
            {
                "current_state": lambda self: {
                    "headless": False,
                    "tabs": [{"tab_id": "tab-1", "title": "GitHub", "url": "https://github.com"}],
                    "active_tab": {"tab_id": "tab-1", "title": "GitHub", "url": "https://github.com"},
                    "active_profile": {"site_name": "github.com", "profile_name": "work", "status": "active"},
                }
            },
        )()

    def has(self, tool_name: str) -> bool:
        if tool_name in {
            "browser_sessions_list",
            "browser_tabs_list",
            "browser_downloads_list",
            "browser_logs",
            "browser_tab_open",
            "browser_tab_switch",
            "browser_tab_close",
            "browser_screenshot",
            "browser_login",
        }:
            return True
        if self.host_tools_enabled and tool_name in {
            "list_host_dir",
            "search_host_files",
            "exec_shell",
            "write_host_file",
            "set_windows_brightness",
        }:
            return True
        return self.has_llm_task and tool_name == "llm_task"

    async def dispatch(self, tool_name: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append((tool_name, payload))
        if tool_name == "gmail_latest_email":
            return {
                "found": True,
                "from": "sender@example.com",
                "subject": "Test subject",
                "date": "Mon, 01 Jan 2026 10:00:00 +0000",
                "snippet": "Snippet text",
                "body": "Body preview",
            }
        if tool_name == "github_list_repos":
            return {"repositories": [{"full_name": "octo/repo-1"}, {"full_name": "octo/repo-2"}]}
        if tool_name == "github_list_pull_requests":
            return {
                "owner": payload["owner"],
                "repo": payload["repo"],
                "pull_requests": [
                    {
                        "number": 7,
                        "title": "Improve routing",
                        "user": "octocat",
                        "html_url": "https://github.com/octo/repo/pull/7",
                    }
                ],
            }
        if tool_name == "llm_task":
            return self.llm_task_response
        if tool_name == "list_host_dir":
            requested_path = str(payload["path"])
            if requested_path in {"R:/", "R:\\"}:
                return {
                    "path": requested_path,
                    "entries": [
                        {"name": "college", "path": "R:/college", "is_dir": True, "size": 0},
                        {"name": "notes.txt", "path": "R:/notes.txt", "is_dir": False, "size": 256},
                    ],
                }
            if requested_path.endswith("/5sem") or requested_path.endswith("\\5sem"):
                return {
                    "path": requested_path,
                    "entries": [
                        {"name": "dbms", "path": f"{requested_path}/dbms", "is_dir": True, "size": 0},
                        {"name": "os-notes.pdf", "path": f"{requested_path}/os-notes.pdf", "is_dir": False, "size": 1024},
                    ],
                }
            return {
                "path": payload["path"],
                "entries": [
                    {"name": "Resume.pdf", "path": "C:/Users/Ritesh/Downloads/Resume.pdf", "is_dir": False, "size": 1024},
                    {"name": "notes", "path": "C:/Users/Ritesh/Downloads/notes", "is_dir": True, "size": 0},
                ],
            }
        if tool_name == "search_host_files":
            query = str(payload.get("name_query", ""))
            query_compact = re.sub(r"[^a-z0-9]+", "", query.lower())
            root = str(payload.get("root", "@allowed"))
            if query_compact == "5sem" and root == "R:/":
                return {
                    "root": "R:/",
                    "searched_roots": ["R:/"],
                    "matches": [
                        {"name": "5sem", "path": "R:/college/5sem", "is_dir": True},
                    ],
                    "directories_only": bool(payload.get("directories_only")),
                }
            if query_compact == "spcc":
                return {
                    "root": root,
                    "searched_roots": [root] if root != "@allowed" else ["R:/"],
                    "matches": [
                        {"name": "SPCC", "path": "R:/6_semester/SPCC", "is_dir": True},
                    ],
                    "directories_only": bool(payload.get("directories_only")),
                }
            return {
                "root": root,
                "searched_roots": ["C:/Users/Ritesh/Documents", "R:/"] if root == "@allowed" else [root],
                "matches": [
                    {"name": "5sem", "path": "C:/Users/Ritesh/Documents/college/5sem", "is_dir": True},
                    {"name": "5sem-notes.txt", "path": "C:/Users/Ritesh/Documents/5sem-notes.txt", "is_dir": False},
                ],
                "directories_only": bool(payload.get("directories_only")),
            }
        if tool_name == "exec_shell":
            return {
                "stdout": "",
                "stderr": "",
                "exit_code": 0,
                "status": "completed",
                "approval_mode": "session_cache",
                "approval_category": "ask_once",
                "audit_id": "audit-1",
                "host": True,
            }
        if tool_name == "set_windows_brightness":
            return {
                "stdout": "",
                "stderr": "",
                "exit_code": 0,
                "brightness_percent": int(payload["percent"]),
                "status": "completed",
                "approval_mode": "auto",
                "approval_category": "auto_allow",
                "audit_id": "audit-bright",
                "host": True,
            }
        if tool_name == "write_host_file":
            return {
                "path": payload["path"],
                "bytes_written": len(str(payload["content"]).encode("utf-8")),
                "status": "completed",
                "approval_mode": "session_cache",
                "approval_category": "ask_once",
                "audit_id": "audit-2",
                "host": True,
            }
        if tool_name == "browser_sessions_list":
            return {
                "sessions": [
                    {"site_name": "github.com", "profile_name": "work", "status": "active"},
                    {"site_name": "leetcode.com", "profile_name": "personal", "status": "stale"},
                ]
            }
        if tool_name == "browser_tabs_list":
            return {
                "current_tab_id": "tab-1",
                "tabs": [
                    {"tab_id": "tab-1", "title": "GitHub", "url": "https://github.com"},
                    {"tab_id": "tab-2", "title": "LeetCode", "url": "https://leetcode.com"},
                ],
            }
        if tool_name == "browser_downloads_list":
            return {"downloads": [{"filename": "report.csv", "path": "workspace/inbox/browser_downloads/work/report.csv"}]}
        if tool_name == "browser_logs":
            return {"logs": [{"kind": "console", "message": "Loaded dashboard"}, {"kind": "request_failed", "message": "GET /api/test -> 500"}]}
        if tool_name == "browser_tab_open":
            return {"tab_id": "tab-3", "title": "Docs", "url": str(payload.get("url", ""))}
        if tool_name == "browser_tab_switch":
            return {"tab_id": payload["tab_id"], "title": "LeetCode", "url": "https://leetcode.com"}
        if tool_name == "browser_tab_close":
            return {"current_tab_id": "tab-1"}
        if tool_name == "browser_screenshot":
            return {"path": "workspace/browser/screenshot-tab-1.png", "tab_id": "tab-1", "url": "https://github.com"}
        if tool_name == "browser_login":
            return {"site_name": payload["site_name"], "profile_name": payload.get("profile_name", "default"), "status": "active", "url": f"https://{payload['site_name']}"}
        raise AssertionError(f"Unexpected tool: {tool_name}")


class DummyBrowserMonitorService:
    def __init__(self) -> None:
        self.created: list[tuple[str, str, str]] = []
        self.deleted: list[tuple[str, str]] = []

    async def create_watch(self, user_id: str, url: str, condition: str) -> dict[str, object]:
        self.created.append((user_id, url, condition))
        return {
            "watch_id": "watch-1",
            "user_id": user_id,
            "url": url,
            "condition": condition,
            "baseline_preview": "Initial baseline",
        }

    async def list_watches(self, user_id: str) -> list[dict[str, object]]:
        return [
            {
                "watch_id": "watch-1",
                "user_id": user_id,
                "url": "https://example.com",
                "condition": "price changes",
            }
        ]

    async def delete_watch(self, user_id: str, watch_id: str) -> bool:
        self.deleted.append((user_id, watch_id))
        return watch_id == "watch-1"


class ChallengeBrowserRuntimeStub:
    def __init__(self) -> None:
        self.submitted_otp: str | None = None
        self.submitted_captcha: str | None = None

    def current_state(self) -> dict[str, object]:
        return {}

    async def submit_pending_otp(self, otp: str, *, user_id: str | None = None) -> dict[str, object]:
        self.submitted_otp = otp
        return {"otp": otp, "user_id": user_id}

    async def submit_pending_captcha(self, answer: str, *, user_id: str | None = None) -> dict[str, object]:
        self.submitted_captcha = answer
        return {"answer": answer, "user_id": user_id}


class ChallengeEngineStub:
    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.calls: list[tuple[str, str]] = []
        self.nlp = type("NLPStub", (), {"standalone_execution_override": staticmethod(lambda _message: None)})()

    async def maybe_run(self, message: str, *, user_id: str, **_kwargs):
        self.calls.append((message, user_id))
        return type(
            "WorkflowResult",
            (),
            {
                "recipe_name": "browser_continue_last_task",
                "status": "completed",
                "payload": {},
                "progress_lines": [],
                "response_text": self.response_text,
                "state_update": browser_task_state_update(active_task={}),
                "clear_state": False,
            },
        )()


class DummyAutomationEngine:
    async def list_notifications(self, _user_id: str):
        return []

    async def list_runs(self, _user_id: str):
        return []

    async def list_rules(self, _user_id: str):
        return []

    async def pause_rule(self, _user_id: str, _rule_name: str) -> None:
        return None

    async def resume_rule(self, _user_id: str, _rule_name: str) -> None:
        return None

    async def replay_run(self, _run_id: str):
        return {"status": "ok"}

    async def list_approvals(self, _user_id: str):
        return []

    async def decide_approval(self, _approval_id: str, _decision: str) -> None:
        return None

    def __init__(self) -> None:
        self.dynamic_jobs: list[dict[str, object]] = []

    async def create_dynamic_cron_job(self, user_id: str, schedule: str, message: str, mode: str = "direct") -> dict[str, object]:
        job = {
            "cron_id": "cron-user-1",
            "user_id": user_id,
            "schedule": schedule,
            "message": message,
            "mode": mode,
            "paused": False,
        }
        self.dynamic_jobs = [job]
        return job

    async def list_dynamic_cron_jobs(self, _user_id: str) -> list[dict[str, object]]:
        return list(self.dynamic_jobs)

    async def pause_dynamic_cron_job(self, _user_id: str, cron_id: str) -> dict[str, object]:
        for job in self.dynamic_jobs:
            if job["cron_id"] == cron_id:
                job["paused"] = True
                return job
        raise KeyError(f"Unknown cron job '{cron_id}'.")

    async def resume_dynamic_cron_job(self, _user_id: str, cron_id: str) -> dict[str, object]:
        for job in self.dynamic_jobs:
            if job["cron_id"] == cron_id:
                job["paused"] = False
                return job
        raise KeyError(f"Unknown cron job '{cron_id}'.")

    async def delete_dynamic_cron_job(self, _user_id: str, cron_id: str) -> bool:
        for index, job in enumerate(self.dynamic_jobs):
            if job["cron_id"] == cron_id:
                self.dynamic_jobs.pop(index)
                return True
        raise KeyError(f"Unknown cron job '{cron_id}'.")


class DummyUserProfiles:
    async def resolve_user_id(self, _identity_type: str, _identity_value: str, _metadata=None) -> str:
        return "default"


class DummySystemAccessManager:
    def __init__(self, approvals: list[dict[str, object]] | None = None) -> None:
        self.approvals = approvals or []
        self.decisions: list[tuple[str, str]] = []
        self.default_apps_calls: list[dict[str, object]] = []

    async def open_ms_settings_default_apps(self, **kwargs: object) -> dict[str, object]:
        self.default_apps_calls.append(dict(kwargs))
        return {
            "stdout": "",
            "stderr": "",
            "exit_code": 0,
            "status": "completed",
            "host": True,
            "audit_id": "audit-default-apps",
        }

    async def list_approvals(self, _user_id: str, limit: int = 20) -> list[dict[str, object]]:
        return list(self.approvals)[:limit]

    async def decide_approval(self, approval_id: str, decision: str) -> dict[str, object]:
        self.decisions.append((approval_id, decision))
        return {
            "approval_id": approval_id,
            "action_kind": "write_host_file",
            "target_summary": "C:/Users/Ritesh/Desktop/todo.txt",
            "category": "ask_once",
            "status": decision,
            "payload": {"path": "C:/Users/Ritesh/Desktop/todo.txt"},
        }


class FakeSkill:
    def __init__(
        self,
        name: str,
        *,
        description: str = "Skill description",
        aliases: list[str] | None = None,
        activation_examples: list[str] | None = None,
        keywords: list[str] | None = None,
        user_invocable: bool = True,
        natural_language_enabled: bool = True,
    ) -> None:
        self.name = name
        self.description = description
        self.aliases = aliases or []
        self.activation_examples = activation_examples or []
        self.keywords = keywords or []
        self.user_invocable = user_invocable
        self.natural_language_enabled = natural_language_enabled
        self.priority = 0


class FakeSkillMatch:
    def __init__(self, skill, score: int, *, exact: bool = False) -> None:
        self.skill = skill
        self.score = score
        self.exact = exact


@pytest.mark.asyncio
async def test_router_shortcuts_latest_email_without_model(app_config) -> None:
    tool_registry = DummyToolRegistry()
    session_manager = DummySessionManager()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=session_manager,
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-1",
        request_id="req-1",
        session_key="webchat_main",
        message="what is the last mail i got",
        metadata={"trace_id": "trace-1"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is False
    assert "Latest email in your inbox" in response.payload["command_response"]
    assert tool_registry.calls[0][0] == "gmail_latest_email"
    assert [message["role"] for message in session_manager.messages] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_router_shortcuts_repo_count_without_model(app_config) -> None:
    tool_registry = DummyToolRegistry()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-1",
        request_id="req-2",
        session_key="webchat_main",
        message="how many repos do i have",
        metadata={"trace_id": "trace-2"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is False
    assert "2 repositories" in response.payload["command_response"]
    assert tool_registry.calls[0][0] == "github_list_repos"


@pytest.mark.asyncio
async def test_router_shortcuts_pull_request_check_uses_recent_repo_context(app_config) -> None:
    tool_registry = DummyToolRegistry()
    session_manager = DummySessionManager()
    await session_manager.append_message(
        session_manager.session,
        {
            "id": "msg-1",
            "role": "assistant",
            "content": "The repository is Rishiraj-Yadav/Personal-AI-Assistant.",
        },
    )
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=session_manager,
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-1",
        request_id="req-3",
        session_key="webchat_main",
        message="is there any pull request to this repo",
        metadata={"trace_id": "trace-3"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is False
    assert "Rishiraj-Yadav/Personal-AI-Assistant" in response.payload["command_response"]
    assert "#7: Improve routing" in response.payload["command_response"]
    assert tool_registry.calls[-1][0] == "github_list_pull_requests"


@pytest.mark.asyncio
async def test_router_shortcuts_win_before_skill_activation(app_config) -> None:
    tool_registry = DummyToolRegistry()
    skill = FakeSkill("gmail-triage", aliases=["check my inbox"], keywords=["inbox", "email"])
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(matches=[FakeSkillMatch(skill, 100, exact=True)]),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-1",
        request_id="req-4",
        session_key="webchat_main",
        message="what is the last email i received",
        metadata={"trace_id": "trace-4"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is False
    assert tool_registry.calls[0][0] == "gmail_latest_email"


@pytest.mark.asyncio
async def test_router_default_browser_shortcut_opens_settings(app_config) -> None:
    app_config.system_access.enabled = True
    sam = DummySystemAccessManager(approvals=[])
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(host_tools_enabled=False),
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
        system_access_manager=sam,
    )

    response = await router.route_user_message(
        connection_id="conn-browser-1",
        request_id="req-browser-1",
        session_key="webchat_main",
        message="can you change my default browser to brave",
        metadata={"trace_id": "trace-browser-1", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is False
    assert sam.default_apps_calls
    assert "Brave" in response.payload["command_response"]
    assert "Default apps" in response.payload["command_response"]


@pytest.mark.asyncio
async def test_router_brightness_shortcut_invokes_tool(app_config) -> None:
    tool_registry = DummyToolRegistry(host_tools_enabled=True)
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-bright-1",
        request_id="req-bright-1",
        session_key="webchat_main",
        message="can you decrease the brightness to 10 percent",
        metadata={"trace_id": "trace-bright-1", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is False
    assert tool_registry.calls[0][0] == "set_windows_brightness"
    assert tool_registry.calls[0][1]["percent"] == 10
    assert "10%" in response.payload["command_response"]


@pytest.mark.asyncio
async def test_router_host_shortcut_lists_downloads(app_config) -> None:
    tool_registry = DummyToolRegistry(host_tools_enabled=True)
    session_manager = DummySessionManager()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=session_manager,
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-host-1",
        request_id="req-host-1",
        session_key="telegram:123",
        message="show me files in my Downloads",
        metadata={"trace_id": "trace-host-1", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is False
    assert "Downloads" in response.payload["command_response"]
    assert "Resume.pdf" in response.payload["command_response"]
    assert tool_registry.calls[0][0] == "list_host_dir"


@pytest.mark.asyncio
async def test_router_host_shortcut_lists_downloads_without_show_me_phrase(app_config) -> None:
    tool_registry = DummyToolRegistry(host_tools_enabled=True)
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-host-1b",
        request_id="req-host-1b",
        session_key="telegram:123",
        message="show files in my downloads",
        metadata={"trace_id": "trace-host-1b", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is False
    assert "Downloads" in response.payload["command_response"]
    assert tool_registry.calls[0][0] == "list_host_dir"


@pytest.mark.asyncio
async def test_router_host_shortcut_searches_named_folder(app_config) -> None:
    tool_registry = DummyToolRegistry(host_tools_enabled=True)
    session_manager = DummySessionManager()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=session_manager,
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-host-2",
        request_id="req-host-2",
        session_key="telegram:123",
        message="search 5sem folder",
        metadata={"trace_id": "trace-host-2", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "5sem" in response.payload["command_response"]
    assert "Documents/college/5sem" in response.payload["command_response"]
    assert tool_registry.calls[0][0] == "search_host_files"
    assert tool_registry.calls[0][1]["root"] == "@allowed"


@pytest.mark.asyncio
async def test_router_host_shortcut_searches_named_folder_on_r_drive(app_config) -> None:
    tool_registry = DummyToolRegistry(host_tools_enabled=True)
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-host-2b",
        request_id="req-host-2b",
        session_key="telegram:123",
        message="5 sem folder in R drive",
        metadata={"trace_id": "trace-host-2b", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "R:/" in response.payload["command_response"]
    assert tool_registry.calls[0][0] == "search_host_files"
    assert tool_registry.calls[0][1]["root"] == "R:/"


@pytest.mark.asyncio
async def test_router_host_shortcut_opens_named_folder_and_lists_contents(app_config) -> None:
    tool_registry = DummyToolRegistry(host_tools_enabled=True)
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-host-2bb",
        request_id="req-host-2bb",
        session_key="telegram:123",
        message="oepn spcc folder",
        metadata={"trace_id": "trace-host-2bb", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "R:/6_semester/SPCC" in response.payload["command_response"]
    assert [call[0] for call in tool_registry.calls[:2]] == ["search_host_files", "list_host_dir"]


@pytest.mark.asyncio
async def test_router_host_shortcut_lists_folder_contents_when_user_asks_whats_inside(app_config) -> None:
    tool_registry = DummyToolRegistry(host_tools_enabled=True)
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-host-2c",
        request_id="req-host-2c",
        session_key="telegram:123",
        message="can you find the 5sem folder and tell me what is there inside it",
        metadata={"trace_id": "trace-host-2c", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "I found the folder at" in response.payload["command_response"]
    assert "dbms/" in response.payload["command_response"]
    assert [call[0] for call in tool_registry.calls[:2]] == ["search_host_files", "list_host_dir"]


@pytest.mark.asyncio
async def test_router_host_shortcut_opens_notepad(app_config) -> None:
    tool_registry = DummyToolRegistry(host_tools_enabled=True)
    session_manager = DummySessionManager()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=session_manager,
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-host-3",
        request_id="req-host-3",
        session_key="telegram:123",
        message="can you open notepad",
        metadata={"trace_id": "trace-host-3", "user_id": "default", "channel": "telegram"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "launched notepad" in response.payload["command_response"].lower()
    assert tool_registry.calls[0][0] == "exec_shell"
    assert tool_registry.calls[0][1]["host"] is True


@pytest.mark.asyncio
async def test_router_host_shortcut_creates_desktop_note_when_filename_and_content_are_given(app_config) -> None:
    tool_registry = DummyToolRegistry(host_tools_enabled=True)
    session_manager = DummySessionManager()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=session_manager,
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-host-4",
        request_id="req-host-4",
        session_key="telegram:123",
        message="create a note called todo on my Desktop with content Buy milk",
        metadata={"trace_id": "trace-host-4", "user_id": "default", "channel": "telegram"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "created todo.txt in your desktop" in response.payload["command_response"].lower()
    assert tool_registry.calls[0][0] == "write_host_file"
    assert tool_registry.calls[0][1]["path"] == "~/Desktop/todo.txt"


@pytest.mark.asyncio
async def test_router_host_shortcut_creates_file_in_recent_host_folder_context(app_config) -> None:
    tool_registry = DummyToolRegistry(host_tools_enabled=True)
    session_manager = DummySessionManager()
    await session_manager.append_message(
        session_manager.session,
        {
            "id": "msg-host-folder-1",
            "role": "assistant",
            "content": "Here's what's inside the R:\\6_semester\\SPCC folder:\n- SPCC Experiment 5.pdf",
        },
    )
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=session_manager,
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-host-4b",
        request_id="req-host-4b",
        session_key="telegram:123",
        message="Create a timepass.txt file there in which it is written hello world",
        metadata={"trace_id": "trace-host-4b", "user_id": "default", "channel": "telegram"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "created timepass.txt in your r:\\6_semester\\spcc" in response.payload["command_response"].lower()
    assert tool_registry.calls[0][0] == "write_host_file"
    assert tool_registry.calls[0][1]["path"] == "R:\\6_semester\\SPCC/timepass.txt"
    assert tool_registry.calls[0][1]["content"] == "hello world"


@pytest.mark.asyncio
async def test_router_host_shortcut_creates_file_in_named_folder_context(app_config) -> None:
    tool_registry = DummyToolRegistry(host_tools_enabled=True)
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-host-4c",
        request_id="req-host-4c",
        session_key="telegram:123",
        message="create a timepass.py file in the SPCC folder with content print('hello')",
        metadata={"trace_id": "trace-host-4c", "user_id": "default", "channel": "telegram"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "created timepass.py in your r:/6_semester/spcc" in response.payload["command_response"].lower()
    assert tool_registry.calls[0][0] == "search_host_files"
    assert tool_registry.calls[1][0] == "write_host_file"
    assert tool_registry.calls[1][1]["path"] == "R:/6_semester/SPCC/timepass.py"
    assert tool_registry.calls[1][1]["content"] == "print('hello')"


@pytest.mark.asyncio
async def test_router_host_shortcut_creates_pdf_file_when_extension_is_provided(app_config) -> None:
    tool_registry = DummyToolRegistry(host_tools_enabled=True)
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-host-4d",
        request_id="req-host-4d",
        session_key="telegram:123",
        message="create a report.pdf file in the SPCC folder with content hello world",
        metadata={"trace_id": "trace-host-4d", "user_id": "default", "channel": "telegram"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert tool_registry.calls[1][0] == "write_host_file"
    assert tool_registry.calls[1][1]["path"] == "R:/6_semester/SPCC/report.pdf"


@pytest.mark.asyncio
async def test_router_accepts_slash_commands_with_telegram_bot_suffix(app_config) -> None:
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(),
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-command-1",
        request_id="req-command-1",
        session_key="telegram:123",
        message="/skills@sonar_new_bot",
        metadata={"trace_id": "trace-command-1", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is False
    assert "No skills are currently enabled." in response.payload["command_response"]


@pytest.mark.asyncio
async def test_router_host_approve_without_id_uses_single_pending_approval(app_config) -> None:
    system_access_manager = DummySystemAccessManager(
        approvals=[
            {
                "approval_id": "approval-123",
                "status": "pending",
                "expired": False,
                "action_kind": "write_host_file",
                "target_summary": "C:/Users/Ritesh/Desktop/todo.txt",
            }
        ]
    )
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(),
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        system_access_manager=system_access_manager,
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-command-2",
        request_id="req-command-2",
        session_key="telegram:123",
        message="/host-approve",
        metadata={"trace_id": "trace-command-2", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert "Approved host approval 'approval-123'." in response.payload["command_response"]
    assert system_access_manager.decisions == [("approval-123", "approved")]


@pytest.mark.asyncio
async def test_router_browser_commands_are_available_for_channel_use(app_config) -> None:
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(),
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    profiles = await router.route_user_message(
        connection_id="conn-browser-1",
        request_id="req-browser-1",
        session_key="telegram:123",
        message="/browser profiles",
        metadata={"trace_id": "trace-browser-1", "user_id": "default"},
        mode=QueueMode.STEER,
    )
    tabs = await router.route_user_message(
        connection_id="conn-browser-2",
        request_id="req-browser-2",
        session_key="telegram:123",
        message="/browser tabs",
        metadata={"trace_id": "trace-browser-2", "user_id": "default"},
        mode=QueueMode.STEER,
    )
    logs = await router.route_user_message(
        connection_id="conn-browser-3",
        request_id="req-browser-3",
        session_key="telegram:123",
        message="/browser logs 2",
        metadata={"trace_id": "trace-browser-3", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert profiles.ok is True
    assert "Saved browser profiles:" in profiles.payload["command_response"]
    assert "github.com/work" in profiles.payload["command_response"]
    assert tabs.ok is True
    assert "Open browser tabs:" in tabs.payload["command_response"]
    assert "tab-1 [active]" in tabs.payload["command_response"]
    assert logs.ok is True
    assert "Recent browser logs:" in logs.payload["command_response"]


@pytest.mark.asyncio
async def test_router_browser_macros_respect_disabled_flag(app_config) -> None:
    app_config.browser_workflows.macro_shortcuts_enabled = False
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(),
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-browser-macro-disabled",
        request_id="req-browser-macro-disabled",
        session_key="telegram:123",
        message="/browser macros",
        metadata={"trace_id": "trace-browser-macro-disabled", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is False
    assert "disabled" in response.error.lower()


@pytest.mark.asyncio
async def test_router_browser_watch_commands_are_available(app_config) -> None:
    monitor_service = DummyBrowserMonitorService()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(),
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
        browser_monitor_service=monitor_service,
    )

    created = await router.route_user_message(
        connection_id="conn-browser-watch-1",
        request_id="req-browser-watch-1",
        session_key="telegram:123",
        message="/browser watch example.com price changes",
        metadata={"trace_id": "trace-browser-watch-1", "user_id": "default"},
        mode=QueueMode.STEER,
    )
    listed = await router.route_user_message(
        connection_id="conn-browser-watch-2",
        request_id="req-browser-watch-2",
        session_key="telegram:123",
        message="/browser watches",
        metadata={"trace_id": "trace-browser-watch-2", "user_id": "default"},
        mode=QueueMode.STEER,
    )
    deleted = await router.route_user_message(
        connection_id="conn-browser-watch-3",
        request_id="req-browser-watch-3",
        session_key="telegram:123",
        message="/browser unwatch watch-1",
        metadata={"trace_id": "trace-browser-watch-3", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert created.ok is True
    assert "Created browser watch 'watch-1'." in created.payload["command_response"]
    assert monitor_service.created == [("default", "https://example.com", "price changes")]
    assert listed.ok is True
    assert "Saved browser watches:" in listed.payload["command_response"]
    assert deleted.ok is True
    assert "Removed browser watch 'watch-1'." in deleted.payload["command_response"]


@pytest.mark.asyncio
async def test_router_intercepts_pending_otp_reply_before_generic_routing(app_config) -> None:
    runtime = ChallengeBrowserRuntimeStub()
    engine = ChallengeEngineStub("Filled the OTP and resumed the browser task.")
    tool_registry = DummyToolRegistry()
    tool_registry.browser_runtime = runtime
    session_manager = DummySessionManager()
    session_manager.session.metadata = browser_task_state_update(
        active_task={"site_name": "irctc", "blocked_reason": "otp"},
        pending_otp={"site_name": "irctc", "selector": "input[name=otp]"},
    )
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=session_manager,
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=tool_registry,
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
        browser_workflow_engine=engine,
    )

    response = await router.route_user_message(
        connection_id="conn-browser-otp",
        request_id="req-browser-otp",
        session_key="telegram:123",
        message="123456",
        metadata={"trace_id": "trace-browser-otp", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["command_response"] == "Filled the OTP and resumed the browser task."
    assert runtime.submitted_otp == "123456"
    assert engine.calls == [("continue", "default")]


def test_router_compose_browser_workflow_response_dedupes_progress(app_config) -> None:
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(),
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )
    result = type(
        "WorkflowResultStub",
        (),
        {
            "progress_lines": ["Opened YouTube."],
            "response_text": "Opened YouTube in tab-1.",
        },
    )()

    response = router._compose_browser_workflow_response(result)

    assert response == "Opened YouTube in tab-1."


@pytest.mark.asyncio
async def test_router_cron_add_list_pause_resume_delete_flow(app_config) -> None:
    automation_engine = DummyAutomationEngine()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(),
        automation_engine=automation_engine,
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    add_response = await router.route_user_message(
        connection_id="conn-cron-1",
        request_id="req-cron-1",
        session_key="telegram:123",
        message='/cron add "0 8 * * *" "Good morning briefing"',
        metadata={"trace_id": "trace-cron-1", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert add_response.ok is True
    assert "Created cron job 'cron-user-1'" in add_response.payload["command_response"]

    list_response = await router.route_user_message(
        connection_id="conn-cron-2",
        request_id="req-cron-2",
        session_key="telegram:123",
        message="/cron list",
        metadata={"trace_id": "trace-cron-2", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert list_response.ok is True
    assert "cron-user-1: active | direct | 0 8 * * * | Good morning briefing" in list_response.payload["command_response"]

    pause_response = await router.route_user_message(
        connection_id="conn-cron-3",
        request_id="req-cron-3",
        session_key="telegram:123",
        message="/cron pause cron-user-1",
        metadata={"trace_id": "trace-cron-3", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert pause_response.ok is True
    assert "Paused cron job 'cron-user-1'." in pause_response.payload["command_response"]
    assert automation_engine.dynamic_jobs[0]["paused"] is True

    resume_response = await router.route_user_message(
        connection_id="conn-cron-4",
        request_id="req-cron-4",
        session_key="telegram:123",
        message="/cron resume cron-user-1",
        metadata={"trace_id": "trace-cron-4", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert resume_response.ok is True
    assert "Resumed cron job 'cron-user-1'." in resume_response.payload["command_response"]
    assert automation_engine.dynamic_jobs[0]["paused"] is False

    delete_response = await router.route_user_message(
        connection_id="conn-cron-5",
        request_id="req-cron-5",
        session_key="telegram:123",
        message="/cron delete cron-user-1",
        metadata={"trace_id": "trace-cron-5", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert delete_response.ok is True
    assert "Deleted cron job 'cron-user-1'." in delete_response.payload["command_response"]
    assert automation_engine.dynamic_jobs == []


@pytest.mark.asyncio
async def test_router_cron_add_accepts_pipe_syntax(app_config) -> None:
    automation_engine = DummyAutomationEngine()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(),
        automation_engine=automation_engine,
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-cron-6",
        request_id="req-cron-6",
        session_key="telegram:123",
        message="/cron add */10 * * * * | Cron test message",
        metadata={"trace_id": "trace-cron-6", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert automation_engine.dynamic_jobs[0]["schedule"] == "*/10 * * * *"
    assert automation_engine.dynamic_jobs[0]["message"] == "Cron test message"


@pytest.mark.asyncio
async def test_router_creates_dynamic_cron_from_daily_reminder_phrase(app_config) -> None:
    automation_engine = DummyAutomationEngine()
    session_manager = DummySessionManager()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=session_manager,
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(),
        automation_engine=automation_engine,
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-cron-nl-1",
        request_id="req-cron-nl-1",
        session_key="telegram:123",
        message="remind me every day at 8 am to go to college",
        metadata={"trace_id": "trace-cron-nl-1", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is False
    assert "Created cron job 'cron-user-1' on 0 8 * * *." in response.payload["command_response"]
    assert automation_engine.dynamic_jobs[0]["schedule"] == "0 8 * * *"
    assert automation_engine.dynamic_jobs[0]["message"] == "Reminder: go to college"
    assert [message["role"] for message in session_manager.messages] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_router_creates_dynamic_cron_from_named_day_reminder_phrase(app_config) -> None:
    automation_engine = DummyAutomationEngine()
    router = GatewayRouter(
        config=app_config,
        agent_loop=DummyAgentLoop(),
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(),
        automation_engine=automation_engine,
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-cron-nl-2",
        request_id="req-cron-nl-2",
        session_key="telegram:123",
        message="create a cron job to remind me every monday at 9:30 pm to submit attendance",
        metadata={"trace_id": "trace-cron-nl-2", "user_id": "default"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is False
    assert automation_engine.dynamic_jobs[0]["schedule"] == "30 21 * * 1"
    assert automation_engine.dynamic_jobs[0]["message"] == "Reminder: submit attendance"


@pytest.mark.asyncio
async def test_router_auto_activates_exact_skill_match(app_config) -> None:
    agent_loop = DummyAgentLoop()
    skill = FakeSkill(
        "gmail-triage",
        description="Inbox triage",
        aliases=["check my inbox"],
        activation_examples=["triage my emails"],
        keywords=["email", "inbox"],
    )
    router = GatewayRouter(
        config=app_config,
        agent_loop=agent_loop,
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(enabled=[skill], matches=[FakeSkillMatch(skill, 100, exact=True)]),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(),
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-1",
        request_id="req-5",
        session_key="webchat_main",
        message="check my inbox",
        metadata={"trace_id": "trace-5"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is True
    assert response.payload["activated_skill"] == "gmail-triage"
    enqueued = agent_loop.enqueued[-1]
    assert enqueued.metadata["activated_skill"] == "gmail-triage"
    assert enqueued.metadata["skill_activation_source"] == "exact"
    assert "## Active Skill" in (enqueued.system_suffix or "")


@pytest.mark.asyncio
async def test_router_falls_back_when_skill_match_is_ambiguous(app_config) -> None:
    agent_loop = DummyAgentLoop()
    first = FakeSkill("daily-briefing", aliases=["daily briefing"], keywords=["briefing", "today"])
    second = FakeSkill("gmail-briefing", aliases=["inbox briefing"], keywords=["briefing", "email"])
    router = GatewayRouter(
        config=app_config,
        agent_loop=agent_loop,
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(
            enabled=[first, second],
            matches=[FakeSkillMatch(first, 5), FakeSkillMatch(second, 4)],
        ),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(has_llm_task=False),
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-1",
        request_id="req-6",
        session_key="webchat_main",
        message="give me a briefing",
        metadata={"trace_id": "trace-6"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is True
    enqueued = agent_loop.enqueued[-1]
    assert "activated_skill" not in enqueued.metadata


@pytest.mark.asyncio
async def test_router_uses_classifier_for_plausible_skill_candidates(app_config) -> None:
    agent_loop = DummyAgentLoop()
    target = FakeSkill("daily-briefing", aliases=["morning briefing"], keywords=["briefing", "today"])
    other = FakeSkill("gmail-briefing", aliases=["inbox briefing"], keywords=["briefing", "email"])
    router = GatewayRouter(
        config=app_config,
        agent_loop=agent_loop,
        connection_manager=DummyConnectionManager(),
        session_manager=DummySessionManager(),
        memory_manager=None,
        skill_registry=DummySkillRegistry(
            enabled=[target, other],
            matches=[FakeSkillMatch(target, 5), FakeSkillMatch(other, 4)],
        ),
        hook_runner=DummyHookRunner(),
        presence_registry=DummyPresenceRegistry(),
        oauth_flow_manager=DummyOAuthFlowManager(),
        tool_registry=DummyToolRegistry(
            llm_task_response={"content": json.dumps({"skill": "daily-briefing", "confidence": 0.93})}
        ),
        automation_engine=DummyAutomationEngine(),
        user_profiles=DummyUserProfiles(),
        started_at=datetime.now(timezone.utc),
    )

    response = await router.route_user_message(
        connection_id="conn-1",
        request_id="req-7",
        session_key="webchat_main",
        message="give me a briefing for today",
        metadata={"trace_id": "trace-7"},
        mode=QueueMode.STEER,
    )

    assert response.ok is True
    assert response.payload["queued"] is True
    assert response.payload["activated_skill"] == "daily-briefing"
    enqueued = agent_loop.enqueued[-1]
    assert enqueued.metadata["skill_activation_source"] == "classifier"
