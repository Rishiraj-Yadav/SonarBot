from __future__ import annotations

import json
from datetime import datetime, timezone
import re

import pytest

from assistant.browser_workflows.engine import BrowserWorkflowEngine
from assistant.browser_workflows.models import BrowserWorkflowMatch
from assistant.browser_workflows.nlp import BrowserWorkflowNLP
from assistant.gateway.router import GatewayRouter


class FakeLocator:
    def __init__(self, page) -> None:
        self.page = page

    async def fill(self, text: str) -> None:
        self.page.last_filled = text


class FakePage:
    def __init__(self) -> None:
        self.url = "about:blank"
        self.last_filled = ""

    async def goto(self, url: str, wait_until: str | None = None):
        self.url = url
        return None

    async def content(self) -> str:
        return "<html><body>Fake page</body></html>"

    async def title(self) -> str:
        return "Fake page"


class FakeBrowserRuntime:
    def __init__(self) -> None:
        self.page = FakePage()
        self.current_tab_id = "tab-1"
        self.current_headless = True
        self.current_mode_value = "headless"
        self.current_user_id = "default"
        self.visited_urls: list[str] = []
        self.tabs: list[dict[str, object]] = [
            {"tab_id": "tab-1", "url": self.page.url, "title": "Fake page", "mode": "headless", "active": True}
        ]
        self.sessions: list[dict[str, object]] = []
        self.results: list[dict[str, object]] = []
        self.blocker: dict[str, object] | None = None
        self.events: list[tuple[str, dict[str, object]]] = []
        self.play_started = False
        self.pending_login: dict[str, object] | None = None
        self.pending_action: dict[str, object] | None = None
        self.requested_headless: list[bool | None] = []
        self.pressed_keys: list[str] = []

    def current_state(self) -> dict[str, object]:
        active_tab = next((tab for tab in self.tabs if str(tab.get("tab_id")) == self.current_tab_id), None) or {
            "tab_id": self.current_tab_id,
            "url": self.page.url,
            "title": "Fake page",
            "mode": self.current_mode_value,
            "active": True,
        }
        return {
            "active_profile": None,
            "current_tab_id": self.current_tab_id,
            "active_tab": active_tab,
            "tabs": list(self.tabs),
            "current_mode": self.current_mode_value,
            "headless": self.current_headless,
            "pending_login": self.pending_login,
            "pending_protected_action": self.pending_action,
        }

    def list_sessions(self) -> list[dict[str, object]]:
        return list(self.sessions)

    def current_mode(self) -> str:
        return self.current_mode_value

    async def get_page(self, target_url: str | None = None, **kwargs):
        self.requested_headless.append(kwargs.get("headless"))
        headless = kwargs.get("headless")
        if headless is not None:
            self.current_headless = bool(headless)
            self.current_mode_value = "headless" if headless else "headed"
        if target_url:
            self.visited_urls.append(target_url)
            self.page.url = target_url
        self._set_active_tab(self.current_tab_id, self.page.url, self.current_mode_value)
        return self.page

    def wait_state_for_navigation(self, _wait_for: str | None) -> str:
        return "domcontentloaded"

    async def post_action_wait(self, _page, _wait_for: str | None, _timeout_seconds: int) -> None:
        return None

    async def refresh_active_tab(self, _user_id: str | None = None) -> None:
        return None

    async def find_search_input(self, _page, **_kwargs):
        return FakeLocator(self.page), "fake"

    async def press_key(self, _page, key: str, **_kwargs) -> None:
        self.pressed_keys.append(key)
        if key.lower() == "enter":
            return None

    async def wait_for_url_match(self, _page, pattern: str, *, timeout_seconds: int = 10) -> bool:
        return re.search(pattern, self.page.url, flags=re.IGNORECASE) is not None

    async def extract_search_results(self, _page, **_kwargs) -> list[dict[str, object]]:
        return list(self.results)

    async def capture_dom_snapshot(self, _page) -> dict[str, object]:
        text = " ".join(str(item.get("title", "")) for item in self.results).strip()
        return {"text": text or "Fake page content"}

    async def click_best_match(self, page, query: str, candidates: list[dict[str, object]], **kwargs):
        chosen = candidates[0] if kwargs.get("open_first_result") else candidates[-1]
        page.url = str(chosen.get("href", page.url))
        self.current_tab_id = "tab-2"
        self._set_active_tab(self.current_tab_id, page.url, self.current_mode_value, title=str(chosen.get("title", "Fake page")))
        return chosen

    async def try_start_media_playback(self, _page) -> None:
        self.play_started = True

    async def detect_blocking_state(self, _page):
        return self.blocker

    async def emit_workflow_event(self, _user_id: str, event_name: str, payload: dict[str, object]) -> None:
        self.events.append((event_name, payload))

    async def start_login(self, site_name: str, profile_name: str, login_url: str, *, user_id: str | None = None):
        self.pending_login = {
            "site_name": site_name,
            "profile_name": profile_name,
            "login_url": login_url,
        }
        self.current_headless = False
        self.current_mode_value = "headed"
        self.page.url = login_url
        self._set_active_tab(self.current_tab_id, login_url, "headed", title=f"{site_name} login")
        return self.page

    async def open_visible_intervention(self, url: str, *, user_id: str | None = None):
        self.current_headless = False
        self.current_mode_value = "headed"
        self.page.url = url
        self._set_active_tab(self.current_tab_id, url, "headed", title="Visible intervention")
        return {"url": url, "tab_id": self.current_tab_id, "headless": False}

    async def finalize_pending_login_if_complete(self, *, user_id: str | None = None):
        if self.pending_login is None:
            return None
        saved = {
            "site_name": self.pending_login["site_name"],
            "profile_name": self.pending_login["profile_name"],
            "status": "active",
            "url": self.page.url,
        }
        self.sessions = [saved]
        self.pending_login = None
        self.current_headless = True
        self.current_mode_value = "headless"
        self._set_active_tab(self.current_tab_id, self.page.url, "headless")
        return saved

    def pending_protected_action(self):
        return dict(self.pending_action) if self.pending_action else None

    async def prepare_protected_action(self, action_type: str, **kwargs):
        self.pending_action = {
            "action_type": action_type,
            "selector": kwargs.get("selector", ""),
            "target": kwargs.get("target", self.page.url),
            "awaiting_followup": "confirmation",
            "tab_id": self.current_tab_id,
        }
        self.current_headless = False
        self.current_mode_value = "headed"
        self._set_active_tab(self.current_tab_id, self.page.url, "headed")
        return dict(self.pending_action)

    async def confirm_pending_action(self, *, user_id: str | None = None):
        if self.pending_action is None:
            raise RuntimeError("No pending action")
        action = dict(self.pending_action)
        self.pending_action = None
        self.current_headless = True
        self.current_mode_value = "headless"
        self._set_active_tab(self.current_tab_id, self.page.url, "headless")
        return action

    async def cancel_pending_action(self, *, user_id: str | None = None):
        if self.pending_action is None:
            raise RuntimeError("No pending action")
        action = dict(self.pending_action)
        self.pending_action = None
        self.current_headless = True
        self.current_mode_value = "headless"
        self._set_active_tab(self.current_tab_id, self.page.url, "headless")
        return action

    async def switch_to_matching_tab(
        self,
        *,
        target_url: str | None = None,
        site_name: str | None = None,
        prefer_mode: str | None = None,
        user_id: str | None = None,
    ):
        for tab in self.tabs:
            tab_url = str(tab.get("url", ""))
            tab_mode = str(tab.get("mode", ""))
            if prefer_mode and tab_mode != prefer_mode:
                continue
            if target_url and tab_url == target_url:
                self.current_tab_id = str(tab["tab_id"])
                self.current_mode_value = tab_mode
                self.current_headless = tab_mode == "headless"
                self.page.url = tab_url
                self._mark_active(self.current_tab_id)
                return dict(tab)
            if site_name and site_name in tab_url.lower():
                self.current_tab_id = str(tab["tab_id"])
                self.current_mode_value = tab_mode
                self.current_headless = tab_mode == "headless"
                self.page.url = tab_url
                self._mark_active(self.current_tab_id)
                return dict(tab)
        return None

    def _mark_active(self, tab_id: str) -> None:
        for tab in self.tabs:
            tab["active"] = str(tab.get("tab_id")) == tab_id

    def _set_active_tab(self, tab_id: str, url: str, mode: str, *, title: str = "Fake page") -> None:
        updated = False
        for tab in self.tabs:
            if str(tab.get("tab_id")) == tab_id:
                tab["url"] = url
                tab["mode"] = mode
                tab["title"] = title
                updated = True
                break
        if not updated:
            self.tabs.append({"tab_id": tab_id, "url": url, "title": title, "mode": mode, "active": True})
        self._mark_active(tab_id)


class FakeToolRegistry:
    def __init__(self, runtime: FakeBrowserRuntime, *, llm_task_response: dict[str, object] | None = None) -> None:
        self.browser_runtime = runtime
        self.llm_task_response = llm_task_response

    def has(self, tool_name: str) -> bool:
        if tool_name == "llm_task":
            return self.llm_task_response is not None
        if tool_name in {"browser_sessions_list", "browser_login"}:
            return True
        return False

    async def dispatch(self, tool_name: str, payload: dict[str, object]) -> dict[str, object]:
        if tool_name == "llm_task":
            assert self.llm_task_response is not None
            return {"content": json.dumps(self.llm_task_response)}
        if tool_name == "browser_login":
            site_name = str(payload["site_name"])
            saved = {"site_name": site_name, "profile_name": "default", "status": "active", "url": f"https://{site_name}.com"}
            self.browser_runtime.sessions = [saved]
            return saved
        if tool_name == "browser_sessions_list":
            return {"sessions": self.browser_runtime.list_sessions()}
        if tool_name == "github_list_repos":
            return {
                "repositories": [
                    {
                        "full_name": "Rishiraj-Yadav/SonarBot",
                        "default_branch": "main",
                        "description": "Personal AI assistant",
                        "html_url": "https://github.com/Rishiraj-Yadav/SonarBot",
                    }
                ]
            }
        if tool_name == "github_list_pull_requests":
            return {"owner": payload["owner"], "repo": payload["repo"], "pull_requests": []}
        if tool_name == "github_list_issues":
            return {"owner": payload["owner"], "repo": payload["repo"], "issues": []}
        raise AssertionError(f"Unexpected tool dispatch: {tool_name}")


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
        self.session = type("Session", (), {"session_key": "webchat_main", "metadata": {}})()

    async def load_or_create(self, session_key: str):
        self.session.session_key = session_key
        return self.session

    async def append_message(self, session, message):
        self.messages.append(message)

    async def session_history(self, _session_key: str, limit: int = 20):
        return self.messages[-limit:]

    async def update_metadata(self, _session_key: str, updates=None, *, remove_keys=None):
        updates = updates or {}
        self.session.metadata.update(updates)
        if remove_keys:
            for key in remove_keys:
                self.session.metadata.pop(key, None)
        return dict(self.session.metadata)

    def active_count(self) -> int:
        return 1


class DummySkillRegistry:
    def active_count(self) -> int:
        return 0

    def match_natural_language(self, _message: str):
        return []

    def find_user_invocable(self, _name: str):
        return None

    def list_enabled(self):
        return []


class DummyHookRunner:
    async def fire_event(self, *_args, **_kwargs):
        return type("HookEvent", (), {"messages": []})()


class DummyPresenceRegistry:
    def snapshot(self):
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


class DummyAutomationEngine:
    async def list_notifications(self, _user_id: str):
        return []

    async def list_runs(self, _user_id: str):
        return []

    async def list_rules(self, _user_id: str):
        return []


class DummyUserProfiles:
    async def resolve_user_id(self, _identity_type: str, _identity_value: str, _metadata=None) -> str:
        return "default"


@pytest.mark.asyncio
async def test_browser_workflow_nlp_matches_youtube_phrase(app_config) -> None:
    runtime = FakeBrowserRuntime()
    nlp = BrowserWorkflowNLP(app_config, FakeToolRegistry(runtime))

    match = await nlp.match("open youtube and play Trapped On An Island Until I Build A Boat")

    assert match is not None
    assert match.recipe_name == "youtube_search_play"
    assert match.site_name == "youtube"
    assert match.query == "Trapped On An Island Until I Build A Boat"


@pytest.mark.asyncio
async def test_browser_workflow_nlp_matches_on_youtube_search_phrase(app_config) -> None:
    runtime = FakeBrowserRuntime()
    nlp = BrowserWorkflowNLP(app_config, FakeToolRegistry(runtime))

    match = await nlp.match("On YouTube and search for assistant")

    assert match is not None
    assert match.recipe_name == "youtube_search_play"
    assert match.site_name == "youtube"
    assert match.query == "assistant"


@pytest.mark.asyncio
async def test_browser_workflow_nlp_uses_classifier_for_ambiguous_request(app_config) -> None:
    runtime = FakeBrowserRuntime()
    nlp = BrowserWorkflowNLP(
        app_config,
        FakeToolRegistry(
            runtime,
            llm_task_response={
                "recipe_name": "site_open_and_search",
                "confidence": 0.91,
                "site_name": "leetcode",
                "query": "arrays problems",
                "action": "search",
                "open_first_result": False,
            },
        ),
    )

    match = await nlp.match("please browse leetcode for arrays problems")

    assert match is not None
    assert match.recipe_name == "site_open_and_search"
    assert match.site_name == "leetcode"
    assert match.query == "arrays problems"


@pytest.mark.asyncio
async def test_browser_workflow_nlp_matches_arbitrary_domain_open(app_config) -> None:
    runtime = FakeBrowserRuntime()
    nlp = BrowserWorkflowNLP(app_config, FakeToolRegistry(runtime))

    match = await nlp.match("open erp.vcet.edu.in")

    assert match is not None
    assert match.recipe_name == "site_open_exact_url_or_path"
    assert match.site_name == "erp.vcet.edu.in"
    assert match.action == "open"
    assert match.details["target_url"] == "https://erp.vcet.edu.in"


@pytest.mark.asyncio
async def test_browser_workflow_nlp_matches_leetcode_problem_request(app_config) -> None:
    runtime = FakeBrowserRuntime()
    nlp = BrowserWorkflowNLP(app_config, FakeToolRegistry(runtime))

    match = await nlp.match("open the 654 problem on leetcode")

    assert match is not None
    assert match.recipe_name == "leetcode_open_problem"
    assert match.site_name == "leetcode"
    assert match.query == "654"


@pytest.mark.asyncio
async def test_browser_workflow_nlp_matches_youtube_latest_request(app_config) -> None:
    runtime = FakeBrowserRuntime()
    nlp = BrowserWorkflowNLP(app_config, FakeToolRegistry(runtime))

    match = await nlp.match("Now run the latest video of MrBeast")

    assert match is not None
    assert match.recipe_name == "youtube_latest_video"
    assert match.query == "MrBeast"


@pytest.mark.asyncio
async def test_browser_workflow_engine_opens_youtube_search_results_directly(app_config) -> None:
    runtime = FakeBrowserRuntime()
    runtime.results = [
        {"title": "Assistant tutorial", "href": "https://www.youtube.com/watch?v=abc123"},
    ]
    engine = BrowserWorkflowEngine(app_config, FakeToolRegistry(runtime))

    result = await engine.run_match(
        BrowserWorkflowMatch(
            recipe_name="youtube_search_play",
            confidence=0.96,
            site_name="youtube",
            query="assistant",
            action="play",
            details={"task_state": {}},
        ),
        user_id="default",
        session_key="webchat_main",
        channel="webchat",
        previous_state={},
    )

    assert result.status == "completed"
    assert "assistant" in result.response_text.lower()
    assert any("results?search_query=assistant" in url for url in runtime.visited_urls)
    assert runtime.page.last_filled == "assistant"
    assert "Enter" in runtime.pressed_keys


@pytest.mark.asyncio
async def test_browser_workflow_engine_types_and_submits_youtube_search_query(app_config) -> None:
    runtime = FakeBrowserRuntime()
    runtime.results = [
        {"title": "MrBeast latest upload", "href": "https://www.youtube.com/watch?v=xyz987"},
    ]
    engine = BrowserWorkflowEngine(app_config, FakeToolRegistry(runtime))

    result = await engine.run_match(
        BrowserWorkflowMatch(
            recipe_name="youtube_latest_video",
            confidence=0.96,
            site_name="youtube",
            query="MrBeast",
            action="play",
            details={"task_state": {}},
        ),
        user_id="default",
        session_key="webchat_main",
        channel="webchat",
        previous_state={},
    )

    assert result.status == "completed"
    assert runtime.page.last_filled == "MrBeast latest video"
    assert "Enter" in runtime.pressed_keys
    assert any("results?search_query=MrBeast+latest+video" in url for url in runtime.visited_urls)


@pytest.mark.asyncio
async def test_browser_workflow_nlp_matches_youtube_followup_on_active_site(app_config) -> None:
    runtime = FakeBrowserRuntime()
    runtime.page.url = "https://www.youtube.com"
    runtime._set_active_tab("tab-1", "https://www.youtube.com", "headed", title="YouTube")
    nlp = BrowserWorkflowNLP(app_config, FakeToolRegistry(runtime))

    match = await nlp.match(
        "now search mr beast video and play it",
        runtime_state=runtime.current_state(),
        previous_state={
            "active_task": {
                "recipe_name": "site_open_exact_url_or_path",
                "site_name": "youtube",
                "target_url": "https://www.youtube.com",
                "execution_mode": "headed",
                "awaiting_followup": "site_action",
            }
        },
    )

    assert match is not None
    assert match.recipe_name == "youtube_search_play"
    assert match.query == "mr beast video"


@pytest.mark.asyncio
async def test_browser_workflow_nlp_does_not_hijack_explicit_site_open_with_active_youtube(app_config) -> None:
    runtime = FakeBrowserRuntime()
    runtime.page.url = "https://www.youtube.com/watch?v=abc123"
    runtime._set_active_tab("tab-1", "https://www.youtube.com/watch?v=abc123", "headed", title="YouTube")
    nlp = BrowserWorkflowNLP(app_config, FakeToolRegistry(runtime))

    match = await nlp.match(
        "open google",
        runtime_state=runtime.current_state(),
        previous_state={
            "active_task": {
                "recipe_name": "youtube_search_play",
                "site_name": "youtube",
                "target_url": "https://www.youtube.com/watch?v=abc123",
                "execution_mode": "headed",
            }
        },
    )

    assert match is not None
    assert match.recipe_name == "site_open_and_search"
    assert match.site_name == "google"
    assert match.query is None


@pytest.mark.asyncio
async def test_browser_workflow_nlp_tolerates_none_active_tab_in_runtime_state(app_config) -> None:
    runtime = FakeBrowserRuntime()
    nlp = BrowserWorkflowNLP(app_config, FakeToolRegistry(runtime))

    match = await nlp.match(
        "pause the video",
        runtime_state={"active_tab": None, "tabs": [], "current_mode": "headless", "headless": True},
        previous_state={},
        force=True,
    )

    assert match is None


@pytest.mark.asyncio
async def test_browser_workflow_nlp_uses_previous_task_for_open_browser_request(app_config) -> None:
    runtime = FakeBrowserRuntime()
    nlp = BrowserWorkflowNLP(app_config, FakeToolRegistry(runtime))

    match = await nlp.match(
        "open browser",
        runtime_state=runtime.current_state(),
        previous_state={
            "active_task": {
                "recipe_name": "github_repo_inspect",
                "site_name": "github",
                "query": "Rishiraj-Yadav/SonarBot",
                "target_url": "https://github.com/Rishiraj-Yadav/SonarBot",
                "execution_mode": "headless",
            }
        },
    )

    assert match is not None
    assert match.recipe_name == "site_open_exact_url_or_path"
    assert match.site_name == "github"
    assert match.details["target_url"] == "https://github.com/Rishiraj-Yadav/SonarBot"


@pytest.mark.asyncio
async def test_browser_workflow_nlp_handles_typo_variant_in_show_me_override(app_config) -> None:
    runtime = FakeBrowserRuntime()
    nlp = BrowserWorkflowNLP(app_config, FakeToolRegistry(runtime))

    match = await nlp.match(
        "open browser and show me waht you're doing",
        previous_state={
            "active_task": {
                "recipe_name": "github_repo_inspect",
                "site_name": "github",
                "query": "Rishiraj-Yadav/SonarBot",
                "target_url": "https://github.com/Rishiraj-Yadav/SonarBot",
                "execution_mode": "headless",
            }
        },
    )

    assert match is not None
    assert match.recipe_name == "site_open_exact_url_or_path"
    assert match.details["execution_mode_override"] == "headed"
    assert match.details["target_url"] == "https://github.com/Rishiraj-Yadav/SonarBot"


@pytest.mark.asyncio
async def test_browser_workflow_nlp_does_not_treat_local_file_read_as_browser_intent(app_config) -> None:
    runtime = FakeBrowserRuntime()
    nlp = BrowserWorkflowNLP(app_config, FakeToolRegistry(runtime))

    match = await nlp.match("Read TOOLS.md", previous_state={})

    assert match is None


@pytest.mark.asyncio
async def test_browser_workflow_nlp_applies_execution_override(app_config) -> None:
    runtime = FakeBrowserRuntime()
    nlp = BrowserWorkflowNLP(app_config, FakeToolRegistry(runtime))

    match = await nlp.match("show me what you're doing and open github")

    assert match is not None
    assert match.recipe_name == "site_open_and_search"
    assert match.details["execution_mode_override"] == "headed"


@pytest.mark.asyncio
async def test_browser_workflow_nlp_applies_execution_override_for_natural_variant(app_config) -> None:
    runtime = FakeBrowserRuntime()
    nlp = BrowserWorkflowNLP(app_config, FakeToolRegistry(runtime))

    match = await nlp.match("show me what are you doing now and open github")

    assert match is not None
    assert match.details["execution_mode_override"] == "headed"


@pytest.mark.asyncio
async def test_browser_workflow_nlp_applies_headless_override(app_config) -> None:
    runtime = FakeBrowserRuntime()
    nlp = BrowserWorkflowNLP(app_config, FakeToolRegistry(runtime))

    match = await nlp.match("run silently and search google for SonarBot GitHub and open the first result")

    assert match is not None
    assert match.recipe_name == "google_search_open"
    assert match.details["execution_mode_override"] == "headless"


@pytest.mark.asyncio
async def test_browser_workflow_nlp_does_not_resume_on_bare_yes_without_pending_followup(app_config) -> None:
    runtime = FakeBrowserRuntime()
    nlp = BrowserWorkflowNLP(app_config, FakeToolRegistry(runtime))

    match = await nlp.match("yes", previous_state={"site_name": "leetcode", "query": "arrays"})

    assert match is None


@pytest.mark.asyncio
async def test_browser_workflow_engine_runs_google_search_open(app_config) -> None:
    runtime = FakeBrowserRuntime()
    runtime.results = [
        {"title": "Other result", "href": "https://example.com/other"},
        {"title": "SonarBot GitHub", "href": "https://github.com/example/sonarbot"},
    ]
    engine = BrowserWorkflowEngine(app_config, FakeToolRegistry(runtime))

    result = await engine.maybe_run(
        "search google for SonarBot GitHub and open the first result",
        user_id="default",
        session_key="webchat_main",
        channel="webchat",
    )

    assert result is not None
    assert result.status == "completed"
    assert "Opened Google" in result.response_text
    assert runtime.page.url == "https://example.com/other"


@pytest.mark.asyncio
async def test_browser_workflow_engine_opens_amazon_search_results_directly(app_config) -> None:
    runtime = FakeBrowserRuntime()
    runtime.results = [
        {"title": "Running shoes", "href": "https://www.amazon.com/dp/example"},
    ]
    engine = BrowserWorkflowEngine(app_config, FakeToolRegistry(runtime))

    result = await engine.run_match(
        BrowserWorkflowMatch(
            recipe_name="amazon_search_buy",
            confidence=0.96,
            site_name="amazon",
            query="shoes",
            action="search",
            details={"task_state": {}},
        ),
        user_id="default",
        session_key="webchat_main",
        channel="webchat",
        previous_state={},
    )

    assert result is not None
    assert result.status == "blocked"
    assert any("amazon.com" in url for url in runtime.visited_urls)
    assert runtime.page.last_filled == "shoes"
    assert "Enter" in runtime.pressed_keys


@pytest.mark.asyncio
async def test_browser_workflow_engine_prefers_headed_mode_for_amazon_search(app_config) -> None:
    runtime = FakeBrowserRuntime()
    engine = BrowserWorkflowEngine(app_config, FakeToolRegistry(runtime))

    match = BrowserWorkflowMatch(
        recipe_name="amazon_search_buy",
        confidence=0.96,
        site_name="amazon",
        query="watch",
        action="search",
        details={"task_state": {}},
    )

    assert engine._desired_mode_for_match(match) == "headed"


@pytest.mark.asyncio
async def test_browser_workflow_nlp_matches_natural_amazon_request(app_config) -> None:
    runtime = FakeBrowserRuntime()
    nlp = BrowserWorkflowNLP(app_config, FakeToolRegistry(runtime))

    match = await nlp.match("on amazon can you search for shoes")

    assert match is not None
    assert match.recipe_name == "amazon_search_buy"
    assert match.site_name == "amazon"
    assert match.query == "shoes"


@pytest.mark.asyncio
async def test_browser_workflow_nlp_cleans_messy_google_search_request(app_config) -> None:
    runtime = FakeBrowserRuntime()
    nlp = BrowserWorkflowNLP(app_config, FakeToolRegistry(runtime))

    match = await nlp.match("do it google search then you will see the github appears")

    assert match is not None
    assert match.recipe_name == "google_search_open"
    assert match.site_name == "google"
    assert match.query == "github"


@pytest.mark.asyncio
async def test_browser_workflow_nlp_turns_github_lookup_into_google_search(app_config) -> None:
    runtime = FakeBrowserRuntime()
    nlp = BrowserWorkflowNLP(app_config, FakeToolRegistry(runtime))

    match = await nlp.match("find the github of openclaw")

    assert match is not None
    assert match.recipe_name == "google_search_open"
    assert match.site_name == "google"
    assert match.query == "openclaw github"


@pytest.mark.asyncio
async def test_browser_workflow_nlp_does_not_inherit_active_amazon_for_generic_search(app_config) -> None:
    runtime = FakeBrowserRuntime()
    runtime.page.url = "https://www.amazon.com"
    runtime._set_active_tab("tab-1", "https://www.amazon.com", "headed", title="Amazon")
    nlp = BrowserWorkflowNLP(app_config, FakeToolRegistry(runtime))

    match = await nlp.match(
        "search for openclaw",
        runtime_state=runtime.current_state(),
        previous_state={},
    )

    assert match is not None
    assert match.recipe_name == "google_search_open"
    assert match.site_name == "google"
    assert match.query == "openclaw"


@pytest.mark.asyncio
async def test_browser_workflow_engine_keeps_headed_mode_for_same_site_followup(app_config) -> None:
    runtime = FakeBrowserRuntime()
    runtime.results = [
        {"title": "Trapped On An Island Until I Build A Boat", "href": "https://www.youtube.com/watch?v=abc123"},
    ]
    runtime.page.url = "https://www.youtube.com"
    runtime.current_headless = False
    runtime.current_mode_value = "headed"
    runtime._set_active_tab("tab-1", "https://www.youtube.com", "headed", title="YouTube")
    engine = BrowserWorkflowEngine(app_config, FakeToolRegistry(runtime))

    result = await engine.maybe_run(
        "now search mr beast video and play it",
        user_id="default",
        session_key="telegram:123",
        channel="telegram",
        previous_state={
            "active_task": {
                "recipe_name": "site_open_exact_url_or_path",
                "site_name": "youtube",
                "target_url": "https://www.youtube.com",
                "execution_mode": "headed",
                "awaiting_followup": "site_action",
            }
        },
    )

    assert result is not None
    assert result.status == "completed"
    assert runtime.requested_headless[-1] is False
    assert runtime.page.url == "https://www.youtube.com/watch?v=abc123"


@pytest.mark.asyncio
async def test_browser_workflow_engine_blocks_login_favoring_site_without_profile(app_config) -> None:
    runtime = FakeBrowserRuntime()
    engine = BrowserWorkflowEngine(app_config, FakeToolRegistry(runtime))

    result = await engine.maybe_run(
        "open leetcode and search arrays problems",
        user_id="default",
        session_key="webchat_main",
        channel="webchat",
    )

    assert result is not None
    assert result.status == "blocked"
    assert "needs login first" in result.response_text.lower()
    assert "visible browser window" in result.response_text.lower()
    assert result.state_update["browser_task_state"]["active_task"]["blocked_reason"] == "login_required"
    assert result.state_update["browser_task_state"]["pending_login"]["site_name"] == "leetcode"
    assert runtime.current_headless is False
    assert "accounts/login" in runtime.page.url


@pytest.mark.asyncio
async def test_browser_workflow_engine_uses_headless_for_low_risk_search(app_config) -> None:
    runtime = FakeBrowserRuntime()
    runtime.results = [
        {"title": "SonarBot GitHub", "href": "https://github.com/example/sonarbot"},
    ]
    engine = BrowserWorkflowEngine(app_config, FakeToolRegistry(runtime))

    result = await engine.maybe_run(
        "search google for SonarBot GitHub and open the first result",
        user_id="default",
        session_key="webchat_main",
        channel="webchat",
    )

    assert result is not None
    assert result.status == "completed"
    assert runtime.requested_headless[0] is True


@pytest.mark.asyncio
async def test_browser_workflow_engine_respects_headed_override(app_config) -> None:
    runtime = FakeBrowserRuntime()
    engine = BrowserWorkflowEngine(app_config, FakeToolRegistry(runtime))

    result = await engine.maybe_run(
        "show me what you're doing and open github",
        user_id="default",
        session_key="webchat_main",
        channel="webchat",
    )

    assert result is not None
    assert result.status == "completed"
    assert runtime.requested_headless[0] is False


@pytest.mark.asyncio
async def test_browser_workflow_engine_can_open_github_issue_composer(app_config) -> None:
    runtime = FakeBrowserRuntime()
    engine = BrowserWorkflowEngine(app_config, FakeToolRegistry(runtime))

    preview = await engine.maybe_run(
        "open issue on the SonarBot repo",
        user_id="default",
        session_key="telegram:123",
        channel="telegram",
    )

    assert preview is not None
    assert preview.status == "needs_followup"
    assert "Plan preview" in preview.response_text

    confirmed = await engine.run_match(
        BrowserWorkflowMatch(
            recipe_name="browser_continue_last_task",
            confidence=0.99,
            site_name="github",
            action="confirm",
            details={"task_state": preview.state_update["browser_task_state"]},
        ),
        user_id="default",
        session_key="telegram:123",
        channel="telegram",
        previous_state=preview.state_update["browser_task_state"],
    )

    assert confirmed.status == "blocked"
    assert "issue composer" in confirmed.response_text.lower()
    assert confirmed.state_update["browser_task_state"]["pending_confirmation"]["action_type"] == "submit"
    assert runtime.current_mode_value == "headed"


@pytest.mark.asyncio
async def test_browser_workflow_engine_inspects_github_repo_via_api(app_config) -> None:
    runtime = FakeBrowserRuntime()
    runtime.page.url = "https://github.com/Rishiraj-Yadav/SonarBot"
    engine = BrowserWorkflowEngine(app_config, FakeToolRegistry(runtime))

    result = await engine.maybe_run(
        "tell me about this repo",
        user_id="default",
        session_key="telegram:123",
        channel="telegram",
    )

    assert result is not None
    assert result.status == "completed"
    assert "Rishiraj-Yadav/SonarBot" in result.response_text
    assert "Open pull requests: 0" in result.response_text


@pytest.mark.asyncio
async def test_browser_workflow_engine_returns_disambiguation_for_mid_confidence_llm_match(app_config) -> None:
    runtime = FakeBrowserRuntime()
    engine = BrowserWorkflowEngine(
        app_config,
        FakeToolRegistry(
            runtime,
            llm_task_response={
                "recipe_name": "page_read_summarize",
                "confidence": 0.7,
                "site_name": "example.com",
                "query": "example.com/docs",
                "action": "summarize",
            },
        ),
    )

    result = await engine.maybe_run(
        "summarize that website",
        user_id="default",
        session_key="webchat_main",
        channel="webchat",
    )

    assert result is not None
    assert result.status == "needs_followup"
    assert "Did you mean" in result.response_text
    assert result.state_update["browser_task_state"]["pending_disambiguation"]["recipe_name"] == "page_read_summarize"


@pytest.mark.asyncio
async def test_browser_workflow_confirmed_disambiguation_executes_instead_of_looping(app_config) -> None:
    runtime = FakeBrowserRuntime()
    runtime.results = [{"title": "News result", "href": "https://news.example.com/usa-vs-iran"}]
    engine = BrowserWorkflowEngine(app_config, FakeToolRegistry(runtime))
    previous_state = {
        "pending_disambiguation": {
            "recipe_name": "google_search_open",
            "site_name": "google",
            "query": "news about the usa vs iran",
            "action": "open_result",
            "open_first_result": False,
            "details": {"needs_disambiguation": True, "target_url": "https://google.com"},
        }
    }

    result = await engine.maybe_run(
        "yes",
        user_id="default",
        session_key="telegram:123",
        channel="telegram",
        previous_state=previous_state,
    )

    assert result is not None
    assert result.status == "completed"
    assert "Opened Google" in result.response_text
    assert runtime.page.url == "https://news.example.com/usa-vs-iran"


@pytest.mark.asyncio
async def test_browser_workflow_nlp_lowers_threshold_for_short_followup_with_active_task(app_config) -> None:
    runtime = FakeBrowserRuntime()
    engine = BrowserWorkflowEngine(
        app_config,
        FakeToolRegistry(
            runtime,
            llm_task_response={
                "recipe_name": "google_search_open",
                "confidence": 0.65,
                "site_name": "google",
                "query": "SonarBot GitHub",
                "action": "open_result",
                "open_first_result": True,
            },
        ),
    )

    match = await engine.match_message(
        "that one",
        previous_state={
            "active_task": {
                "recipe_name": "google_search_open",
                "site_name": "google",
                "query": "SonarBot GitHub",
                "awaiting_followup": "site_action",
            }
        },
        force=True,
    )

    assert match is not None
    assert match.recipe_name == "google_search_open"
    assert match.action != "disambiguate"


@pytest.mark.asyncio
async def test_browser_workflow_nlp_treats_short_google_context_phrase_as_search(app_config) -> None:
    runtime = FakeBrowserRuntime()
    nlp = BrowserWorkflowNLP(app_config, FakeToolRegistry(runtime))

    match = await nlp.match(
        "openclaw",
        previous_state={
            "active_task": {
                "recipe_name": "site_open_and_search",
                "site_name": "google",
                "awaiting_followup": "site_action",
            }
        },
    )

    assert match is not None
    assert match.recipe_name == "google_search_open"
    assert match.query == "openclaw"


@pytest.mark.asyncio
async def test_browser_workflow_engine_streams_live_chunks_for_steps(app_config) -> None:
    runtime = FakeBrowserRuntime()
    runtime.results = [{"title": "SonarBot GitHub", "href": "https://github.com/example/sonarbot"}]
    chunk_events: list[tuple[str, str, dict[str, object]]] = []

    async def chunk_emitter(connection_id: str, event_name: str, payload: dict[str, object]) -> None:
        chunk_events.append((connection_id, event_name, payload))

    engine = BrowserWorkflowEngine(app_config, FakeToolRegistry(runtime), chunk_emitter=chunk_emitter)

    result = await engine.run_match(
        BrowserWorkflowMatch(
            recipe_name="google_search_open",
            confidence=0.95,
            site_name="google",
            query="SonarBot GitHub",
            action="open_result",
            open_first_result=True,
            details={},
        ),
        user_id="default",
        session_key="webchat_main",
        channel="webchat",
        connection_id="route-1",
    )

    assert result.status == "completed"
    assert any(event_name == "agent.chunk" for _, event_name, _ in chunk_events)
    assert any(payload["text"].startswith("🔄 ") for _, _, payload in chunk_events)


@pytest.mark.asyncio
async def test_browser_workflow_email_compose_requires_plan_preview_before_execution(app_config) -> None:
    runtime = FakeBrowserRuntime()
    engine = BrowserWorkflowEngine(app_config, FakeToolRegistry(runtime))

    preview = await engine.run_match(
        BrowserWorkflowMatch(
            recipe_name="email_compose_send",
            confidence=0.95,
            site_name="gmail",
            query="Quarterly update",
            details={"to": "test@example.com", "subject": "Quarterly update", "body": "Hello there"},
        ),
        user_id="default",
        session_key="telegram:123",
        channel="telegram",
        previous_state={},
    )

    assert preview.status == "needs_followup"
    assert "Plan preview" in preview.response_text

    confirmed = await engine.run_match(
        BrowserWorkflowMatch(
            recipe_name="browser_continue_last_task",
            confidence=0.99,
            site_name="gmail",
            action="confirm",
            details={"task_state": preview.state_update["browser_task_state"]},
        ),
        user_id="default",
        session_key="telegram:123",
        channel="telegram",
        previous_state=preview.state_update["browser_task_state"],
    )

    assert confirmed.status == "blocked"
    assert runtime.current_mode_value == "headed"
    assert confirmed.state_update["browser_task_state"]["pending_confirmation"]["action_type"] == "send"


@pytest.mark.asyncio
async def test_multi_tab_research_asks_for_explicit_urls(app_config) -> None:
    runtime = FakeBrowserRuntime()
    engine = BrowserWorkflowEngine(app_config, FakeToolRegistry(runtime))

    result = await engine.run_match(
        BrowserWorkflowMatch(
            recipe_name="multi_tab_research",
            confidence=0.9,
            query="compare the top options",
            details={},
        ),
        user_id="default",
        session_key="webchat_main",
        channel="webchat",
        previous_state={},
    )

    assert result.status == "needs_followup"
    assert "Tell me the URLs" in result.response_text


@pytest.mark.asyncio
async def test_browser_workflow_continue_preserves_state_when_current_site_mismatches(app_config) -> None:
    runtime = FakeBrowserRuntime()
    runtime.page.url = "https://erp.vcet.edu.in"
    runtime.tabs = [{"tab_id": "tab-1", "url": "https://erp.vcet.edu.in", "title": "ERP", "mode": "headed", "active": True}]
    engine = BrowserWorkflowEngine(app_config, FakeToolRegistry(runtime))
    previous_state = {
        "recipe_name": "site_open_and_search",
        "site_name": "leetcode",
        "query": "arrays problems",
        "blocked_reason": "login_required",
        "awaiting_followup": "continue",
    }

    result = await engine.run_match(
        BrowserWorkflowMatch(
            recipe_name="browser_continue_last_task",
            confidence=0.99,
            site_name="leetcode",
            details={"previous_state": previous_state},
        ),
        user_id="default",
        session_key="webchat_main",
        channel="webchat",
        previous_state=previous_state,
    )

    assert result.status == "blocked"
    assert result.clear_state is False
    assert "waiting for the leetcode login" in result.response_text.lower()
    assert result.state_update["browser_task_state"]["active_task"]["site_name"] == "leetcode"
    assert result.state_update["browser_task_state"]["pending_login"]["site_name"] == "leetcode"


@pytest.mark.asyncio
async def test_browser_workflow_continue_switches_back_to_matching_site_for_pending_login(app_config) -> None:
    runtime = FakeBrowserRuntime()
    runtime.current_tab_id = "tab-1"
    runtime.tabs = [
        {"tab_id": "tab-1", "url": "https://leetcode.com/problems", "title": "LeetCode", "mode": "headless", "active": True},
        {"tab_id": "tab-4", "url": "https://erp.vcet.edu.in/login.htm;jsessionid=abc?token=123", "title": "ERP Login", "mode": "headed", "active": False},
    ]
    runtime.page.url = "https://leetcode.com/problems"
    engine = BrowserWorkflowEngine(app_config, FakeToolRegistry(runtime))
    previous_state = {
        "active_task": {
            "recipe_name": "site_open_exact_url_or_path",
            "site_name": "erp.vcet.edu.in",
            "active_site": "erp.vcet.edu.in",
            "target_url": "https://erp.vcet.edu.in/login.htm",
            "active_url": "https://erp.vcet.edu.in/login.htm",
            "execution_mode": "headed",
            "blocked_reason": "login_required",
            "awaiting_followup": "continue",
        },
        "pending_login": {
            "site_name": "erp.vcet.edu.in",
            "target_url": "https://erp.vcet.edu.in/login.htm",
            "execution_mode": "headed",
        },
    }

    result = await engine.run_match(
        BrowserWorkflowMatch(
            recipe_name="browser_continue_last_task",
            confidence=0.99,
            site_name="erp.vcet.edu.in",
            details={"task_state": previous_state},
        ),
        user_id="default",
        session_key="telegram:123",
        channel="telegram",
        previous_state=previous_state,
    )

    assert result.status == "blocked"
    assert "login still looks incomplete" in result.response_text.lower()
    assert runtime.current_tab_id == "tab-4"
    assert runtime.current_mode_value == "headed"


def test_browser_workflow_engine_treats_site_aliases_as_same_site(app_config) -> None:
    runtime = FakeBrowserRuntime()
    engine = BrowserWorkflowEngine(app_config, FakeToolRegistry(runtime))

    assert engine._sites_match("leetcode.com", "leetcode") is True
    assert engine._sites_match("https://leetcode.com/problems/", "leetcode") is True


@pytest.mark.asyncio
async def test_browser_workflow_continue_can_confirm_pending_action(app_config) -> None:
    runtime = FakeBrowserRuntime()
    runtime.pending_action = {
        "action_type": "submit",
        "selector": "button[type=submit]",
        "target": "https://example.com/checkout",
        "awaiting_followup": "confirmation",
        "tab_id": "tab-1",
    }
    runtime.page.url = "https://example.com/checkout"
    engine = BrowserWorkflowEngine(app_config, FakeToolRegistry(runtime))
    previous_state = {
        "recipe_name": "site_open_and_search",
        "site_name": "example.com",
        "query": "",
        "awaiting_followup": "confirmation",
    }

    result = await engine.run_match(
        BrowserWorkflowMatch(
            recipe_name="browser_continue_last_task",
            confidence=0.99,
            site_name="example.com",
            action="confirm",
            details={"previous_state": previous_state},
        ),
        user_id="default",
        session_key="webchat_main",
        channel="webchat",
        previous_state=previous_state,
    )

    assert result.status == "completed"
    assert "confirmed the pending browser submit" in result.response_text.lower()
    assert runtime.pending_action is None


@pytest.mark.asyncio
async def test_router_uses_browser_workflow_before_generic_agent(app_config) -> None:
    runtime = FakeBrowserRuntime()
    runtime.results = [{"title": "Trapped On An Island Until I Build A Boat", "href": "https://youtube.com/watch?v=abc"}]
    tool_registry = FakeToolRegistry(runtime)
    engine = BrowserWorkflowEngine(app_config, tool_registry)
    session_manager = DummySessionManager()
    agent_loop = DummyAgentLoop()
    router = GatewayRouter(
        config=app_config,
        agent_loop=agent_loop,
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
        connection_id="conn-1",
        request_id="req-1",
        session_key="webchat_main",
        message="open youtube and play Trapped On An Island Until I Build A Boat",
        metadata={"trace_id": "trace-1", "user_id": "default", "channel": "webchat"},
    )

    assert response.ok is True
    assert response.payload["queued"] is False
    assert "Opened YouTube" in response.payload["command_response"]
    assert agent_loop.enqueued == []
    assert session_manager.session.metadata["browser_task_state"]["active_task"]["site_name"] == "youtube"


@pytest.mark.asyncio
async def test_router_exposes_browser_workflow_list_via_slash_command(app_config) -> None:
    runtime = FakeBrowserRuntime()
    tool_registry = FakeToolRegistry(runtime)
    engine = BrowserWorkflowEngine(app_config, tool_registry)
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
        browser_workflow_engine=engine,
    )

    response = await router.route_user_message(
        connection_id="conn-2",
        request_id="req-2",
        session_key="telegram:123",
        message="/browser workflows",
        metadata={"trace_id": "trace-2", "user_id": "default"},
    )

    assert response.ok is True
    assert "Browser autonomous workflows:" in response.payload["command_response"]
    assert "youtube_search_play" in response.payload["command_response"]


@pytest.mark.asyncio
async def test_router_browser_profile_alias_and_state_redaction(app_config) -> None:
    runtime = FakeBrowserRuntime()
    runtime.tabs = [
        {
            "tab_id": "tab-4",
            "url": "https://erp.vcet.edu.in/login.htm;jsessionid=ABC123?token=secret",
            "title": "Welcome to VCET",
            "mode": "headed",
            "active": True,
        }
    ]
    runtime.current_tab_id = "tab-4"
    runtime.current_mode_value = "headed"
    runtime.current_headless = False
    runtime.page.url = "https://erp.vcet.edu.in/login.htm;jsessionid=ABC123?token=secret"
    runtime.sessions = [
        {"site_name": "leetcode", "profile_name": "default", "status": "active"},
    ]
    tool_registry = FakeToolRegistry(runtime)
    engine = BrowserWorkflowEngine(app_config, tool_registry)
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
        browser_workflow_engine=engine,
    )

    profile_response = await router.route_user_message(
        connection_id="conn-profile",
        request_id="req-profile",
        session_key="telegram:123",
        message="/browser profile",
        metadata={"trace_id": "trace-profile", "user_id": "default"},
    )
    state_response = await router.route_user_message(
        connection_id="conn-state",
        request_id="req-state",
        session_key="telegram:123",
        message="/browser state",
        metadata={"trace_id": "trace-state", "user_id": "default"},
    )

    assert profile_response.ok is True
    assert "Saved browser profiles:" in profile_response.payload["command_response"]
    assert ";jsessionid=" not in state_response.payload["command_response"]
    assert "?token=" not in state_response.payload["command_response"]


@pytest.mark.asyncio
async def test_router_stores_standalone_browser_mode_override(app_config) -> None:
    runtime = FakeBrowserRuntime()
    tool_registry = FakeToolRegistry(runtime)
    engine = BrowserWorkflowEngine(app_config, tool_registry)
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
        browser_workflow_engine=engine,
    )

    response = await router.route_user_message(
        connection_id="conn-standalone",
        request_id="req-standalone",
        session_key="telegram:123",
        message="show me what you're doing",
        metadata={"trace_id": "trace-standalone", "user_id": "default", "channel": "telegram"},
    )

    assert response.ok is True
    assert "visible window" in response.payload["command_response"].lower()
    assert session_manager.session.metadata["browser_task_state"]["next_task_mode_override"] == "headed"
