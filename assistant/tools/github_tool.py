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

    async def github_get_repo_summary(payload: dict[str, Any]) -> dict[str, Any]:
        owner = str(payload["owner"]).strip()
        repo = str(payload["repo"]).strip()
        issue_limit = max(1, min(int(payload.get("issue_limit", 10)), 25))
        pr_limit = max(1, min(int(payload.get("pr_limit", 10)), 25))
        commit_limit = max(1, min(int(payload.get("commit_limit", 5)), 10))
        token = await _require_github_token(oauth_token_manager)

        repo_data = await _github_request(token, "GET", f"{GITHUB_API_ROOT}/repos/{owner}/{repo}")
        issues = await _github_request(
            token,
            "GET",
            f"{GITHUB_API_ROOT}/repos/{owner}/{repo}/issues",
            params={"state": "open", "per_page": issue_limit},
        )
        pulls = await _github_request(
            token,
            "GET",
            f"{GITHUB_API_ROOT}/repos/{owner}/{repo}/pulls",
            params={"state": "open", "per_page": pr_limit},
        )
        commits = await _github_request(
            token,
            "GET",
            f"{GITHUB_API_ROOT}/repos/{owner}/{repo}/commits",
            params={"per_page": commit_limit},
        )

        filtered_issues = [issue for issue in issues if "pull_request" not in issue]
        blocker_labels = {"blocker", "critical", "urgent", "high", "high-priority", "bug"}
        likely_blockers = [
            {
                "number": issue.get("number"),
                "title": issue.get("title"),
                "labels": [label.get("name") for label in issue.get("labels", [])],
                "html_url": issue.get("html_url"),
            }
            for issue in filtered_issues
            if blocker_labels.intersection(
                {
                    str(label.get("name", "")).strip().lower()
                    for label in issue.get("labels", [])
                    if str(label.get("name", "")).strip()
                }
            )
        ]
        draft_prs = [pull for pull in pulls if bool(pull.get("draft"))]
        ready_prs = [pull for pull in pulls if not bool(pull.get("draft"))]

        summary_parts = [
            f"{owner}/{repo} has {len(ready_prs)} ready pull request(s), {len(draft_prs)} draft pull request(s), and {len(filtered_issues)} open issue(s)."
        ]
        if likely_blockers:
            blocker_titles = ", ".join(str(item.get("title", "")).strip() for item in likely_blockers[:3] if item.get("title"))
            if blocker_titles:
                summary_parts.append(f"Likely blockers: {blocker_titles}.")
        latest_commit = commits[0] if commits else None
        if latest_commit:
            commit_info = latest_commit.get("commit") or {}
            commit_author = (commit_info.get("author") or {}).get("name") or "unknown"
            commit_date = (commit_info.get("author") or {}).get("date") or "unknown date"
            commit_message = str(commit_info.get("message", "")).splitlines()[0].strip()
            if commit_message:
                summary_parts.append(
                    f"Latest commit: '{commit_message}' by {commit_author} on {commit_date}."
                )

        return {
            "owner": owner,
            "repo": repo,
            "repository": {
                "full_name": repo_data.get("full_name"),
                "description": repo_data.get("description"),
                "html_url": repo_data.get("html_url"),
                "private": repo_data.get("private"),
                "default_branch": repo_data.get("default_branch"),
                "language": repo_data.get("language"),
                "topics": repo_data.get("topics", []),
                "open_issues_count": repo_data.get("open_issues_count"),
                "stargazers_count": repo_data.get("stargazers_count"),
                "forks_count": repo_data.get("forks_count"),
                "watchers_count": repo_data.get("watchers_count"),
                "updated_at": repo_data.get("updated_at"),
                "pushed_at": repo_data.get("pushed_at"),
            },
            "counts": {
                "open_pull_requests": len(pulls),
                "ready_pull_requests": len(ready_prs),
                "draft_pull_requests": len(draft_prs),
                "open_issues": len(filtered_issues),
                "likely_blockers": len(likely_blockers),
                "recent_commits": len(commits),
            },
            "open_pull_requests": [
                {
                    "number": pull.get("number"),
                    "title": pull.get("title"),
                    "user": (pull.get("user") or {}).get("login"),
                    "draft": pull.get("draft"),
                    "html_url": pull.get("html_url"),
                    "head": ((pull.get("head") or {}).get("ref")),
                    "base": ((pull.get("base") or {}).get("ref")),
                }
                for pull in pulls
            ],
            "open_issues": [
                {
                    "number": issue.get("number"),
                    "title": issue.get("title"),
                    "user": (issue.get("user") or {}).get("login"),
                    "labels": [label.get("name") for label in issue.get("labels", [])],
                    "html_url": issue.get("html_url"),
                }
                for issue in filtered_issues
            ],
            "recent_commits": [
                {
                    "sha": commit.get("sha"),
                    "message": str((commit.get("commit") or {}).get("message", "")).splitlines()[0].strip(),
                    "author": (((commit.get("commit") or {}).get("author") or {}).get("name")),
                    "date": (((commit.get("commit") or {}).get("author") or {}).get("date")),
                    "html_url": commit.get("html_url"),
                }
                for commit in commits
            ],
            "likely_blockers": likely_blockers,
            "summary_markdown": " ".join(part for part in summary_parts if part).strip(),
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
        ToolDefinition(
            name="github_get_repo_summary",
            description="Get a high-level GitHub repository summary including metadata, open pull requests, issues, blockers, and recent commits.",
            parameters={
                "type": "object",
                "properties": {
                    "owner": {"type": "string"},
                    "repo": {"type": "string"},
                    "issue_limit": {"type": "integer", "minimum": 1, "default": 10},
                    "pr_limit": {"type": "integer", "minimum": 1, "default": 10},
                    "commit_limit": {"type": "integer", "minimum": 1, "default": 5},
                },
                "required": ["owner", "repo"],
            },
            handler=github_get_repo_summary,
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
