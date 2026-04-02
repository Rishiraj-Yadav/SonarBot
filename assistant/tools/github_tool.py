"""GitHub tool definitions backed by GitHub OAuth."""

from __future__ import annotations

from typing import Any

import httpx

from assistant.tools.registry import ToolDefinition

GITHUB_API_ROOT = "https://api.github.com"


def build_github_tools(oauth_token_manager) -> list[ToolDefinition]:
    async def github_list_branches(payload: dict[str, Any]) -> dict[str, Any]:
        owner = str(payload["owner"]).strip()
        repo = str(payload["repo"]).strip()
        limit = max(1, min(int(payload.get("limit", 100)), 100))
        protected_only = bool(payload.get("protected_only", False))
        token = await _require_github_token(oauth_token_manager)
        branches = await _github_request(
            token,
            "GET",
            f"{GITHUB_API_ROOT}/repos/{owner}/{repo}/branches",
            params={"per_page": limit, **({"protected": "true"} if protected_only else {})},
        )
        return {
            "owner": owner,
            "repo": repo,
            "branches": [
                {
                    "name": branch.get("name"),
                    "protected": bool(branch.get("protected")),
                    "sha": ((branch.get("commit") or {}).get("sha")),
                }
                for branch in branches
            ],
        }

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

    async def github_compare_branches(payload: dict[str, Any]) -> dict[str, Any]:
        owner = str(payload["owner"]).strip()
        repo = str(payload["repo"]).strip()
        base = str(payload["base"]).strip()
        head = str(payload["head"]).strip()
        token = await _require_github_token(oauth_token_manager)
        comparison = await _github_request(
            token,
            "GET",
            f"{GITHUB_API_ROOT}/repos/{owner}/{repo}/compare/{base}...{head}",
        )
        return {
            "owner": owner,
            "repo": repo,
            "base": base,
            "head": head,
            "status": comparison.get("status"),
            "ahead_by": comparison.get("ahead_by", 0),
            "behind_by": comparison.get("behind_by", 0),
            "total_commits": comparison.get("total_commits", 0),
            "html_url": comparison.get("html_url"),
            "commits": [
                {
                    "sha": commit.get("sha"),
                    "message": str((commit.get("commit") or {}).get("message", "")).splitlines()[0].strip(),
                    "author": (((commit.get("commit") or {}).get("author") or {}).get("name")),
                }
                for commit in comparison.get("commits", [])
            ],
            "files": [
                {
                    "filename": file_item.get("filename"),
                    "status": file_item.get("status"),
                    "changes": file_item.get("changes"),
                }
                for file_item in comparison.get("files", [])
            ],
        }

    async def github_create_pull_request(payload: dict[str, Any]) -> dict[str, Any]:
        owner = str(payload["owner"]).strip()
        repo = str(payload["repo"]).strip()
        title = str(payload["title"]).strip()
        head = str(payload["head"]).strip()
        base = str(payload["base"]).strip()
        body = str(payload.get("body", "")).strip()
        draft = bool(payload.get("draft", False))
        maintainer_can_modify = bool(payload.get("maintainer_can_modify", True))
        token = await _require_github_token(oauth_token_manager)
        created = await _github_request(
            token,
            "POST",
            f"{GITHUB_API_ROOT}/repos/{owner}/{repo}/pulls",
            json_body={
                "title": title,
                "head": head,
                "base": base,
                "body": body,
                "draft": draft,
                "maintainer_can_modify": maintainer_can_modify,
            },
        )
        return {
            "owner": owner,
            "repo": repo,
            "number": created.get("number"),
            "title": created.get("title"),
            "state": created.get("state"),
            "draft": created.get("draft"),
            "html_url": created.get("html_url"),
            "user": (created.get("user") or {}).get("login"),
            "head": ((created.get("head") or {}).get("ref")) or head,
            "base": ((created.get("base") or {}).get("ref")) or base,
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
            name="github_list_branches",
            description="List branches for a GitHub repository.",
            parameters={
                "type": "object",
                "properties": {
                    "owner": {"type": "string"},
                    "repo": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "default": 100},
                    "protected_only": {"type": "boolean", "default": False},
                },
                "required": ["owner", "repo"],
            },
            handler=github_list_branches,
        ),
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
            name="github_compare_branches",
            description="Compare two branches in a GitHub repository to see whether the head branch has commits to merge.",
            parameters={
                "type": "object",
                "properties": {
                    "owner": {"type": "string"},
                    "repo": {"type": "string"},
                    "base": {"type": "string"},
                    "head": {"type": "string"},
                },
                "required": ["owner", "repo", "base", "head"],
            },
            handler=github_compare_branches,
        ),
        ToolDefinition(
            name="github_create_pull_request",
            description="Create a pull request in a GitHub repository.",
            parameters={
                "type": "object",
                "properties": {
                    "owner": {"type": "string"},
                    "repo": {"type": "string"},
                    "title": {"type": "string"},
                    "head": {"type": "string"},
                    "base": {"type": "string"},
                    "body": {"type": "string", "default": ""},
                    "draft": {"type": "boolean", "default": False},
                    "maintainer_can_modify": {"type": "boolean", "default": True},
                },
                "required": ["owner", "repo", "title", "head", "base"],
            },
            handler=github_create_pull_request,
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
    json_body: dict[str, Any] | None = None,
) -> Any:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.request(method, url, headers=headers, params=params, json=json_body)
    if getattr(response, "status_code", 200) >= 400:
        raise RuntimeError(_format_github_error(response))
    if getattr(response, "status_code", 200) == 204:
        return {}
    return response.json()


def _format_github_error(response: Any) -> str:
    status_code = int(getattr(response, "status_code", 500))
    try:
        payload = response.json()
    except Exception:
        payload = None
    if isinstance(payload, dict):
        message = str(payload.get("message", "")).strip()
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            parts: list[str] = []
            for error in errors:
                if isinstance(error, dict):
                    detail = str(error.get("message") or error.get("code") or "").strip()
                    field = str(error.get("field", "")).strip()
                    if field and detail and field not in detail:
                        detail = f"{field}: {detail}"
                    if detail:
                        parts.append(detail)
                elif error:
                    parts.append(str(error).strip())
            if message and parts:
                return f"{message}: {'; '.join(part for part in parts if part)}"
            if parts:
                return "; ".join(part for part in parts if part)
        if message:
            return message
    return f"GitHub API request failed with status {status_code}."
