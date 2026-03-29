from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from assistant.tools.browser_runtime import BrowserTabState, profile_key_for
from assistant.tools.browser_tool import build_browser_tools


def _tool_by_name(tools, name: str):
    for tool in tools:
        if tool.name == name:
            return tool
    raise AssertionError(f"Missing tool '{name}'.")


def test_browser_tools_include_v2_surface(app_config) -> None:
    tools, _runtime = build_browser_tools(app_config)
    names = {tool.name for tool in tools}
    assert {
        "browser_navigate",
        "browser_click",
        "browser_type",
        "browser_screenshot",
        "browser_login",
        "browser_sessions_list",
        "browser_tabs_list",
        "browser_tab_open",
        "browser_tab_switch",
        "browser_tab_close",
        "browser_upload",
        "browser_downloads_list",
        "browser_logs",
        "browser_extract_table",
        "browser_fill_form",
    }.issubset(names)


@pytest.mark.asyncio
async def test_browser_sessions_list_returns_named_profiles_with_status(app_config) -> None:
    tools, runtime = build_browser_tools(app_config)
    runtime.session_index_path.write_text(
        json.dumps(
            {
                profile_key_for("github.com", "personal"): {
                    "site_name": "github.com",
                    "profile_name": "personal",
                    "domain": "github.com",
                    "storage_path": str(runtime.sessions_dir / "github-personal.json"),
                    "status": "active",
                    "last_used_at": "2026-03-24T09:00:00+00:00",
                },
                profile_key_for("github.com", "work"): {
                    "site_name": "github.com",
                    "profile_name": "work",
                    "domain": "github.com",
                    "storage_path": str(runtime.sessions_dir / "github-work.json"),
                    "status": "stale",
                    "last_used_at": "2026-03-24T08:00:00+00:00",
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    result = await _tool_by_name(tools, "browser_sessions_list").handler({})
    assert result["sessions"][0]["profile_name"] == "personal"
    assert result["sessions"][1]["profile_name"] == "work"
    assert result["sessions"][1]["status"] == "stale"


def test_browser_runtime_current_state_and_lists(app_config) -> None:
    _tools, runtime = build_browser_tools(app_config)
    profile_key = profile_key_for("example.com", "work")
    runtime.session_index_path.write_text(
        json.dumps(
            {
                profile_key: {
                    "profile_key": profile_key,
                    "site_name": "example.com",
                    "profile_name": "work",
                    "domain": "example.com",
                    "storage_path": str(runtime.sessions_dir / "example-work.json"),
                    "status": "active",
                    "last_used_at": "2026-03-24T09:00:00+00:00",
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    runtime._activate_mode("headed")
    runtime.current_profile_key = profile_key
    runtime.current_tab_id = "tab-1"
    runtime.current_headless = False
    runtime._tabs["tab-1"] = BrowserTabState(
        tab_id="tab-1",
        page=None,
        created_at=datetime.now(timezone.utc).isoformat(),
        title="Example tab",
        url="https://example.com/work",
    )
    runtime._recent_logs.append(
        {
            "timestamp": "2026-03-24T09:05:00+00:00",
            "kind": "console",
            "level": "log",
            "message": "loaded",
            "tab_id": "tab-1",
            "url": "https://example.com/work",
            "profile_key": profile_key,
        }
    )
    runtime._recent_downloads.append(
        {
            "path": str(runtime.downloads_dir / "example.com" / "work" / "report.csv"),
            "filename": "report.csv",
            "profile_key": profile_key,
            "created_at": "2026-03-24T09:06:00+00:00",
            "size": 128,
        }
    )
    runtime._pending_protected_action = {
        "action_type": "submit",
        "selector": "button[type=submit]",
        "awaiting_followup": "confirmation",
    }

    state = runtime.current_state()
    assert state["active_profile"]["profile_name"] == "work"
    assert state["tabs"][0]["title"] == "Example tab"
    assert state["current_mode"] == "headed"
    assert state["pending_protected_action"]["action_type"] == "submit"
    assert runtime.list_logs(limit=1)[0]["message"] == "loaded"
    assert runtime.list_downloads(limit=1)[0]["filename"] == "report.csv"


def test_browser_runtime_restores_persistent_pending_challenges(app_config) -> None:
    _tools, runtime = build_browser_tools(app_config)
    runtime._update_pending_runtime_state(
        _pending_otp={"site_name": "irctc", "tab_id": "tab-1", "selector": "input[name=otp]"},
        _pending_captcha={"site_name": "google", "tab_id": "tab-2", "selector": "input[name=captcha]"},
    )

    _tools_2, restored = build_browser_tools(app_config)

    assert restored.pending_otp()["site_name"] == "irctc"
    assert restored.pending_captcha()["site_name"] == "google"
    state = restored.current_state()
    assert state["pending_otp"]["selector"] == "input[name=otp]"
    assert state["pending_captcha"]["selector"] == "input[name=captcha]"


def test_browser_runtime_hides_mismatched_active_profile_and_dedupes_tabs(app_config) -> None:
    _tools, runtime = build_browser_tools(app_config)
    profile_key = profile_key_for("leetcode.com", "default")
    runtime.session_index_path.write_text(
        json.dumps(
            {
                profile_key: {
                    "profile_key": profile_key,
                    "site_name": "leetcode.com",
                    "profile_name": "default",
                    "domain": "leetcode.com",
                    "storage_path": str(runtime.sessions_dir / "leetcode-default.json"),
                    "status": "active",
                    "last_used_at": "2026-03-24T09:00:00+00:00",
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    runtime._activate_mode("headed")
    runtime.current_profile_key = profile_key
    runtime.current_tab_id = "tab-4"
    runtime._tabs["tab-4"] = BrowserTabState(
        tab_id="tab-4",
        page=None,
        created_at=datetime.now(timezone.utc).isoformat(),
        title="ERP headed",
        url="https://erp.vcet.edu.in/login.htm;jsessionid=ABC123?token=secret",
    )
    runtime._mode_states["headless"].tabs["tab-1"] = BrowserTabState(
        tab_id="tab-1",
        page=None,
        created_at=datetime.now(timezone.utc).isoformat(),
        title="ERP headless",
        url="https://erp.vcet.edu.in/login.htm;jsessionid=DIFFERENT?other=1",
    )
    runtime._mode_states["headless"].current_tab_id = "tab-1"

    state = runtime.current_state()

    assert state["active_profile"] is None
    assert len(state["tabs"]) == 1
    assert state["tabs"][0]["tab_id"] == "tab-4"


def test_browser_runtime_current_state_prefers_mode_with_real_active_tab(app_config) -> None:
    _tools, runtime = build_browser_tools(app_config)
    runtime._activate_mode("headed")
    runtime.current_tab_id = None
    runtime.current_headless = False
    runtime._mode_states["headless"].current_tab_id = "tab-2"
    runtime._mode_states["headless"].tabs["tab-2"] = BrowserTabState(
        tab_id="tab-2",
        page=None,
        created_at=datetime.now(timezone.utc).isoformat(),
        title="LeetCode",
        url="https://leetcode.com/problemset/",
    )

    state = runtime.current_state()

    assert state["current_mode"] == "headless"
    assert state["current_tab_id"] == "tab-2"
    assert state["active_tab"]["title"] == "LeetCode"


def test_browser_runtime_matches_short_site_name_against_full_host(app_config) -> None:
    _tools, runtime = build_browser_tools(app_config)
    runtime._activate_mode("headed")
    runtime.current_tab_id = "tab-7"
    runtime._tabs["tab-7"] = BrowserTabState(
        tab_id="tab-7",
        page=None,
        created_at=datetime.now(timezone.utc).isoformat(),
        title="YouTube",
        url="https://www.youtube.com/watch?v=abc123",
    )

    match = runtime.find_matching_tab(site_name="youtube")

    assert match is not None
    assert match["tab_id"] == "tab-7"


def test_browser_runtime_filters_noisy_logs_and_redacts_urls(app_config) -> None:
    _tools, runtime = build_browser_tools(app_config)
    runtime._recent_logs.extend(
        [
            {
                "timestamp": "2026-03-24T09:05:00+00:00",
                "kind": "console",
                "level": "log",
                "message": "undefined",
                "tab_id": "tab-1",
                "url": "https://erp.vcet.edu.in/login.htm;jsessionid=ABC123?token=secret",
            },
            {
                "timestamp": "2026-03-24T09:06:00+00:00",
                "kind": "network",
                "level": "error",
                "message": "Request failed: POST https://www.google-analytics.com/g/collect?v=2",
                "tab_id": "tab-1",
                "url": "https://www.google-analytics.com/g/collect?v=2",
            },
            {
                "timestamp": "2026-03-24T09:07:00+00:00",
                "kind": "pageerror",
                "level": "error",
                "message": "Unexpected token at https://erp.vcet.edu.in/login.htm;jsessionid=ABC123?token=secret",
                "tab_id": "tab-1",
                "url": "https://erp.vcet.edu.in/login.htm;jsessionid=ABC123?token=secret",
            },
        ]
    )

    logs = runtime.list_logs(limit=5)

    assert len(logs) == 1
    assert ";jsessionid=" not in logs[0]["message"]
    assert "?token=" not in logs[0]["message"]
    assert ";jsessionid=" not in logs[0]["url"]


class _WaitPage:
    def __init__(self, *, fail_on: str | None = None) -> None:
        self.fail_on = fail_on
        self.calls: list[tuple[str, int]] = []
        self.timeouts: list[int] = []

    async def wait_for_load_state(self, state: str, timeout: int) -> None:
        self.calls.append((state, timeout))
        if self.fail_on == state:
            raise RuntimeError(f"{state} timeout")

    async def wait_for_timeout(self, timeout: int) -> None:
        self.timeouts.append(timeout)


@pytest.mark.asyncio
async def test_browser_runtime_post_action_wait_tolerates_networkidle_timeout(app_config) -> None:
    _tools, runtime = build_browser_tools(app_config)
    page = _WaitPage(fail_on="networkidle")

    await runtime.post_action_wait(page, "networkidle", 30)

    assert page.calls == [("networkidle", 30000)]
    assert page.timeouts == [400]


class _BlockingPage:
    def __init__(self, url: str, html: str) -> None:
        self.url = url
        self._html = html

    async def content(self) -> str:
        return self._html


@pytest.mark.asyncio
async def test_browser_runtime_does_not_treat_plain_sign_in_button_as_login_block(app_config) -> None:
    _tools, runtime = build_browser_tools(app_config)
    page = _BlockingPage(
        "https://www.youtube.com",
        "<html><body><button>Sign in</button><input type='search' placeholder='Search'></body></html>",
    )

    result = await runtime.detect_blocking_state(page)

    assert result is None


@pytest.mark.asyncio
async def test_browser_runtime_detects_real_login_page_from_password_form(app_config) -> None:
    _tools, runtime = build_browser_tools(app_config)
    page = _BlockingPage(
        "https://leetcode.com/accounts/login/",
        "<html><body><h1>Sign in</h1><input type='email'><input type='password'></body></html>",
    )

    result = await runtime.detect_blocking_state(page)

    assert result is not None
    assert result["kind"] == "login"


@pytest.mark.asyncio
async def test_browser_runtime_detects_captcha_from_iframe_markers_without_visible_text(app_config) -> None:
    _tools, runtime = build_browser_tools(app_config)
    page = _BlockingPage(
        "https://www.google.com/sorry/index",
        """
        <html><body>
        <iframe src="https://www.google.com/recaptcha/api2/anchor?k=test-site-key"></iframe>
        <div id="rc-anchor-container"></div>
        </body></html>
        """,
    )

    result = await runtime.detect_blocking_state(page)

    assert result is not None
    assert result["kind"] == "captcha"
    assert result["sitekey"] == "test-site-key"


class _FormPage:
    url = "https://example.com/form"

    async def evaluate(self, _script: str):
        return {
            "url": self.url,
            "title": "Example Form",
            "form_count": 1,
            "fields": [
                {
                    "form_index": 0,
                    "tag": "input",
                    "type": "email",
                    "name": "email",
                    "id": "email",
                    "placeholder": "Email address",
                    "aria_label": "",
                    "label": "Email",
                    "required": True,
                    "disabled": False,
                    "visible": True,
                    "selector_hint": "#email",
                    "options": [],
                },
                {
                    "form_index": 0,
                    "tag": "select",
                    "type": "select",
                    "name": "class",
                    "id": "class",
                    "placeholder": "",
                    "aria_label": "",
                    "label": "Class",
                    "required": False,
                    "disabled": False,
                    "visible": True,
                    "selector_hint": "#class",
                    "options": [{"value": "economy", "label": "Economy"}],
                },
            ],
        }


@pytest.mark.asyncio
async def test_browser_runtime_inspect_form_returns_structured_schema(app_config) -> None:
    _tools, runtime = build_browser_tools(app_config)

    schema = await runtime.inspect_form(_FormPage())

    assert schema["form_count"] == 1
    assert schema["fields"][0]["label"] == "Email"
    assert schema["fields"][1]["options"][0]["value"] == "economy"


@pytest.mark.asyncio
async def test_browser_click_returns_review_required_for_protected_action(app_config, monkeypatch: pytest.MonkeyPatch) -> None:
    tools, runtime = build_browser_tools(app_config)
    click_tool = _tool_by_name(tools, "browser_click")

    async def fake_get_page(self, **_kwargs):
        return object()

    async def fake_prepare_protected_action(self, action_type: str, **kwargs):
        return {
            "action_type": action_type,
            "selector": kwargs.get("selector", ""),
            "awaiting_followup": "confirmation",
            "tab_id": "tab-1",
        }

    monkeypatch.setattr(type(runtime), "get_page", fake_get_page)
    monkeypatch.setattr(type(runtime), "prepare_protected_action", fake_prepare_protected_action)
    monkeypatch.setattr(type(runtime), "safe_action_requires_confirmation", lambda self, action_type, target=None: True)

    result = await click_tool.handler({"selector": "button[type=submit]", "action_type": "submit"})

    assert result["review_required"] is True
    assert result["pending_action"]["action_type"] == "submit"
