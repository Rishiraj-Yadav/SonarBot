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

    state = runtime.current_state()
    assert state["active_profile"]["profile_name"] == "work"
    assert state["tabs"][0]["title"] == "Example tab"
    assert runtime.list_logs(limit=1)[0]["message"] == "loaded"
    assert runtime.list_downloads(limit=1)[0]["filename"] == "report.csv"
