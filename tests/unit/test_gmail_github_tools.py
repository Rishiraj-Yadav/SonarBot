from __future__ import annotations

import base64
from typing import Any

import pytest

from assistant.tools.github_tool import build_github_tools
from assistant.tools.gmail_tool import build_gmail_tools


class FakeTokenManager:
    def __init__(self, tokens: dict[str, dict[str, Any]]) -> None:
        self.tokens = tokens

    async def get_token(self, provider: str, user_id: str = "default") -> dict[str, Any] | None:
        return self.tokens.get(provider)


class FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Any:
        return self.payload


class FakeAsyncClient:
    def __init__(self, handler) -> None:
        self.handler = handler

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def request(self, method: str, url: str, **kwargs):
        return self.handler(method, url, kwargs)


@pytest.mark.asyncio
async def test_gmail_tools_search_read_and_send(monkeypatch) -> None:
    def handler(method: str, url: str, kwargs: dict[str, Any]) -> FakeResponse:
        if url.endswith("/threads"):
            return FakeResponse({"threads": [{"id": "thread-1"}]})
        if url.endswith("/threads/thread-1"):
            params = kwargs.get("params", {})
            if params.get("format") == "metadata":
                return FakeResponse(
                    {
                        "historyId": "1",
                        "snippet": "Snippet text",
                        "messages": [
                            {
                                "payload": {
                                    "headers": [
                                        {"name": "Subject", "value": "Test Subject"},
                                        {"name": "From", "value": "sender@example.com"},
                                        {"name": "Date", "value": "Mon, 01 Jan 2026 10:00:00 +0000"},
                                    ]
                                }
                            }
                        ],
                    }
                )
            return FakeResponse(
                {
                    "messages": [
                        {
                            "id": "msg-1",
                            "threadId": "thread-1",
                            "snippet": "Hello there",
                            "payload": {
                                "headers": [
                                    {"name": "Subject", "value": "Test Subject"},
                                    {"name": "From", "value": "sender@example.com"},
                                    {"name": "To", "value": "user@example.com"},
                                    {"name": "Date", "value": "Mon, 01 Jan 2026 10:00:00 +0000"},
                                ],
                                "body": {"data": "SGVsbG8gdGhlcmU"},
                            },
                        }
                    ]
                }
            )
        if url.endswith("/messages/send"):
            return FakeResponse({"id": "sent-1", "threadId": "thread-2", "labelIds": ["SENT"]})
        raise AssertionError(f"Unexpected Gmail URL: {url}")

    monkeypatch.setattr("assistant.tools.gmail_tool.httpx.AsyncClient", lambda timeout=30.0: FakeAsyncClient(handler))

    token_manager = FakeTokenManager({"google": {"access_token": "google-token"}})
    tools = {tool.name: tool for tool in build_gmail_tools(token_manager)}

    search_result = await tools["gmail_search"].handler({"query": "in:inbox", "limit": 5})
    assert search_result["threads"][0]["subject"] == "Test Subject"

    latest_result = await tools["gmail_latest_email"].handler({})
    assert latest_result["found"] is True
    assert latest_result["subject"] == "Test Subject"
    assert latest_result["body"] == "Hello there"

    read_result = await tools["gmail_read_thread"].handler({"thread_id": "thread-1"})
    assert read_result["messages"][0]["body"] == "Hello there"

    send_result = await tools["gmail_send"].handler(
        {"to": "friend@example.com", "subject": "Hi", "body": "Checking in"}
    )
    assert send_result["id"] == "sent-1"

    assert "required" not in tools["gmail_search"].parameters


@pytest.mark.asyncio
async def test_gmail_tools_convert_html_body_to_readable_text(monkeypatch) -> None:
    html_email = "<!DOCTYPE html><html><head><title>Notice</title></head><body><h1>Maintenance</h1><p>Tonight at 11 PM</p></body></html>"
    encoded_html = base64.urlsafe_b64encode(html_email.encode("utf-8")).decode("utf-8").rstrip("=")

    def handler(method: str, url: str, kwargs: dict[str, Any]) -> FakeResponse:
        if url.endswith("/threads"):
            return FakeResponse({"threads": [{"id": "thread-html"}]})
        if url.endswith("/threads/thread-html"):
            params = kwargs.get("params", {})
            if params.get("format") == "metadata":
                return FakeResponse(
                    {
                        "historyId": "1",
                        "snippet": "Maintenance tonight",
                        "messages": [
                            {
                                "payload": {
                                    "headers": [
                                        {"name": "Subject", "value": "Maintenance notice"},
                                        {"name": "From", "value": "ops@example.com"},
                                        {"name": "Date", "value": "Mon, 01 Jan 2026 10:00:00 +0000"},
                                    ]
                                }
                            }
                        ],
                    }
                )
            return FakeResponse(
                {
                    "messages": [
                        {
                            "id": "msg-html",
                            "threadId": "thread-html",
                            "snippet": "Maintenance tonight",
                            "payload": {
                                "headers": [
                                    {"name": "Subject", "value": "Maintenance notice"},
                                    {"name": "From", "value": "ops@example.com"},
                                ],
                                "parts": [
                                    {
                                        "mimeType": "text/html",
                                        "body": {"data": encoded_html},
                                    }
                                ],
                            },
                        }
                    ]
                }
            )
        raise AssertionError(f"Unexpected Gmail URL: {url}")

    monkeypatch.setattr("assistant.tools.gmail_tool.httpx.AsyncClient", lambda timeout=30.0: FakeAsyncClient(handler))

    token_manager = FakeTokenManager({"google": {"access_token": "google-token"}})
    tools = {tool.name: tool for tool in build_gmail_tools(token_manager)}

    latest_result = await tools["gmail_latest_email"].handler({})

    assert latest_result["found"] is True
    assert "<html" not in latest_result["body"].lower()
    assert "Maintenance" in latest_result["body"]
    assert "Tonight at 11 PM" in latest_result["body"]


@pytest.mark.asyncio
async def test_github_tools_list_and_get_pull_request(monkeypatch) -> None:
    def handler(method: str, url: str, kwargs: dict[str, Any]) -> FakeResponse:
        if url.endswith("/user/repos"):
            return FakeResponse(
                [
                    {
                        "full_name": "octo/repo",
                        "private": False,
                        "default_branch": "main",
                        "description": "Example repo",
                        "html_url": "https://github.com/octo/repo",
                    }
                ]
            )
        if url.endswith("/repos/octo/repo"):
            return FakeResponse(
                {
                    "full_name": "octo/repo",
                    "description": "Example repo",
                    "html_url": "https://github.com/octo/repo",
                    "private": False,
                    "default_branch": "main",
                    "language": "Python",
                    "topics": ["automation", "ai"],
                    "open_issues_count": 3,
                    "stargazers_count": 42,
                    "forks_count": 7,
                    "watchers_count": 10,
                    "updated_at": "2026-01-02T00:00:00Z",
                    "pushed_at": "2026-01-03T00:00:00Z",
                }
            )
        if url.endswith("/repos/octo/repo/issues"):
            return FakeResponse(
                [
                    {
                        "number": 1,
                        "title": "Bug report",
                        "state": "open",
                        "user": {"login": "octocat"},
                        "labels": [{"name": "bug"}],
                        "html_url": "https://github.com/octo/repo/issues/1",
                    }
                ]
            )
        if url.endswith("/repos/octo/repo/branches"):
            return FakeResponse(
                [
                    {
                        "name": "main",
                        "protected": True,
                        "commit": {"sha": "sha-main"},
                    },
                    {
                        "name": "Nick",
                        "protected": False,
                        "commit": {"sha": "sha-nick"},
                    },
                ]
            )
        if url.endswith("/repos/octo/repo/pulls"):
            if method == "POST":
                payload = kwargs.get("json", {})
                return FakeResponse(
                    {
                        "number": 9,
                        "title": payload.get("title"),
                        "state": "open",
                        "draft": False,
                        "html_url": "https://github.com/octo/repo/pull/9",
                        "user": {"login": "octocat"},
                        "head": {"ref": payload.get("head")},
                        "base": {"ref": payload.get("base")},
                    }
                )
            return FakeResponse(
                [
                    {
                        "number": 7,
                        "title": "Add feature",
                        "state": "open",
                        "user": {"login": "octocat"},
                        "draft": False,
                        "html_url": "https://github.com/octo/repo/pull/7",
                        "head": {"ref": "feature"},
                        "base": {"ref": "main"},
                    }
                ]
            )
        if url.endswith("/repos/octo/repo/compare/main...Nick"):
            return FakeResponse(
                {
                    "status": "ahead",
                    "ahead_by": 2,
                    "behind_by": 0,
                    "total_commits": 2,
                    "html_url": "https://github.com/octo/repo/compare/main...Nick",
                    "commits": [
                        {
                            "sha": "cmp-1",
                            "commit": {"message": "Add compare support", "author": {"name": "octocat"}},
                        }
                    ],
                    "files": [{"filename": "router.py", "status": "modified", "changes": 12}],
                }
            )
        if url.endswith("/repos/octo/repo/commits"):
            return FakeResponse(
                [
                    {
                        "sha": "abc123",
                        "html_url": "https://github.com/octo/repo/commit/abc123",
                        "commit": {
                            "message": "Fix automation edge case\n\nExtra body",
                            "author": {"name": "octocat", "date": "2026-01-03T00:00:00Z"},
                        },
                    }
                ]
            )
        if url.endswith("/repos/octo/repo/pulls/7"):
            return FakeResponse(
                {
                    "title": "Add feature",
                    "body": "PR body",
                    "state": "open",
                    "draft": False,
                    "html_url": "https://github.com/octo/repo/pull/7",
                    "user": {"login": "octocat"},
                    "head": {"ref": "feature"},
                    "base": {"ref": "main"},
                }
            )
        if url.endswith("/repos/octo/repo/pulls/7/files"):
            return FakeResponse([{"filename": "app.py", "status": "modified", "additions": 5, "deletions": 1, "changes": 6, "patch": "@@"}])
        if url.endswith("/repos/octo/repo/issues/7/comments"):
            return FakeResponse([{"user": {"login": "reviewer"}, "body": "Looks good", "created_at": "2026-01-01T00:00:00Z"}])
        if url.endswith("/repos/octo/repo/pulls/7/reviews"):
            return FakeResponse([{"user": {"login": "reviewer"}, "state": "APPROVED", "body": "Ship it", "submitted_at": "2026-01-01T00:00:00Z"}])
        raise AssertionError(f"Unexpected GitHub URL: {url}")

    monkeypatch.setattr("assistant.tools.github_tool.httpx.AsyncClient", lambda timeout=30.0: FakeAsyncClient(handler))

    token_manager = FakeTokenManager({"github": {"access_token": "github-token"}})
    tools = {tool.name: tool for tool in build_github_tools(token_manager)}

    repos_result = await tools["github_list_repos"].handler({"limit": 5})
    assert repos_result["repositories"][0]["full_name"] == "octo/repo"

    issues_result = await tools["github_list_issues"].handler({"owner": "octo", "repo": "repo"})
    assert issues_result["issues"][0]["title"] == "Bug report"

    branches_result = await tools["github_list_branches"].handler({"owner": "octo", "repo": "repo"})
    assert branches_result["branches"][1]["name"] == "Nick"

    prs_result = await tools["github_list_pull_requests"].handler({"owner": "octo", "repo": "repo"})
    assert prs_result["pull_requests"][0]["number"] == 7

    pr_detail = await tools["github_get_pull_request"].handler({"owner": "octo", "repo": "repo", "number": 7})
    assert pr_detail["files"][0]["filename"] == "app.py"
    assert pr_detail["reviews"][0]["state"] == "APPROVED"

    comparison = await tools["github_compare_branches"].handler({"owner": "octo", "repo": "repo", "base": "main", "head": "Nick"})
    assert comparison["ahead_by"] == 2
    assert comparison["status"] == "ahead"

    created = await tools["github_create_pull_request"].handler(
        {"owner": "octo", "repo": "repo", "title": "Hello", "head": "Nick", "base": "main", "body": "Testing"}
    )
    assert created["number"] == 9
    assert created["head"] == "Nick"
    assert created["base"] == "main"

    repo_summary = await tools["github_get_repo_summary"].handler({"owner": "octo", "repo": "repo"})
    assert repo_summary["repository"]["full_name"] == "octo/repo"
    assert repo_summary["counts"]["open_pull_requests"] == 1
    assert repo_summary["counts"]["open_issues"] == 1
    assert repo_summary["counts"]["likely_blockers"] == 1
    assert repo_summary["recent_commits"][0]["message"] == "Fix automation edge case"
    assert "octo/repo has 1 ready pull request(s)" in repo_summary["summary_markdown"]
