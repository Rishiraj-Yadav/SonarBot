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
