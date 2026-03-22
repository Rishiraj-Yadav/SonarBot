"""GitHub tool definitions backed by GitHub OAuth."""

from __future__ import annotations

from typing import Any

import httpx

from assistant.tools.registry import ToolDefinition

GITHUB_API_ROOT = "https://api.github.com"


def build_github_tools(oauth_token_manager) -> list[ToolDefinition]:
    async def github_list_repos(payload: dict[str, Any]) -> dict[str, Any]:
        limit = max(1, min(int(payload.get("limit", 10)), 50))
        visibility = str(payload.get("visibility", "all")).strip() or "all"
        token = await _require_github_token(oauth_token_manager)
        repos = await _github_request(
            token,
            "GET",
            f"{GITHUB_API_ROOT}/user/repos",
            params={"per_page": limit, "sort": "updated", "visibility": visibility},
        )
        return {
            "repositories": [
                {
                    "full_name": repo.get("full_name"),
                    "private": repo.get("private"),
                    "default_branch": repo.get("default_branch"),
                    "description": repo.get("description"),
                    "html_url": repo.get("html_url"),
                }
                for repo in repos
            ]
        }

    async def github_list_issues(payload: dict[str, Any]) -> dict[str, Any]:
        owner = str(payload["owner"]).strip()
        repo = str(payload["repo"]).strip()
        state = str(payload.get("state", "open")).strip() or "open"
        limit = max(1, min(int(payload.get("limit", 10)), 50))
        token = await _require_github_token(oauth_token_manager)
        issues = await _github_request(
            token,
            "GET",
            f"{GITHUB_API_ROOT}/repos/{owner}/{repo}/issues",
            params={"state": state, "per_page": limit},
        )
        filtered = [issue for issue in issues if "pull_request" not in issue]
        return {
            "owner": owner,
            "repo": repo,
            "issues": [
                {
                    "number": issue.get("number"),
                    "title": issue.get("title"),
                    "state": issue.get("state"),
                    "user": (issue.get("user") or {}).get("login"),
                    "labels": [label.get("name") for label in issue.get("labels", [])],
                    "html_url": issue.get("html_url"),
                }
                for issue in filtered
            ],
        }

    async def github_list_pull_requests(payload: dict[str, Any]) -> dict[str, Any]:
        owner = str(payload["owner"]).strip()
        repo = str(payload["repo"]).strip()
        state = str(payload.get("state", "open")).strip() or "open"
        limit = max(1, min(int(payload.get("limit", 10)), 50))
        token = await _require_github_token(oauth_token_manager)
        pulls = await _github_request(
            token,
            "GET",
            f"{GITHUB_API_ROOT}/repos/{owner}/{repo}/pulls",
            params={"state": state, "per_page": limit},
        )
        return {
            "owner": owner,
            "repo": repo,
            "pull_requests": [
                {
                    "number": pull.get("number"),
                    "title": pull.get("title"),
                    "state": pull.get("state"),
                    "user": (pull.get("user") or {}).get("login"),
                    "draft": pull.get("draft"),
                    "html_url": pull.get("html_url"),
                    "head": ((pull.get("head") or {}).get("ref")),
                    "base": ((pull.get("base") or {}).get("ref")),
                }
                for pull in pulls
            ],
        }

    async def github_get_pull_request(payload: dict[str, Any]) -> dict[str, Any]:
        owner = str(payload["owner"]).strip()
        repo = str(payload["repo"]).strip()
        number = int(payload["number"])
        token = await _require_github_token(oauth_token_manager)
        pull = await _github_request(token, "GET", f"{GITHUB_API_ROOT}/repos/{owner}/{repo}/pulls/{number}")
        files = await _github_request(token, "GET", f"{GITHUB_API_ROOT}/repos/{owner}/{repo}/pulls/{number}/files")
        comments = await _github_request(token, "GET", f"{GITHUB_API_ROOT}/repos/{owner}/{repo}/issues/{number}/comments")
        reviews = await _github_request(token, "GET", f"{GITHUB_API_ROOT}/repos/{owner}/{repo}/pulls/{number}/reviews")
        return {
            "owner": owner,
            "repo": repo,
            "number": number,
            "title": pull.get("title"),
            "body": pull.get("body", ""),
            "state": pull.get("state"),
            "draft": pull.get("draft"),
            "html_url": pull.get("html_url"),
            "user": (pull.get("user") or {}).get("login"),
            "head": ((pull.get("head") or {}).get("ref")),
            "base": ((pull.get("base") or {}).get("ref")),
            "files": [
                {
                    "filename": file_item.get("filename"),
                    "status": file_item.get("status"),
                    "additions": file_item.get("additions"),
                    "deletions": file_item.get("deletions"),
                    "changes": file_item.get("changes"),
                    "patch": file_item.get("patch", ""),
                }
                for file_item in files
            ],
            "comments": [
                {
                    "user": (comment.get("user") or {}).get("login"),
                    "body": comment.get("body", ""),
                    "created_at": comment.get("created_at"),
                }
                for comment in comments
            ],
            "reviews": [
                {
                    "user": (review.get("user") or {}).get("login"),
                    "state": review.get("state"),
                    "body": review.get("body", ""),
                    "submitted_at": review.get("submitted_at"),
                }
                for review in reviews
            ],
        }

    return [
        ToolDefinition(
            name="github_list_repos",
            description="List repositories available to the connected GitHub account.",
            parameters={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "default": 10},
                    "visibility": {"type": "string", "enum": ["all", "public", "private"], "default": "all"},
                },
            },
            handler=github_list_repos,
        ),
        ToolDefinition(
            name="github_list_issues",
            description="List issues for a GitHub repository.",
            parameters={
                "type": "object",
                "properties": {
                    "owner": {"type": "string"},
                    "repo": {"type": "string"},
                    "state": {"type": "string", "enum": ["open", "closed", "all"], "default": "open"},
                    "limit": {"type": "integer", "minimum": 1, "default": 10},
                },
                "required": ["owner", "repo"],
            },
            handler=github_list_issues,
        ),
        ToolDefinition(
            name="github_list_pull_requests",
            description="List pull requests for a GitHub repository.",
            parameters={
                "type": "object",
                "properties": {
                    "owner": {"type": "string"},
                    "repo": {"type": "string"},
                    "state": {"type": "string", "enum": ["open", "closed", "all"], "default": "open"},
                    "limit": {"type": "integer", "minimum": 1, "default": 10},
                },
                "required": ["owner", "repo"],
            },
            handler=github_list_pull_requests,
        ),
        ToolDefinition(
            name="github_get_pull_request",
            description="Get a full GitHub pull request including files, comments, and reviews.",
            parameters={
                "type": "object",
                "properties": {
                    "owner": {"type": "string"},
                    "repo": {"type": "string"},
                    "number": {"type": "integer", "minimum": 1},
                },
                "required": ["owner", "repo", "number"],
            },
            handler=github_get_pull_request,
        ),
    ]


async def _require_github_token(oauth_token_manager) -> str:
    token_payload = await oauth_token_manager.get_token("github")
    if token_payload is None or not str(token_payload.get("access_token", "")).strip():
        raise RuntimeError("GitHub OAuth is not connected. Run oauth_connect for provider 'github' first.")
    return str(token_payload["access_token"])


async def _github_request(
    access_token: str,
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
) -> Any:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.request(method, url, headers=headers, params=params)
        response.raise_for_status()
    return response.json()
