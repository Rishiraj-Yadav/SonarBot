"""Execution engine for autonomous browser workflows."""

from __future__ import annotations

from dataclasses import asdict
import re
from typing import Any
from urllib.parse import urlparse

from assistant.browser_workflows.models import BlockingState, BrowserWorkflowMatch, BrowserWorkflowResult, WorkflowPlanStep
from assistant.browser_workflows.nlp import BrowserWorkflowNLP, infer_site_from_runtime
from assistant.browser_workflows.recipes import LOGIN_FAVORING_SITES, RECIPES, SITE_LOGIN_URLS, SITE_URLS
from assistant.browser_workflows.state import active_browser_task, browser_task_state_update, normalize_browser_task_state
from assistant.utils.user_facing_errors import format_browser_exception


class BrowserWorkflowEngine:
    def __init__(self, config, tool_registry) -> None:
        self.config = config
        self.tool_registry = tool_registry
        self.runtime = getattr(tool_registry, "browser_runtime", None)
        self.nlp = BrowserWorkflowNLP(config, tool_registry)

    def available_workflows(self) -> list[dict[str, Any]]:
        return [asdict(recipe) for recipe in RECIPES]

    async def maybe_run(
        self,
        message: str,
        *,
        user_id: str,
        session_key: str,
        channel: str,
        previous_state: dict[str, Any] | None = None,
        force: bool = False,
    ) -> BrowserWorkflowResult | None:
        if not self.config.browser_workflows.enabled or self.runtime is None:
            return None
        runtime_state = self.runtime.current_state() if self.runtime is not None else {}
        task_state = normalize_browser_task_state(previous_state)
        match = await self.nlp.match(
            message,
            runtime_state=runtime_state,
            previous_state=task_state,
            force=force,
        )
        if match is None:
            return None
        return await self.run_match(
            match,
            user_id=user_id,
            session_key=session_key,
            channel=channel,
            previous_state=previous_state,
        )

    async def run_match(
        self,
        match: BrowserWorkflowMatch,
        *,
        user_id: str,
        session_key: str,
        channel: str,
        previous_state: dict[str, Any] | None = None,
    ) -> BrowserWorkflowResult:
        task_state = normalize_browser_task_state(previous_state or match.details.get("task_state"))
        active_task = active_browser_task(task_state)
        await self._emit(
            user_id,
            "browser.workflow.started",
            {
                "recipe_name": match.recipe_name,
                "site_name": match.site_name,
                "query": match.query,
                "execution_mode": self._desired_mode_for_match(match),
            },
        )
        try:
            if match.recipe_name == "site_open_exact_url_or_path":
                result = await self._run_site_open_exact_url_or_path(match, user_id=user_id)
            elif match.recipe_name == "youtube_search_play":
                result = await self._run_youtube_search_play(match, user_id=user_id, task_state=task_state)
            elif match.recipe_name == "youtube_latest_video":
                result = await self._run_youtube_latest_video(match, user_id=user_id, task_state=task_state)
            elif match.recipe_name == "google_search_open":
                result = await self._run_google_search_open(match, user_id=user_id, task_state=task_state)
            elif match.recipe_name == "site_open_and_search":
                result = await self._run_site_open_and_search(match, user_id=user_id, task_state=task_state)
            elif match.recipe_name == "leetcode_open_problem":
                result = await self._run_leetcode_open_problem(match, user_id=user_id, task_state=task_state)
            elif match.recipe_name == "github_repo_inspect":
                result = await self._run_github_repo_inspect(match, user_id=user_id, task_state=task_state)
            elif match.recipe_name == "github_issue_compose":
                result = await self._run_github_issue_compose(match, user_id=user_id, task_state=task_state)
            elif match.recipe_name == "site_login_then_continue":
                result = await self._run_site_login_then_continue(match, user_id=user_id, task_state=task_state)
            elif match.recipe_name == "browser_continue_last_task":
                result = await self._run_continue_last_task(match, user_id=user_id, task_state=task_state)
            else:
                result = BrowserWorkflowResult(
                    recipe_name=match.recipe_name,
                    status="needs_followup",
                    response_text="I couldn't map that browser instruction to a supported workflow yet.",
                )
        except Exception as exc:
            safe_text = format_browser_exception(exc)
            await self._emit(
                user_id,
                "browser.workflow.blocked",
                {"recipe_name": match.recipe_name, "reason": "error", "message": safe_text},
            )
            return BrowserWorkflowResult(
                recipe_name=match.recipe_name,
                status="error",
                response_text=safe_text,
                state_update=browser_task_state_update(
                    active_task=self._build_workflow_state(
                        recipe_name=match.recipe_name,
                        site_name=match.site_name,
                        query=match.query,
                        target_url=str(active_task.get("active_url", "") or match.details.get("target_url", "")),
                        execution_mode=self._desired_mode_for_match(match),
                        blocked_reason="error",
                        awaiting_followup="continue",
                    ),
                    pending_confirmation=task_state.get("pending_confirmation"),
                    pending_login=task_state.get("pending_login"),
                ),
                payload={"raw_error": str(exc)},
            )
        event_name = "browser.workflow.completed" if result.status == "completed" else "browser.workflow.blocked"
        await self._emit(
            user_id,
            event_name,
            {"recipe_name": result.recipe_name, "status": result.status, "response_text": result.response_text, **result.payload},
        )
        return result

    async def _run_youtube_search_play(
        self,
        match: BrowserWorkflowMatch,
        *,
        user_id: str,
        task_state: dict[str, Any],
    ) -> BrowserWorkflowResult:
        query = (match.query or "").strip()
        execution_mode = self._desired_mode_for_match(match)
        if not query:
            return BrowserWorkflowResult(
                recipe_name=match.recipe_name,
                status="needs_followup",
                response_text="Tell me the YouTube video title you want me to play.",
            )
        page = await self._open_site("youtube", user_id=user_id, headless=self._mode_headless(execution_mode))
        progress = ["Opened YouTube."]
        steps = [WorkflowPlanStep(name="open_site", detail="Open YouTube.", status="completed")]
        await self._emit_step(user_id, match.recipe_name, "open_site", progress[-1])
        blocked = await self._detect_blocker(page)
        if blocked:
            return self._blocked_result(match, blocked, progress, steps)
        search_box, _strategy = await self.runtime.find_search_input(page, site_name="youtube")
        await search_box.fill(query)
        await self.runtime.press_key(page, "Enter")
        await self.runtime.post_action_wait(page, "networkidle", 30)
        await self.runtime.refresh_active_tab(user_id)
        progress.append(f"Searching for \"{query}\".")
        steps.append(WorkflowPlanStep(name="search", detail=f"Search YouTube for {query}.", status="completed"))
        await self._emit_step(user_id, match.recipe_name, "search", progress[-1])
        blocked = await self._detect_blocker(page)
        if blocked:
            return self._blocked_result(match, blocked, progress, steps)
        results = await self.runtime.extract_search_results(
            page,
            site_name="youtube",
            max_results=self.config.browser_workflows.max_results_to_rank,
        )
        if not results:
            return BrowserWorkflowResult(
                recipe_name=match.recipe_name,
                status="needs_followup",
                response_text=f"I searched YouTube for \"{query}\", but I couldn't find a clear video result.",
                progress_lines=progress,
                steps=steps,
                state_update=browser_task_state_update(
                    active_task=self._build_workflow_state(
                        recipe_name=match.recipe_name,
                        site_name="youtube",
                        query=query,
                        target_url=page.url,
                        execution_mode=execution_mode,
                        blocked_reason="missing_result",
                        awaiting_followup="continue",
                    )
                ),
            )
        chosen = await self.runtime.click_best_match(
            page,
            query,
            results,
            site_name="youtube",
            open_first_result=False,
            timeout_seconds=30,
        )
        await self.runtime.refresh_active_tab(user_id)
        await self.runtime.try_start_media_playback(page)
        progress.append(f"Opened the best matching video: {chosen.get('title', 'video')}.")
        steps.append(WorkflowPlanStep(name="open_result", detail="Open the best matching YouTube result.", status="completed"))
        await self._emit_step(user_id, match.recipe_name, "open_result", progress[-1])
        return BrowserWorkflowResult(
            recipe_name=match.recipe_name,
            status="completed",
            response_text=(
                f"Opened YouTube, searched for \"{query}\", and opened the best matching video in "
                f"{self.runtime.current_tab_id or 'the current tab'}."
            ),
            progress_lines=progress,
            steps=steps,
            state_update=browser_task_state_update(
                active_task=self._build_workflow_state(
                    recipe_name=match.recipe_name,
                    site_name="youtube",
                    query=query,
                    target_url=page.url,
                    execution_mode=execution_mode,
                    last_result_title=chosen.get("title", ""),
                ),
            ),
            payload={"tab_id": self.runtime.current_tab_id, "site_name": "youtube", "query": query, "execution_mode": execution_mode},
        )

    async def _run_youtube_latest_video(
        self,
        match: BrowserWorkflowMatch,
        *,
        user_id: str,
        task_state: dict[str, Any],
    ) -> BrowserWorkflowResult:
        query = (match.query or "").strip()
        execution_mode = self._desired_mode_for_match(match)
        if not query:
            return BrowserWorkflowResult(
                recipe_name=match.recipe_name,
                status="needs_followup",
                response_text="Tell me which creator or channel you want the latest YouTube video from.",
            )
        page = await self._open_site("youtube", user_id=user_id, headless=self._mode_headless(execution_mode))
        progress = ["Opened YouTube."]
        steps = [WorkflowPlanStep(name="open_site", detail="Open YouTube.", status="completed")]
        await self._emit_step(user_id, match.recipe_name, "open_site", progress[-1])
        blocked = await self._detect_blocker(page)
        if blocked:
            return self._blocked_result(match, blocked, progress, steps)
        search_box, _strategy = await self.runtime.find_search_input(page, site_name="youtube")
        await search_box.fill(query)
        await self.runtime.press_key(page, "Enter")
        await self.runtime.post_action_wait(page, "networkidle", 30)
        await self.runtime.refresh_active_tab(user_id)
        progress.append(f"Searching YouTube for the latest video from \"{query}\".")
        steps.append(WorkflowPlanStep(name="search", detail=f"Search YouTube for {query}.", status="completed"))
        await self._emit_step(user_id, match.recipe_name, "search", progress[-1])
        results = await self.runtime.extract_search_results(
            page,
            site_name="youtube",
            max_results=self.config.browser_workflows.max_results_to_rank,
        )
        if not results:
            return BrowserWorkflowResult(
                recipe_name=match.recipe_name,
                status="needs_followup",
                response_text=f"I searched YouTube for \"{query}\", but I couldn't find a clear recent video result.",
                progress_lines=progress,
                steps=steps,
                state_update=browser_task_state_update(
                    active_task=self._build_workflow_state(
                        recipe_name=match.recipe_name,
                        site_name="youtube",
                        query=query,
                        target_url=page.url,
                        execution_mode=execution_mode,
                        blocked_reason="missing_result",
                        awaiting_followup="continue",
                    )
                ),
            )
        chosen = await self.runtime.click_best_match(
            page,
            query,
            results,
            site_name="youtube",
            open_first_result=True,
            timeout_seconds=30,
        )
        await self.runtime.refresh_active_tab(user_id)
        await self.runtime.try_start_media_playback(page)
        progress.append(f"Opened the latest-looking result: {chosen.get('title', 'video')}.")
        steps.append(WorkflowPlanStep(name="open_result", detail="Open the top recent YouTube result.", status="completed"))
        await self._emit_step(user_id, match.recipe_name, "open_result", progress[-1])
        return BrowserWorkflowResult(
            recipe_name=match.recipe_name,
            status="completed",
            response_text=(
                f"Opened YouTube, searched for the latest video from \"{query}\", and opened the top matching result in "
                f"{self.runtime.current_tab_id or 'the current tab'}."
            ),
            progress_lines=progress,
            steps=steps,
            state_update=browser_task_state_update(
                active_task=self._build_workflow_state(
                    recipe_name=match.recipe_name,
                    site_name="youtube",
                    query=query,
                    target_url=page.url,
                    execution_mode=execution_mode,
                    last_result_title=chosen.get("title", ""),
                )
            ),
            payload={"tab_id": self.runtime.current_tab_id, "site_name": "youtube", "query": query, "execution_mode": execution_mode},
        )

    async def _run_google_search_open(
        self,
        match: BrowserWorkflowMatch,
        *,
        user_id: str,
        task_state: dict[str, Any],
    ) -> BrowserWorkflowResult:
        query = (match.query or "").strip()
        execution_mode = self._desired_mode_for_match(match)
        if not query:
            return BrowserWorkflowResult(
                recipe_name=match.recipe_name,
                status="needs_followup",
                response_text="Tell me what you want me to search for on Google.",
            )
        page = await self._open_site("google", user_id=user_id, headless=self._mode_headless(execution_mode))
        progress = ["Opened Google."]
        steps = [WorkflowPlanStep(name="open_site", detail="Open Google.", status="completed")]
        await self._emit_step(user_id, match.recipe_name, "open_site", progress[-1])
        blocked = await self._detect_blocker(page)
        if blocked:
            return self._blocked_result(match, blocked, progress, steps)
        search_box, _strategy = await self.runtime.find_search_input(page, site_name="google")
        await search_box.fill(query)
        await self.runtime.press_key(page, "Enter")
        await self.runtime.post_action_wait(page, "networkidle", 30)
        await self.runtime.refresh_active_tab(user_id)
        progress.append(f"Searching Google for \"{query}\".")
        steps.append(WorkflowPlanStep(name="search", detail=f"Search Google for {query}.", status="completed"))
        await self._emit_step(user_id, match.recipe_name, "search", progress[-1])
        blocked = await self._detect_blocker(page)
        if blocked:
            return self._blocked_result(match, blocked, progress, steps)
        results = await self.runtime.extract_search_results(
            page,
            site_name="google",
            max_results=self.config.browser_workflows.max_results_to_rank,
        )
        if not results:
            return BrowserWorkflowResult(
                recipe_name=match.recipe_name,
                status="needs_followup",
                response_text=f"I searched Google for \"{query}\", but I couldn't find a usable result.",
                progress_lines=progress,
                steps=steps,
                state_update=browser_task_state_update(
                    active_task=self._build_workflow_state(
                        recipe_name=match.recipe_name,
                        site_name="google",
                        query=query,
                        target_url=page.url,
                        execution_mode=execution_mode,
                        blocked_reason="missing_result",
                        awaiting_followup="continue",
                    )
                ),
            )
        chosen = await self.runtime.click_best_match(
            page,
            query,
            results,
            site_name="google",
            open_first_result=match.open_first_result,
            timeout_seconds=30,
        )
        await self.runtime.refresh_active_tab(user_id)
        progress.append(f"Opened result: {chosen.get('title', 'result')}.")
        steps.append(WorkflowPlanStep(name="open_result", detail="Open the chosen Google result.", status="completed"))
        await self._emit_step(user_id, match.recipe_name, "open_result", progress[-1])
        which = "first" if match.open_first_result else "best matching"
        return BrowserWorkflowResult(
            recipe_name=match.recipe_name,
            status="completed",
            response_text=(
                f"Opened Google, searched for \"{query}\", and opened the {which} result in "
                f"{self.runtime.current_tab_id or 'the current tab'}."
            ),
            progress_lines=progress,
            steps=steps,
            state_update=browser_task_state_update(
                active_task=self._build_workflow_state(
                    recipe_name=match.recipe_name,
                    site_name="google",
                    query=query,
                    target_url=page.url,
                    execution_mode=execution_mode,
                    open_first_result=match.open_first_result,
                )
            ),
            payload={"tab_id": self.runtime.current_tab_id, "site_name": "google", "query": query, "execution_mode": execution_mode},
        )

    async def _run_site_open_exact_url_or_path(self, match: BrowserWorkflowMatch, *, user_id: str) -> BrowserWorkflowResult:
        site_name = (match.site_name or "").strip()
        target_url = self._resolve_target_url(match, site_name)
        execution_mode = self._desired_mode_for_match(match)
        if not target_url:
            return BrowserWorkflowResult(
                recipe_name=match.recipe_name,
                status="needs_followup",
                response_text="Tell me which URL or site you want me to open.",
            )
        page = await self._open_site(site_name or target_url, user_id=user_id, target_url=target_url, headless=self._mode_headless(execution_mode))
        label = self._site_label(site_name, page.url)
        progress = [f"Opened {label}."]
        steps = [WorkflowPlanStep(name="open_site", detail=f"Open {label}.", status="completed")]
        await self._emit_step(user_id, match.recipe_name, "open_site", progress[-1])
        blocked = await self._detect_blocker(page)
        if blocked:
            return self._blocked_result(match, blocked, progress, steps)
        return BrowserWorkflowResult(
            recipe_name=match.recipe_name,
            status="completed",
            response_text=f"Opened {label} in {self.runtime.current_tab_id or 'the current tab'}.",
            progress_lines=progress,
            steps=steps,
            state_update=browser_task_state_update(
                active_task=self._build_workflow_state(
                    recipe_name=match.recipe_name,
                    site_name=self._site_from_url(page.url) or site_name,
                    query="",
                    target_url=page.url,
                    execution_mode=execution_mode,
                    awaiting_followup="site_action",
                )
            ),
            payload={"tab_id": self.runtime.current_tab_id, "site_name": site_name or self._site_from_url(page.url), "execution_mode": execution_mode},
        )

    async def _run_leetcode_open_problem(
        self,
        match: BrowserWorkflowMatch,
        *,
        user_id: str,
        task_state: dict[str, Any],
    ) -> BrowserWorkflowResult:
        query = (match.query or "").strip()
        execution_mode = self._desired_mode_for_match(match)
        if not query:
            return BrowserWorkflowResult(
                recipe_name=match.recipe_name,
                status="needs_followup",
                response_text="Tell me which LeetCode problem number or title you want me to open.",
            )
        if not self._has_active_profile("leetcode"):
            opened = None
            try:
                opened = await self.runtime.start_login(
                    "leetcode",
                    "default",
                    SITE_LOGIN_URLS["leetcode"],
                    user_id=user_id,
                )
            except Exception:
                opened = None
            return BrowserWorkflowResult(
                recipe_name=match.recipe_name,
                status="blocked",
                response_text=(
                    "LeetCode needs login first. "
                    + (
                        "I opened a visible browser window for the login page on the host machine. Complete the login there and say \"continue\"."
                        if opened is not None
                        else "Complete the login in the visible browser window on the host machine, then say \"continue\"."
                    )
                ),
                state_update=browser_task_state_update(
                    active_task=self._build_workflow_state(
                        recipe_name=match.recipe_name,
                        site_name="leetcode",
                        query=query,
                        target_url=SITE_LOGIN_URLS["leetcode"],
                        execution_mode="headed",
                        blocked_reason="login_required",
                        awaiting_followup="continue",
                    ),
                    pending_login={"site_name": "leetcode", "target_url": SITE_LOGIN_URLS["leetcode"], "execution_mode": "headed"},
                ),
                payload={"blocking_reason": "login_required", "site_name": "leetcode", "execution_mode": "headed"},
            )
        search_url = f"https://leetcode.com/problemset/?search={query}"
        page = await self._open_site("leetcode", user_id=user_id, target_url=search_url, headless=self._mode_headless(execution_mode))
        progress = ["Opened LeetCode."]
        steps = [WorkflowPlanStep(name="open_site", detail="Open LeetCode.", status="completed")]
        await self._emit_step(user_id, match.recipe_name, "open_site", progress[-1])
        blocked = await self._detect_blocker(page)
        if blocked:
            return self._blocked_result(match, blocked, progress, steps)
        await self.runtime.post_action_wait(page, "networkidle", 30)
        await self.runtime.refresh_active_tab(user_id)
        results = await self.runtime.extract_search_results(page, site_name="leetcode", max_results=self.config.browser_workflows.max_results_to_rank)
        if not results:
            return BrowserWorkflowResult(
                recipe_name=match.recipe_name,
                status="needs_followup",
                response_text=f"I opened LeetCode and searched for \"{query}\", but I couldn't find a clear problem result.",
                progress_lines=progress,
                steps=steps,
                state_update=browser_task_state_update(
                    active_task=self._build_workflow_state(
                        recipe_name=match.recipe_name,
                        site_name="leetcode",
                        query=query,
                        target_url=page.url,
                        execution_mode=execution_mode,
                        blocked_reason="missing_result",
                        awaiting_followup="continue",
                    )
                ),
            )
        chosen = await self.runtime.click_best_match(
            page,
            query,
            results,
            site_name="leetcode",
            open_first_result=bool(re.fullmatch(r"\d+", query)),
            timeout_seconds=30,
        )
        await self.runtime.refresh_active_tab(user_id)
        progress.append(f"Opened LeetCode problem: {chosen.get('title', 'problem')}.")
        steps.append(WorkflowPlanStep(name="open_problem", detail="Open the matching LeetCode problem.", status="completed"))
        await self._emit_step(user_id, match.recipe_name, "open_problem", progress[-1])
        return BrowserWorkflowResult(
            recipe_name=match.recipe_name,
            status="completed",
            response_text=f"Opened LeetCode problem {query} in {self.runtime.current_tab_id or 'the current tab'}.",
            progress_lines=progress,
            steps=steps,
            state_update=browser_task_state_update(
                active_task=self._build_workflow_state(
                    recipe_name=match.recipe_name,
                    site_name="leetcode",
                    query=query,
                    target_url=page.url,
                    execution_mode=execution_mode,
                    last_result_title=chosen.get("title", ""),
                )
            ),
            payload={"tab_id": self.runtime.current_tab_id, "site_name": "leetcode", "query": query, "execution_mode": execution_mode},
        )

    async def _run_github_repo_inspect(
        self,
        match: BrowserWorkflowMatch,
        *,
        user_id: str,
        task_state: dict[str, Any],
    ) -> BrowserWorkflowResult:
        repo_ref = await self._resolve_github_repo_reference(match)
        if repo_ref is None:
            return BrowserWorkflowResult(
                recipe_name=match.recipe_name,
                status="needs_followup",
                response_text="Tell me which GitHub repository you want me to inspect, for example owner/repo.",
            )
        owner, repo, repo_payload = repo_ref
        pulls = await self.tool_registry.dispatch(
            "github_list_pull_requests",
            {"owner": owner, "repo": repo, "limit": 10, "state": "open"},
        )
        issues = await self.tool_registry.dispatch(
            "github_list_issues",
            {"owner": owner, "repo": repo, "limit": 10, "state": "open"},
        )
        description = str(repo_payload.get("description") or "No description is set.")
        lines = [
            f"Here is what I found for {owner}/{repo}:",
            f"- Description: {description}",
            f"- Default branch: {repo_payload.get('default_branch', 'unknown')}",
            f"- Open pull requests: {len(pulls.get('pull_requests', []))}",
            f"- Open issues: {len(issues.get('issues', []))}",
        ]
        return BrowserWorkflowResult(
            recipe_name=match.recipe_name,
            status="completed",
            response_text="\n".join(lines),
            state_update=browser_task_state_update(
                active_task=self._build_workflow_state(
                    recipe_name=match.recipe_name,
                    site_name="github",
                    query=f"{owner}/{repo}",
                    target_url=str(repo_payload.get("html_url", "")),
                    execution_mode="headless",
                )
            ),
            payload={"owner": owner, "repo": repo, "html_url": repo_payload.get("html_url", "")},
        )

    async def _run_github_issue_compose(
        self,
        match: BrowserWorkflowMatch,
        *,
        user_id: str,
        task_state: dict[str, Any],
    ) -> BrowserWorkflowResult:
        repo_ref = await self._resolve_github_repo_reference(match)
        if repo_ref is None:
            return BrowserWorkflowResult(
                recipe_name=match.recipe_name,
                status="needs_followup",
                response_text="Tell me which GitHub repository you want to open an issue for, for example owner/repo.",
            )
        owner, repo, _repo_payload = repo_ref
        target_url = f"https://github.com/{owner}/{repo}/issues/new"
        page = await self._open_site("github", user_id=user_id, target_url=target_url, headless=False)
        progress = [f"Opened the GitHub issue composer for {owner}/{repo}."]
        steps = [WorkflowPlanStep(name="open_issue_composer", detail="Open the GitHub issue composer.", status="completed")]
        await self._emit_step(user_id, match.recipe_name, "open_issue_composer", progress[-1])
        blocked = await self._detect_blocker(page)
        if blocked:
            return self._blocked_result(match, blocked, progress, steps)
        pending = await self.runtime.prepare_protected_action(
            "submit",
            selector="button[type='submit']",
            target=target_url,
            description=f"Submit the GitHub issue for {owner}/{repo}",
            user_id=user_id,
        )
        progress.append("A visible browser window is open on the host machine. Fill or review the issue there, then reply with confirm or cancel.")
        steps.append(WorkflowPlanStep(name="pause_before_submit", detail="Pause before submitting the GitHub issue.", status="blocked"))
        return BrowserWorkflowResult(
            recipe_name=match.recipe_name,
            status="blocked",
            response_text=(
                f"I opened the GitHub issue composer for {owner}/{repo} in a visible browser window on the host machine and paused before submit. "
                'Fill or review the issue there, then reply with "confirm" or "cancel".'
            ),
            progress_lines=progress,
            steps=steps,
            state_update=browser_task_state_update(
                active_task=self._build_workflow_state(
                    recipe_name=match.recipe_name,
                    site_name="github",
                    query=f"{owner}/{repo}",
                    target_url=target_url,
                    execution_mode="headed",
                    awaiting_followup="confirmation",
                ),
                pending_confirmation={
                    **pending,
                    "site_name": "github",
                    "owner": owner,
                    "repo": repo,
                },
            ),
            payload={"owner": owner, "repo": repo, "target_url": target_url, "pending_action": pending},
        )

    async def _run_site_open_and_search(
        self,
        match: BrowserWorkflowMatch,
        *,
        user_id: str,
        task_state: dict[str, Any],
    ) -> BrowserWorkflowResult:
        site_name = (match.site_name or infer_site_from_runtime(self.runtime.current_state()) or "").strip()
        target_url = self._resolve_target_url(match, site_name)
        execution_mode = self._desired_mode_for_match(match)
        if not site_name and not target_url:
            return BrowserWorkflowResult(
                recipe_name=match.recipe_name,
                status="needs_followup",
                response_text="Tell me which supported site you want me to open.",
            )
        query = (match.query or "").strip()
        normalized_site = site_name.lower()
        if normalized_site in LOGIN_FAVORING_SITES and not self._has_active_profile(normalized_site):
            opened = None
            try:
                opened = await self.runtime.start_login(
                    site_name,
                    "default",
                    SITE_LOGIN_URLS.get(normalized_site, target_url or SITE_URLS[normalized_site]),
                    user_id=user_id,
                )
            except Exception:
                opened = None
            return BrowserWorkflowResult(
                recipe_name=match.recipe_name,
                status="blocked",
                response_text=(
                    f"{site_name.title()} needs login first. "
                    + (
                        "I opened a visible browser window for the login page on the host machine. Complete the login there and say \"continue\"."
                        if opened is not None
                        else "Complete the login in the visible browser window on the host machine, then say \"continue\"."
                    )
                ),
                state_update=browser_task_state_update(
                    active_task=self._build_workflow_state(
                        recipe_name=match.recipe_name,
                        site_name=site_name,
                        query=query,
                        target_url=target_url,
                        execution_mode="headed",
                        blocked_reason="login_required",
                        awaiting_followup="continue",
                    ),
                    pending_login={
                        "site_name": site_name,
                        "target_url": target_url,
                        "execution_mode": "headed",
                    },
                ),
                payload={"blocking_reason": "login_required", "site_name": site_name, "tab_id": self.runtime.current_tab_id, "execution_mode": "headed"},
            )
        page = await self._open_site(
            site_name,
            user_id=user_id,
            target_url=target_url,
            headless=self._mode_headless(execution_mode),
        )
        label = self._site_label(site_name, page.url)
        progress = [f"Opened {label}."]
        steps = [WorkflowPlanStep(name="open_site", detail=f"Open {label}.", status="completed")]
        await self._emit_step(user_id, match.recipe_name, "open_site", progress[-1])
        blocked = await self._detect_blocker(page)
        if blocked:
            return self._blocked_result(match, blocked, progress, steps)
        if not query:
            return BrowserWorkflowResult(
                recipe_name=match.recipe_name,
                status="completed",
                response_text=f"Opened {label} in {self.runtime.current_tab_id or 'the current tab'}.",
                progress_lines=progress,
                steps=steps,
                state_update=browser_task_state_update(
                    active_task=self._build_workflow_state(
                        recipe_name=match.recipe_name,
                        site_name=self._site_from_url(page.url) or site_name,
                        query="",
                        target_url=page.url,
                        execution_mode=execution_mode,
                        awaiting_followup="site_action",
                    )
                ),
                payload={"tab_id": self.runtime.current_tab_id, "site_name": site_name, "execution_mode": execution_mode},
            )
        search_box, _strategy = await self.runtime.find_search_input(page, site_name=site_name)
        await search_box.fill(query)
        await self.runtime.press_key(page, "Enter")
        await self.runtime.post_action_wait(page, "networkidle", 30)
        await self.runtime.refresh_active_tab(user_id)
        progress.append(f"Searched {label} for \"{query}\".")
        steps.append(WorkflowPlanStep(name="search", detail=f"Search {label} for {query}.", status="completed"))
        await self._emit_step(user_id, match.recipe_name, "search", progress[-1])
        blocked = await self._detect_blocker(page)
        if blocked:
            return self._blocked_result(match, blocked, progress, steps)
        return BrowserWorkflowResult(
            recipe_name=match.recipe_name,
            status="completed",
            response_text=f"Opened {label} and searched for \"{query}\".",
            progress_lines=progress,
            steps=steps,
            state_update=browser_task_state_update(
                active_task=self._build_workflow_state(
                    recipe_name=match.recipe_name,
                    site_name=self._site_from_url(page.url) or site_name,
                    query=query,
                    target_url=page.url,
                    execution_mode=execution_mode,
                )
            ),
            payload={"tab_id": self.runtime.current_tab_id, "site_name": site_name, "query": query, "execution_mode": execution_mode},
        )

    async def _run_site_login_then_continue(
        self,
        match: BrowserWorkflowMatch,
        *,
        user_id: str,
        task_state: dict[str, Any],
    ) -> BrowserWorkflowResult:
        site_name = (match.site_name or "").strip()
        target_url = self._resolve_target_url(match, site_name)
        execution_mode = self._desired_mode_for_match(match)
        if not site_name:
            return BrowserWorkflowResult(
                recipe_name=match.recipe_name,
                status="needs_followup",
                response_text="Tell me which site you want to log into.",
            )
        result = await self.tool_registry.dispatch(
            "browser_login",
            {"site_name": site_name, "profile_name": "default", "user_id": user_id, "url": target_url or ""},
        )
        progress = [f"Saved browser login for {site_name.title()}."]
        steps = [WorkflowPlanStep(name="login", detail=f"Log into {site_name.title()}.", status="completed")]
        await self._emit_step(user_id, match.recipe_name, "login", progress[-1])
        active_task = active_browser_task(task_state)
        if active_task and active_task.get("site_name") == site_name and active_task.get("query"):
            resume_match = BrowserWorkflowMatch(
                recipe_name=str(active_task.get("recipe_name", "site_open_and_search")),
                confidence=0.99,
                site_name=site_name,
                query=(str(active_task.get("query", "")).strip() or None),
                action="search",
                details={"execution_mode_override": execution_mode},
            )
            resumed = await self._run_site_open_and_search(resume_match, user_id=user_id, task_state=task_state)
            resumed.progress_lines = progress + resumed.progress_lines
            resumed.steps = steps + resumed.steps
            return resumed
        return BrowserWorkflowResult(
            recipe_name=match.recipe_name,
            status="completed",
            response_text=(
                f"Saved browser profile '{result.get('profile_name', 'default')}' for {result.get('site_name', site_name)}."
            ),
            progress_lines=progress,
            steps=steps,
            state_update=browser_task_state_update(
                active_task=self._build_workflow_state(
                    recipe_name="site_open_and_search",
                    site_name=site_name,
                    query="",
                    target_url=target_url or result.get("url", ""),
                    execution_mode="headed",
                )
            ),
            payload={"site_name": site_name, "profile_name": result.get("profile_name", "default"), "execution_mode": "headed"},
        )

    async def _run_continue_last_task(
        self,
        match: BrowserWorkflowMatch,
        *,
        user_id: str,
        task_state: dict[str, Any],
    ) -> BrowserWorkflowResult:
        state = normalize_browser_task_state(task_state or match.details.get("task_state"))
        active_task = dict(state.get("active_task") or {})
        pending_action = dict(state.get("pending_confirmation") or {})
        runtime_pending_action = self.runtime.pending_protected_action() if hasattr(self.runtime, "pending_protected_action") else None
        if runtime_pending_action:
            pending_action = runtime_pending_action
        pending_login = dict(state.get("pending_login") or {})
        action = (match.action or "").strip().lower()
        if action == "cancel" and pending_action:
            cancelled = await self.runtime.cancel_pending_action(user_id=user_id) if runtime_pending_action else pending_action
            return BrowserWorkflowResult(
                recipe_name=match.recipe_name,
                status="completed",
                response_text=f"Cancelled the pending browser {cancelled.get('action_type', 'action')}.",
                progress_lines=["Cancelled the pending protected browser action."],
                steps=[WorkflowPlanStep(name="cancel", detail="Cancel the pending protected browser action.", status="completed")],
                clear_state=True,
                payload={"cancelled_action": cancelled},
            )
        if action == "confirm" and pending_action:
            confirmed = await self.runtime.confirm_pending_action(user_id=user_id) if runtime_pending_action else pending_action
            return BrowserWorkflowResult(
                recipe_name=match.recipe_name,
                status="completed",
                response_text=f"Confirmed the pending browser {confirmed.get('action_type', 'action')}.",
                progress_lines=["Confirmed the pending protected browser action."],
                steps=[WorkflowPlanStep(name="confirm", detail="Confirm the pending protected browser action.", status="completed")],
                clear_state=True,
                payload={"confirmed_action": confirmed},
            )
        if not active_task and not pending_action and not pending_login:
            return BrowserWorkflowResult(
                recipe_name=match.recipe_name,
                status="needs_followup",
                response_text="There isn't a browser task waiting to continue right now.",
                clear_state=True,
            )
        expected_site = str(active_task.get("active_site", "") or active_task.get("site_name", "")).strip()
        expected_url = str(active_task.get("target_url", "") or active_task.get("active_url", "")).strip() or None
        preferred_mode = "headed" if (pending_login or pending_action) else str(active_task.get("execution_mode", "")).strip().lower() or None
        if expected_site or expected_url:
            await self.runtime.switch_to_matching_tab(
                target_url=expected_url,
                site_name=expected_site or None,
                prefer_mode=preferred_mode,
                user_id=user_id,
            )
        current_site = infer_site_from_runtime(self.runtime.current_state())
        if current_site and expected_site and not self._sites_match(expected_site, current_site):
            if pending_login:
                if expected_url:
                    await self.runtime.open_visible_intervention(expected_url, user_id=user_id)
                return BrowserWorkflowResult(
                    recipe_name=match.recipe_name,
                    status="blocked",
                    response_text=(
                        f"I'm still waiting for the {expected_site} login in the visible browser window on the host machine. "
                        'Finish it there, then say "continue" again.'
                    ),
                    state_update=browser_task_state_update(
                        active_task=active_task,
                        pending_login=pending_login,
                    ),
                    payload={"blocking_reason": "login"},
                )
            return BrowserWorkflowResult(
                recipe_name=match.recipe_name,
                status="needs_followup",
                response_text=(
                    f"The previous browser task was for {expected_site}, but the current tab is on {current_site}. "
                    "Tell me which site you want to continue."
                ),
                state_update=browser_task_state_update(
                    active_task=active_task,
                    pending_confirmation=pending_action or None,
                    pending_login=pending_login or None,
                ),
            )
        finalized_login = await self.runtime.finalize_pending_login_if_complete(user_id=user_id)
        if finalized_login is not None:
            active_task["site_name"] = str(finalized_login.get("site_name", active_task.get("site_name", "")))
            active_task["blocked_reason"] = ""
            active_task["awaiting_followup"] = ""
            pending_login = {}
        elif pending_login or active_task.get("blocked_reason") in {"login_required", "login"}:
            return BrowserWorkflowResult(
                recipe_name=match.recipe_name,
                status="blocked",
                response_text="The login still looks incomplete. Finish it in the visible browser window on the host machine, then say \"continue\" again.",
                state_update=browser_task_state_update(
                    active_task=active_task,
                    pending_login=pending_login or {
                        "site_name": str(active_task.get("site_name", "")),
                        "target_url": str(active_task.get("target_url", "")),
                        "execution_mode": str(active_task.get("execution_mode", "headed")),
                    },
                ),
                payload={"blocking_reason": active_task.get("blocked_reason", "login")},
            )
        if pending_action and str(active_task.get("awaiting_followup", "")).strip().lower() == "confirmation":
            return BrowserWorkflowResult(
                recipe_name=match.recipe_name,
                status="blocked",
                response_text="The protected browser action is ready. Say \"confirm\" to continue or \"cancel\" to stop it.",
                state_update=browser_task_state_update(
                    active_task=active_task,
                    pending_confirmation=pending_action,
                ),
                payload={"blocking_reason": "confirmation_required", "pending_action": pending_action},
            )
        page = await self.runtime.get_page(
            user_id=user_id,
            headless=self._mode_headless(str(active_task.get("execution_mode", self._desired_mode_for_match(match))) or "headless"),
        )
        if match.open_first_result or active_task.get("blocked_reason") == "missing_result":
            query = str(active_task.get("query", "") or match.query or "").strip()
            results = await self.runtime.extract_search_results(
                page,
                site_name=(str(active_task.get("site_name", "")).strip() or None),
                max_results=self.config.browser_workflows.max_results_to_rank,
            )
            if results:
                chosen = await self.runtime.click_best_match(
                    page,
                    query,
                    results,
                    site_name=(str(active_task.get("site_name", "")).strip() or None),
                    open_first_result=bool(match.open_first_result or active_task.get("open_first_result")),
                    timeout_seconds=30,
                )
                await self.runtime.refresh_active_tab(user_id)
                return BrowserWorkflowResult(
                    recipe_name=match.recipe_name,
                    status="completed",
                    response_text=f"Continued the browser task and opened {chosen.get('title', 'the selected result')}.",
                    progress_lines=[f"Opened {chosen.get('title', 'the selected result')}."],
                    steps=[WorkflowPlanStep(name="continue", detail="Continue the previous browser workflow.", status="completed")],
                    state_update=browser_task_state_update(
                        active_task={**active_task, "awaiting_followup": "", "blocked_reason": "", "target_url": page.url, "active_url": page.url}
                    ),
                    payload={"tab_id": self.runtime.current_tab_id, "site_name": active_task.get("site_name", "")},
                )
        resume_match = BrowserWorkflowMatch(
            recipe_name=str(active_task.get("recipe_name", "site_open_and_search")),
            confidence=0.99,
            site_name=(str(active_task.get("site_name", "")).strip() or None),
            query=(str(active_task.get("query", "")).strip() or None),
            action="search" if active_task.get("query") else "open",
            open_first_result=bool(active_task.get("open_first_result")),
            details={"execution_mode_override": str(active_task.get("execution_mode", "")).strip() or None, "task_state": state},
        )
        if resume_match.recipe_name == "browser_continue_last_task":
            resume_match.recipe_name = "site_open_and_search"
        return await self.run_match(resume_match, user_id=user_id, session_key="", channel="browser", previous_state=state)

    async def _open_site(self, site_name: str, *, user_id: str, target_url: str | None = None, headless: bool | None = None):
        url = target_url or SITE_URLS.get(site_name, "")
        if not url:
            url = site_name if site_name.startswith("http") else f"https://{site_name}"
        page = await self.runtime.get_page(target_url=url, user_id=user_id, headless=headless)
        await page.goto(url, wait_until=self.runtime.wait_state_for_navigation("domcontentloaded"))
        await self.runtime.post_action_wait(page, "networkidle", 30)
        await self.runtime.refresh_active_tab(user_id)
        return page

    async def _detect_blocker(self, page: Any) -> BlockingState | None:
        payload = await self.runtime.detect_blocking_state(page)
        if not payload:
            return None
        return BlockingState(
            kind=str(payload.get("kind", "blocked")),
            message=str(payload.get("message", "The browser is blocked.")),
            url=payload.get("url"),
        )

    def _blocked_result(
        self,
        match: BrowserWorkflowMatch,
        blocked: BlockingState,
        progress: list[str],
        steps: list[WorkflowPlanStep],
    ) -> BrowserWorkflowResult:
        steps.append(WorkflowPlanStep(name="blocked", detail=blocked.message, status="blocked"))
        opened_visible = False
        try:
            if (
                self._runtime_current_mode() == "headless"
                and blocked.url
                and getattr(self.config.browser_execution, "headed_on_blockers", True)
            ):
                # Open the blocked page in a headed browser so the user can intervene.
                # This keeps Telegram/browser workflows usable even when the default runtime is headless.
                import asyncio

                loop = asyncio.get_running_loop()
                loop.create_task(self.runtime.open_visible_intervention(blocked.url, user_id=self.runtime.current_user_id))
                opened_visible = True
        except Exception:
            opened_visible = False
        state = browser_task_state_update(
            active_task=self._build_workflow_state(
                recipe_name=match.recipe_name,
                site_name=match.site_name,
                query=match.query,
                target_url=blocked.url or match.details.get("target_url"),
                execution_mode="headed" if opened_visible else self._desired_mode_for_match(match),
                blocked_reason=blocked.kind,
                blocked_url=blocked.url,
                awaiting_followup="continue",
            ),
            pending_login=(
                {
                    "site_name": match.site_name,
                    "target_url": blocked.url or match.details.get("target_url", ""),
                    "execution_mode": "headed" if opened_visible else self._desired_mode_for_match(match),
                }
                if blocked.kind == "login"
                else None
            ),
        )
        return BrowserWorkflowResult(
            recipe_name=match.recipe_name,
            status="blocked",
            response_text=(
                f"{blocked.message} "
                + (
                    "I opened a visible browser window on the host machine so you can clear it there. Say \"continue\" when you're done."
                    if opened_visible
                    else "Clear it in the visible browser on the host machine, then say \"continue\"."
                )
            ),
            progress_lines=progress,
            steps=steps,
            state_update=state,
            payload={"blocking_reason": blocked.kind, "url": blocked.url},
        )

    def _has_active_profile(self, site_name: str) -> bool:
        for session in self.runtime.list_sessions():
            candidate = str(session.get("site_name", "")).lower()
            if site_name in candidate and str(session.get("status", "active")) == "active":
                return True
        return False

    def _resolve_target_url(self, match: BrowserWorkflowMatch, site_name: str) -> str | None:
        explicit = str(match.details.get("target_url", "")).strip()
        if explicit:
            return explicit
        if site_name in SITE_URLS:
            return SITE_URLS[site_name]
        if site_name:
            return site_name if site_name.startswith("http") else f"https://{site_name}"
        return None

    def _site_from_url(self, url: str | None) -> str | None:
        if not url:
            return None
        parsed = urlparse(url)
        return parsed.netloc.strip().lower() or None

    def _site_label(self, site_name: str | None, current_url: str | None) -> str:
        return site_name or self._site_from_url(current_url) or "site"

    def _sites_match(self, expected: str, current: str) -> bool:
        expected_host = self._site_from_url(expected if expected.startswith("http") else f"https://{expected}") or expected.lower()
        current_host = self._site_from_url(current if current.startswith("http") else f"https://{current}") or current.lower()
        return expected_host == current_host

    def _desired_mode_for_match(self, match: BrowserWorkflowMatch) -> str:
        override = str(match.details.get("execution_mode_override", "")).strip().lower()
        if override in {"headless", "headed"}:
            return override
        if match.recipe_name in {"site_login_then_continue", "github_issue_compose"} or str(match.action or "").strip().lower() == "login":
            return "headed"
        if match.recipe_name == "browser_continue_last_task":
            task_state = normalize_browser_task_state(match.details.get("task_state"))
            active_task = active_browser_task(task_state)
            if task_state.get("pending_confirmation") or str(active_task.get("awaiting_followup", "")).strip().lower() == "confirmation":
                return "headed"
            if task_state.get("pending_login"):
                return "headed"
        return "headless"

    def _mode_headless(self, mode: str) -> bool:
        return mode != "headed"

    def _runtime_current_mode(self) -> str:
        getter = getattr(self.runtime, "current_mode", None)
        if callable(getter):
            return str(getter())
        return "headless" if bool(getattr(self.runtime, "current_headless", True)) else "headed"

    def _build_workflow_state(
        self,
        *,
        recipe_name: str,
        site_name: str | None,
        query: str | None,
        target_url: str | None,
        execution_mode: str,
        awaiting_followup: str = "",
        blocked_reason: str = "",
        **extra: Any,
    ) -> dict[str, Any]:
        resolved_url = str(target_url or "")
        active_site = self._site_from_url(resolved_url) or site_name or ""
        state = {
            "recipe_name": recipe_name,
            "site_name": site_name or active_site,
            "query": query or "",
            "target_url": resolved_url,
            "active_url": resolved_url,
            "active_site": active_site,
            "execution_mode": execution_mode,
            "blocked_reason": blocked_reason,
            "awaiting_followup": awaiting_followup,
            "tab_id": getattr(self.runtime, "current_tab_id", None),
            "mode": self._runtime_current_mode(),
        }
        state.update(extra)
        return state

    def _repo_from_url(self, url: str | None) -> tuple[str, str] | None:
        if not url:
            return None
        parsed = urlparse(url)
        if "github.com" not in parsed.netloc.lower():
            return None
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 2:
            return None
        return parts[0], parts[1]

    def _compact_repo_name(self, value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", value.lower())

    async def _resolve_github_repo_reference(self, match: BrowserWorkflowMatch) -> tuple[str, str, dict[str, Any]] | None:
        owner = str(match.details.get("owner", "")).strip()
        repo = str(match.details.get("repo", "")).strip()
        if owner and repo:
            return owner, repo, {"full_name": f"{owner}/{repo}", "html_url": f"https://github.com/{owner}/{repo}"}

        current_state = self.runtime.current_state() if self.runtime is not None else {}
        active_tab = current_state.get("active_tab") or {}
        url_repo = self._repo_from_url(str(active_tab.get("url", "")))
        if url_repo is not None:
            owner, repo = url_repo
            return owner, repo, {"full_name": f"{owner}/{repo}", "html_url": f"https://github.com/{owner}/{repo}"}

        repo_hint = str(match.details.get("repo_hint", "")).strip()
        if not hasattr(self.tool_registry, "dispatch"):
            return None
        try:
            repos_result = await self.tool_registry.dispatch("github_list_repos", {"limit": 50})
        except Exception:
            return None
        repositories = list(repos_result.get("repositories", [])) if isinstance(repos_result, dict) else []
        if not repositories:
            return None
        if not repo_hint or repo_hint.lower() == "this repo":
            first = repositories[0]
            full_name = str(first.get("full_name", ""))
            if "/" in full_name:
                owner, repo = full_name.split("/", 1)
                return owner, repo, first
            return None
        compact_hint = self._compact_repo_name(repo_hint)
        for item in repositories:
            full_name = str(item.get("full_name", ""))
            if "/" not in full_name:
                continue
            candidate_owner, candidate_repo = full_name.split("/", 1)
            if self._compact_repo_name(candidate_repo) == compact_hint or self._compact_repo_name(full_name) == compact_hint:
                return candidate_owner, candidate_repo, item
        return None

    async def _emit(self, user_id: str, event_name: str, payload: dict[str, Any]) -> None:
        emitter = getattr(self.runtime, "emit_workflow_event", None)
        if callable(emitter):
            await emitter(user_id, event_name, payload)

    async def _emit_step(self, user_id: str, recipe_name: str, step_name: str, message: str) -> None:
        await self._emit(
            user_id,
            "browser.workflow.step",
            {"recipe_name": recipe_name, "step_name": step_name, "message": message, "status": "running"},
        )
