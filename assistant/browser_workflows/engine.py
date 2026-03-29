"""Execution engine for autonomous browser workflows."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import asdict
import re
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

from assistant.browser_workflows.models import BlockingState, BrowserWorkflowMatch, BrowserWorkflowResult, WorkflowPlanStep
from assistant.browser_workflows.nlp import BrowserWorkflowNLP, infer_site_from_runtime, normalize_browser_target, normalize_site_name
from assistant.browser_workflows.recipes import LOGIN_FAVORING_SITES, RECIPES, SITE_LOGIN_URLS, SITE_URLS
from assistant.browser_workflows.state import active_browser_task, browser_task_state_update, normalize_browser_task_state
from assistant.utils.user_facing_errors import format_browser_exception

_CURRENT_BROWSER_CONNECTION_ID: ContextVar[str] = ContextVar("browser_workflow_connection_id", default="")


class BrowserWorkflowEngine:
    def __init__(
        self,
        config,
        tool_registry,
        *,
        chunk_emitter: Callable[[str, str, dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self.config = config
        self.tool_registry = tool_registry
        self.runtime = getattr(tool_registry, "browser_runtime", None)
        self.nlp = BrowserWorkflowNLP(config, tool_registry)
        # chunk_emitter(connection_id, event_name, payload) — emits live progress to chat UI
        self._chunk_emitter = chunk_emitter

    def available_workflows(self) -> list[dict[str, Any]]:
        return [asdict(recipe) for recipe in RECIPES]

    async def match_message(
        self,
        message: str,
        *,
        previous_state: dict[str, Any] | None = None,
        force: bool = False,
    ) -> BrowserWorkflowMatch | None:
        if not self.config.browser_workflows.enabled or self.runtime is None:
            return None
        runtime_state = self.runtime.current_state() if self.runtime is not None else {}
        task_state = normalize_browser_task_state(previous_state)
        return await self.nlp.match(
            message,
            runtime_state=runtime_state,
            previous_state=task_state,
            force=force,
        )

    async def maybe_run(
        self,
        message: str,
        *,
        user_id: str,
        session_key: str,
        channel: str,
        previous_state: dict[str, Any] | None = None,
        force: bool = False,
        connection_id: str | None = None,
    ) -> BrowserWorkflowResult | None:
        task_state = normalize_browser_task_state(previous_state)
        match = await self.match_message(message, previous_state=task_state, force=force)
        if match is None:
            return None
        if match.action == "disambiguate" or (
            bool(match.details.get("needs_disambiguation"))
            and not bool(match.details.get("disambiguation_confirmed"))
        ):
            return self._disambiguation_result(match, task_state)
        return await self.run_match(
            match,
            user_id=user_id,
            session_key=session_key,
            channel=channel,
            previous_state=previous_state,
            connection_id=connection_id,
        )

    async def run_match(
        self,
        match: BrowserWorkflowMatch,
        *,
        user_id: str,
        session_key: str,
        channel: str,
        previous_state: dict[str, Any] | None = None,
        connection_id: str | None = None,
    ) -> BrowserWorkflowResult:
        task_state = normalize_browser_task_state(previous_state or match.details.get("task_state"))
        active_task = active_browser_task(task_state)
        token = _CURRENT_BROWSER_CONNECTION_ID.set(str(connection_id or ""))
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
            # ── Phase B / D new recipes ───────────────────────────────────
            elif match.recipe_name == "generic_page_interact":
                result = await self._run_generic_page_interact(match, user_id=user_id, task_state=task_state)
            elif match.recipe_name == "page_read_summarize":
                result = await self._run_page_read_summarize(match, user_id=user_id, task_state=task_state)
            elif match.recipe_name == "multi_tab_research":
                result = await self._run_multi_tab_research(match, user_id=user_id, task_state=task_state)
            elif match.recipe_name == "twitter_search_scroll":
                result = await self._run_twitter_search_scroll(match, user_id=user_id, task_state=task_state)
            elif match.recipe_name == "reddit_search_open":
                result = await self._run_reddit_search_open(match, user_id=user_id, task_state=task_state)
            elif match.recipe_name == "amazon_search_buy":
                result = await self._run_amazon_search_buy(match, user_id=user_id, task_state=task_state)
            elif match.recipe_name == "train_search_book":
                result = await self._run_train_search_book(match, user_id=user_id, task_state=task_state)
            elif match.recipe_name == "flight_search_book":
                result = await self._run_flight_search_book(match, user_id=user_id, task_state=task_state)
            elif match.recipe_name == "food_search_order":
                result = await self._run_food_search_order(match, user_id=user_id, task_state=task_state)
            elif match.recipe_name == "bill_payment_review":
                result = await self._run_bill_payment_review(match, user_id=user_id, task_state=task_state)
            elif match.recipe_name == "web_form_fill_submit":
                result = await self._run_web_form_fill_submit(match, user_id=user_id, task_state=task_state)
            elif match.recipe_name == "calendar_book":
                result = await self._run_calendar_book(match, user_id=user_id, task_state=task_state)
            elif match.recipe_name == "email_compose_send":
                result = await self._run_email_compose_send(match, user_id=user_id, task_state=task_state)
            elif match.recipe_name == "youtube_media_control":
                result = await self._run_youtube_media_control(match, user_id=user_id, task_state=task_state)
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
                    pending_otp=task_state.get("pending_otp"),
                    pending_captcha=task_state.get("pending_captcha"),
                    pending_disambiguation=task_state.get("pending_disambiguation"),
                ),
                payload={"raw_error": str(exc)},
            )
        finally:
            _CURRENT_BROWSER_CONNECTION_ID.reset(token)
        event_name = "browser.workflow.completed" if result.status == "completed" else "browser.workflow.blocked"
        await self._emit(
            user_id,
            event_name,
            {"recipe_name": result.recipe_name, "status": result.status, "response_text": result.response_text, **result.payload},
        )
        return result

    def _disambiguation_result(
        self,
        match: BrowserWorkflowMatch,
        task_state: dict[str, Any],
    ) -> BrowserWorkflowResult:
        label = self._describe_disambiguation_match(match)
        pending = {
            "recipe_name": match.recipe_name,
            "site_name": match.site_name or "",
            "query": match.query or "",
            "action": match.details.get("intended_action", match.action or ""),
            "open_first_result": bool(match.open_first_result),
            "details": {key: value for key, value in dict(match.details).items() if key != "task_state"},
        }
        return BrowserWorkflowResult(
            recipe_name=match.recipe_name,
            status="needs_followup",
            response_text=f'Did you mean: {label}? Reply "yes" to continue or tell me the browser task more specifically.',
            state_update=browser_task_state_update(
                active_task=active_browser_task(task_state),
                pending_confirmation=dict(task_state.get("pending_confirmation") or {}),
                pending_login=dict(task_state.get("pending_login") or {}),
                pending_otp=dict(task_state.get("pending_otp") or {}),
                pending_captcha=dict(task_state.get("pending_captcha") or {}),
                pending_disambiguation=pending,
                next_task_mode_override=str(task_state.get("next_task_mode_override", "") or ""),
            ),
            payload={"disambiguation": pending},
        )

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
        search_box, _strategy = await self._find_search_input(page, site_name="youtube")
        await self._step_with_watchdog(search_box.fill(query))
        await self._step_with_watchdog(self.runtime.press_key(page, "Enter"))
        await self._step_with_watchdog(self.runtime.post_action_wait(page, "networkidle", 30))
        await self._step_with_watchdog(self.runtime.refresh_active_tab(user_id))
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
        opened_video = await self._step_with_watchdog(
            self.runtime.wait_for_url_match(page, r"(youtube\.com/(watch|shorts)|youtu\.be/)", timeout_seconds=10)
        )
        if not opened_video:
            return BrowserWorkflowResult(
                recipe_name=match.recipe_name,
                status="needs_followup",
                response_text=(
                    f'I searched YouTube for "{query}" and clicked the best match, but the tab did not reach a watch page. '
                    'The browser is still open on the host machine. Try "continue" or tell me to click a specific result.'
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
                        blocked_reason="watch_page_not_opened",
                        awaiting_followup="continue",
                    )
                ),
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
        search_query = query if any(token in query.lower() for token in ("latest", "newest", "recent")) else f"{query} latest video"
        page = await self._open_site("youtube", user_id=user_id, headless=self._mode_headless(execution_mode))
        progress = ["Opened YouTube."]
        steps = [WorkflowPlanStep(name="open_site", detail="Open YouTube.", status="completed")]
        await self._emit_step(user_id, match.recipe_name, "open_site", progress[-1])
        blocked = await self._detect_blocker(page)
        if blocked:
            return self._blocked_result(match, blocked, progress, steps)
        search_box, _strategy = await self._find_search_input(page, site_name="youtube")
        await self._step_with_watchdog(search_box.fill(search_query))
        await self._step_with_watchdog(self.runtime.press_key(page, "Enter"))
        await self._step_with_watchdog(self.runtime.post_action_wait(page, "networkidle", 30))
        await self._step_with_watchdog(self.runtime.refresh_active_tab(user_id))
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
        opened_video = await self._step_with_watchdog(
            self.runtime.wait_for_url_match(page, r"(youtube\.com/(watch|shorts)|youtu\.be/)", timeout_seconds=10)
        )
        if not opened_video:
            return BrowserWorkflowResult(
                recipe_name=match.recipe_name,
                status="needs_followup",
                response_text=(
                    f'I searched YouTube for the latest video from "{query}" and clicked the top match, '
                    "but the tab did not reach a watch page."
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
                        blocked_reason="watch_page_not_opened",
                        awaiting_followup="continue",
                    )
                ),
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
        search_box, _strategy = await self._find_search_input(page, site_name="google")
        await self._step_with_watchdog(search_box.fill(query))
        await self._step_with_watchdog(self.runtime.press_key(page, "Enter"))
        await self._step_with_watchdog(self.runtime.post_action_wait(page, "networkidle", 30))
        await self._step_with_watchdog(self.runtime.refresh_active_tab(user_id))
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
        preview = self._maybe_preview_workflow_plan(
            match,
            task_state,
            execution_mode="headed",
            target_url=target_url,
            steps=[
                WorkflowPlanStep(name="open_issue_composer", detail=f"Open the GitHub issue composer for {owner}/{repo}."),
                WorkflowPlanStep(name="review_issue", detail="Pause in a visible browser window for review."),
                WorkflowPlanStep(name="submit_issue", detail="Wait for confirm before submitting the issue."),
            ],
            resume_details={"owner": owner, "repo": repo},
        )
        if preview is not None:
            return preview
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
        search_box, _strategy = await self._find_search_input(page, site_name=site_name)
        await self._step_with_watchdog(search_box.fill(query))
        await self._step_with_watchdog(self.runtime.press_key(page, "Enter"))
        await self._step_with_watchdog(self.runtime.post_action_wait(page, "networkidle", 30))
        await self._step_with_watchdog(self.runtime.refresh_active_tab(user_id))
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
        if active_task and self._sites_match(str(active_task.get("site_name", "")), site_name):
            resume_match = BrowserWorkflowMatch(
                recipe_name=str(active_task.get("recipe_name", "site_open_and_search")),
                confidence=0.99,
                site_name=site_name,
                query=(str(active_task.get("query", "")).strip() or None),
                action=str(active_task.get("action", "")).strip() or ("search" if active_task.get("query") else "open"),
                open_first_result=bool(active_task.get("open_first_result")),
                details={
                    "execution_mode_override": execution_mode,
                    "target_url": str(active_task.get("target_url", "")).strip() or None,
                    "task_state": task_state,
                },
            )
            resumed = await self.run_match(
                resume_match,
                user_id=user_id,
                session_key="",
                channel="browser",
                previous_state=task_state,
                connection_id=_CURRENT_BROWSER_CONNECTION_ID.get(""),
            )
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
        awaiting_followup = str(active_task.get("awaiting_followup", "")).strip().lower()
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
        if action == "cancel" and awaiting_followup == "workflow_plan":
            return BrowserWorkflowResult(
                recipe_name=match.recipe_name,
                status="completed",
                response_text="Cancelled the pending browser workflow plan.",
                progress_lines=["Cancelled the pending browser workflow plan."],
                steps=[WorkflowPlanStep(name="cancel", detail="Cancel the pending browser workflow plan.", status="completed")],
                clear_state=True,
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
        if str(active_task.get("awaiting_followup", "")).strip().lower() == "workflow_plan":
            resume_details = dict(active_task.get("resume_details") or {})
            resume_match = BrowserWorkflowMatch(
                recipe_name=str(active_task.get("recipe_name", "site_open_and_search")),
                confidence=0.99,
                site_name=(str(active_task.get("site_name", "")).strip() or None),
                query=(str(active_task.get("query", "")).strip() or None),
                action=str(active_task.get("action", "")).strip() or None,
                open_first_result=bool(active_task.get("open_first_result")),
                details={
                    **resume_details,
                    "execution_mode_override": str(active_task.get("execution_mode", "")).strip() or None,
                    "task_state": {**state, "pending_disambiguation": {}},
                    "workflow_plan_confirmed": True,
                },
            )
            return await self.run_match(
                resume_match,
                user_id=user_id,
                session_key="",
                channel="browser",
                previous_state={**state, "pending_disambiguation": {}},
                connection_id=_CURRENT_BROWSER_CONNECTION_ID.get(""),
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
            details={
                "execution_mode_override": str(active_task.get("execution_mode", "")).strip() or None,
                "task_state": state,
                "target_url": str(active_task.get("target_url", "")).strip() or None,
            },
        )
        if resume_match.recipe_name == "browser_continue_last_task":
            resume_match.recipe_name = "site_open_and_search"
        return await self.run_match(
            resume_match,
            user_id=user_id,
            session_key="",
            channel="browser",
            previous_state=state,
            connection_id=_CURRENT_BROWSER_CONNECTION_ID.get(""),
        )

    async def _open_site(self, site_name: str, *, user_id: str, target_url: str | None = None, headless: bool | None = None):
        url = target_url or SITE_URLS.get(site_name, "")
        if not url:
            url = site_name if site_name.startswith("http") else f"https://{site_name}"
        page = await self._step_with_watchdog(self.runtime.get_page(target_url=url, user_id=user_id, headless=headless))
        await self._step_with_watchdog(page.goto(url, wait_until=self.runtime.wait_state_for_navigation("domcontentloaded")))
        await self._step_with_watchdog(self.runtime.post_action_wait(page, "networkidle", 30))
        # Auto-dismiss consent/cookie banners after navigation
        auto_dismiss = getattr(self.runtime, "auto_dismiss_consent", None)
        if getattr(self.config.browser_workflows, "auto_dismiss_consent", True) and callable(auto_dismiss):
            await self._step_with_watchdog(auto_dismiss(page, site_name=site_name))
        await self._step_with_watchdog(self.runtime.refresh_active_tab(user_id))
        return page

    async def _detect_blocker(self, page: Any) -> BlockingState | None:
        payload = await self._step_with_watchdog(self.runtime.detect_blocking_state(page))
        if payload:
            kind = str(payload.get("kind", "blocked"))
            if kind == "captcha":
                solver = getattr(self.runtime, "solve_captcha_with_vision", None)
                model_provider = getattr(self.tool_registry, "model_provider", None)
                if callable(solver) and model_provider is not None:
                    solved = await self._step_with_watchdog(solver(page, model_provider))
                    if isinstance(solved, dict) and str(solved.get("status", "")).lower() == "solved":
                        await self.runtime.set_pending_captcha(
                            site_name=self._site_from_url(str(getattr(page, "url", "") or "")) or "site",
                            prompt="I solved a simple CAPTCHA automatically and tried to continue.",
                            target_url=str(getattr(page, "url", "") or ""),
                            screenshot_path=str(solved.get("screenshot_path", "")),
                            user_id=getattr(self.runtime, "current_user_id", None),
                        )
                        await self.runtime.submit_pending_captcha(
                            str(solved.get("answer", "")),
                            user_id=getattr(self.runtime, "current_user_id", None),
                        )
                        await self.runtime.refresh_active_tab(getattr(self.runtime, "current_user_id", None))
                        return None
                    if isinstance(solved, dict) and str(solved.get("screenshot_path", "")).strip():
                        payload["screenshot_path"] = str(solved.get("screenshot_path", "")).strip()
            return BlockingState(
                kind=kind,
                message=str(payload.get("message", "The browser is blocked.")),
                url=payload.get("url"),
                details=dict(payload),
            )
        # Phase B: vision-assisted fallback
        vision_detect = getattr(self.runtime, "vision_detect_blocking_state", None)
        if getattr(self.config.browser_workflows, "vision_check_enabled", False) and callable(vision_detect):
            model_provider = getattr(self.tool_registry, "model_provider", None)
            vision_payload = await self._step_with_watchdog(vision_detect(page, model_provider))
            if vision_payload:
                return BlockingState(
                    kind=str(vision_payload.get("kind", "blocked")),
                    message=str(vision_payload.get("message", "Vision-detected blocker on page.")),
                    url=vision_payload.get("url"),
                    details=dict(vision_payload),
                )
        detect_otp = getattr(self.runtime, "detect_otp_requirement", None)
        if callable(detect_otp):
            otp_payload = await self._step_with_watchdog(detect_otp(page))
            if otp_payload:
                return BlockingState(
                    kind="otp",
                    message=str(otp_payload.get("prompt", "An OTP is required to continue.")),
                    url=str(otp_payload.get("url", "") or getattr(page, "url", "") or ""),
                    details=dict(otp_payload),
                )
        return None

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
        pending_login = None
        pending_otp = None
        pending_captcha = None
        response_text = blocked.message
        if blocked.kind == "login":
            pending_login = {
                "site_name": match.site_name,
                "target_url": blocked.url or match.details.get("target_url", ""),
                "execution_mode": "headed" if opened_visible else self._desired_mode_for_match(match),
            }
            response_text = (
                f"{blocked.message} "
                + (
                    "I opened a visible browser window on the host machine so you can clear it there. Say \"continue\" when you're done."
                    if opened_visible
                    else "Clear it in the visible browser on the host machine, then say \"continue\"."
                )
            )
        elif blocked.kind == "otp":
            set_pending_otp = getattr(self.runtime, "set_pending_otp", None)
            if callable(set_pending_otp):
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(
                        set_pending_otp(
                            site_name=match.site_name or self._site_from_url(blocked.url),
                            selector=str(blocked.details.get("selector", "")).strip(),
                            prompt=str(blocked.details.get("prompt", "") or blocked.message),
                            target_url=blocked.url or str(match.details.get("target_url", "") or ""),
                            user_id=self.runtime.current_user_id,
                        )
                    )
                except Exception:
                    pass
            pending_otp = {
                "site_name": match.site_name,
                "target_url": blocked.url or match.details.get("target_url", ""),
                "execution_mode": "headed" if opened_visible else self._desired_mode_for_match(match),
                "prompt": str(blocked.details.get("prompt", "") or blocked.message),
                "selector": str(blocked.details.get("selector", "")).strip(),
            }
            response_text = f'{blocked.message} Reply with the OTP code to continue.'
        elif blocked.kind == "captcha":
            set_pending_captcha = getattr(self.runtime, "set_pending_captcha", None)
            if callable(set_pending_captcha):
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(
                        set_pending_captcha(
                            site_name=match.site_name or self._site_from_url(blocked.url),
                            prompt="Please solve the CAPTCHA and reply with the answer.",
                            target_url=blocked.url or str(match.details.get("target_url", "") or ""),
                            screenshot_path=str(blocked.details.get("screenshot_path", "")),
                            user_id=self.runtime.current_user_id,
                        )
                    )
                except Exception:
                    pass
            pending_captcha = {
                "site_name": match.site_name,
                "target_url": blocked.url or match.details.get("target_url", ""),
                "execution_mode": "headed" if opened_visible else self._desired_mode_for_match(match),
                "prompt": "Please solve the CAPTCHA and reply with the answer.",
                "screenshot_path": str(blocked.details.get("screenshot_path", "")),
            }
            response_text = (
                "The browser is blocked by a CAPTCHA or human-verification challenge. "
                "Reply with the CAPTCHA answer to continue."
            )
            if str(blocked.details.get("screenshot_path", "")).strip():
                response_text += f"\nCAPTCHA screenshot: {blocked.details['screenshot_path']}"
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
                otp_prompt=str(blocked.details.get("prompt", "")),
                captcha_prompt="Please solve the CAPTCHA and reply with the answer.",
                captcha_screenshot_path=str(blocked.details.get("screenshot_path", "")),
            ),
            pending_login=pending_login,
            pending_otp=pending_otp,
            pending_captcha=pending_captcha,
        )
        return BrowserWorkflowResult(
            recipe_name=match.recipe_name,
            status="blocked",
            response_text=response_text,
            progress_lines=progress,
            steps=steps,
            state_update=state,
            payload={"blocking_reason": blocked.kind, "url": blocked.url, **dict(blocked.details)},
        )

    def _has_active_profile(self, site_name: str) -> bool:
        for session in self.runtime.list_sessions():
            candidate = str(session.get("site_name", "")).lower()
            if site_name in candidate and str(session.get("status", "active")) == "active":
                return True
        return False

    def _resolve_target_url(self, match: BrowserWorkflowMatch, site_name: str) -> str | None:
        explicit_raw = match.details.get("target_url")
        explicit = str(explicit_raw or "").strip()
        if explicit and explicit.lower() not in {"none", "null"}:
            return explicit
        normalized_site, normalized_url = normalize_browser_target(site_name)
        canonical_site = normalize_site_name(site_name) or normalized_site or site_name
        if canonical_site in SITE_URLS:
            return SITE_URLS[canonical_site]
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
        expected_canonical = normalize_site_name(expected) or normalize_browser_target(expected)[0] or expected.lower()
        current_canonical = normalize_site_name(current) or normalize_browser_target(current)[0] or current.lower()
        if expected_canonical == current_canonical:
            return True
        expected_host = self._site_from_url(expected if expected.startswith("http") else f"https://{expected}") or expected_canonical
        current_host = self._site_from_url(current if current.startswith("http") else f"https://{current}") or current_canonical
        expected_host_canonical = normalize_site_name(expected_host) or expected_host
        current_host_canonical = normalize_site_name(current_host) or current_host
        if expected_host_canonical == current_host_canonical:
            return True
        return expected_host == current_host

    def _desired_mode_for_match(self, match: BrowserWorkflowMatch) -> str:
        override = str(match.details.get("execution_mode_override", "")).strip().lower()
        if override in {"headless", "headed"}:
            return override
        task_state = normalize_browser_task_state(match.details.get("task_state"))
        active_task = active_browser_task(task_state)
        active_execution_mode = str(active_task.get("execution_mode", "")).strip().lower()
        active_site = str(active_task.get("site_name", "") or active_task.get("active_site", "")).strip()
        match_site = str(match.site_name or "").strip()
        if match.recipe_name in {"site_login_then_continue", "github_issue_compose"} or str(match.action or "").strip().lower() == "login":
            return "headed"
        if match.recipe_name == "browser_continue_last_task":
            if task_state.get("pending_confirmation") or str(active_task.get("awaiting_followup", "")).strip().lower() == "confirmation":
                return "headed"
            if task_state.get("pending_login"):
                return "headed"
        if active_execution_mode in {"headless", "headed"} and active_site:
            if not match_site or self._sites_match(active_site, match_site):
                return active_execution_mode
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

    async def _emit_step(
        self,
        user_id: str,
        recipe_name: str,
        step_name: str,
        message: str,
        *,
        connection_id: str | None = None,
    ) -> None:
        await self._emit(
            user_id,
            "browser.workflow.step",
            {"recipe_name": recipe_name, "step_name": step_name, "message": message, "status": "running"},
        )
        # Live progress chunk to the chat UI (Phase A)
        resolved_connection_id = connection_id or _CURRENT_BROWSER_CONNECTION_ID.get("")
        if getattr(self.config.browser_workflows, "live_progress_chunks", True) and self._chunk_emitter is not None and resolved_connection_id:
            try:
                await self._chunk_emitter(resolved_connection_id, "agent.chunk", {"text": f"🔄 {message}"})
            except Exception:
                pass

    async def _step_with_watchdog(self, coro: Any) -> Any:
        """Run *coro* with a per-step timeout from config. Raises asyncio.TimeoutError on breach."""
        timeout = int(getattr(self.config.browser_execution, "step_timeout_seconds", 0) or 0)
        if timeout > 0:
            return await asyncio.wait_for(coro, timeout=timeout)
        return await coro

    async def _find_search_input(self, page: Any, *, site_name: str | None = None) -> tuple[Any, str]:
        try:
            return await self._step_with_watchdog(self.runtime.find_search_input(page, site_name=site_name))
        except Exception as exc:
            model_provider = getattr(self.tool_registry, "model_provider", None)
            retry_with_hint = getattr(self.runtime, "retry_with_screenshot_hint", None)
            if not callable(retry_with_hint):
                raise
            hinted = await retry_with_hint(page, str(exc), model_provider)
            if hinted is not None:
                return hinted
            raise

    def _describe_disambiguation_match(self, match: BrowserWorkflowMatch) -> str:
        query = str(match.query or "").strip()
        site_name = str(match.site_name or "").strip()
        if match.recipe_name == "google_search_open" and query:
            return f'search Google for "{query}"'
        if match.recipe_name == "youtube_search_play" and query:
            return f'play "{query}" on YouTube'
        if match.recipe_name == "youtube_latest_video" and query:
            return f'open the latest "{query}" video on YouTube'
        if match.recipe_name == "page_read_summarize" and query:
            return f"open and summarize {query}"
        if query and site_name:
            return f'{match.recipe_name.replace("_", " ")} on {site_name} for "{query}"'
        if query:
            return f'{match.recipe_name.replace("_", " ")} for "{query}"'
        if site_name:
            return f'{match.recipe_name.replace("_", " ")} on {site_name}'
        return match.recipe_name.replace("_", " ")

    def _format_workflow_plan(self, steps: list[WorkflowPlanStep]) -> str:
        lines = ["Plan preview:"]
        for index, step in enumerate(steps, start=1):
            lines.append(f"{index}. {step.detail}")
        lines.append('Reply "yes" to continue or "cancel" to stop.')
        return "\n".join(lines)

    def _maybe_preview_workflow_plan(
        self,
        match: BrowserWorkflowMatch,
        task_state: dict[str, Any],
        *,
        execution_mode: str,
        target_url: str | None,
        steps: list[WorkflowPlanStep],
        resume_details: dict[str, Any] | None = None,
    ) -> BrowserWorkflowResult | None:
        if not getattr(self.config.browser_workflows, "ask_before_high_impact", True):
            return None
        if bool(match.details.get("workflow_plan_confirmed")):
            return None
        return BrowserWorkflowResult(
            recipe_name=match.recipe_name,
            status="needs_followup",
            response_text=self._format_workflow_plan(steps),
            steps=steps,
            state_update=browser_task_state_update(
                active_task=self._build_workflow_state(
                    recipe_name=match.recipe_name,
                    site_name=match.site_name,
                    query=match.query,
                    target_url=target_url,
                    execution_mode=execution_mode,
                    awaiting_followup="workflow_plan",
                    resume_details=resume_details or {},
                    action=match.action or "",
                    open_first_result=bool(match.open_first_result),
                ),
                pending_confirmation=dict(task_state.get("pending_confirmation") or {}),
                pending_login=dict(task_state.get("pending_login") or {}),
                pending_disambiguation=dict(task_state.get("pending_disambiguation") or {}),
            ),
            payload={"workflow_plan": [asdict(step) for step in steps]},
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Phase B — generic_page_interact
    # ──────────────────────────────────────────────────────────────────────────

    async def _run_generic_page_interact(
        self,
        match: BrowserWorkflowMatch,
        *,
        user_id: str,
        task_state: dict[str, Any],
    ) -> BrowserWorkflowResult:
        target_url = str(match.details.get("target_url", "") or match.query or "").strip()
        if not target_url:
            return BrowserWorkflowResult(
                recipe_name=match.recipe_name,
                status="needs_followup",
                response_text="Tell me which URL you want me to open and interact with.",
            )
        if not target_url.startswith("http"):
            target_url = f"https://{target_url}"
        execution_mode = self._desired_mode_for_match(match)
        page = await self._open_site(target_url, user_id=user_id, target_url=target_url, headless=self._mode_headless(execution_mode))
        progress = [f"Opened {target_url}."]
        steps = [WorkflowPlanStep(name="open_site", detail=f"Open {target_url}.", status="completed")]
        await self._emit_step(user_id, match.recipe_name, "open_site", progress[-1])
        blocked = await self._detect_blocker(page)
        if blocked:
            return self._blocked_result(match, blocked, progress, steps)
        summary = await self.runtime.page_summarize_rich(page)
        candidates = await self.runtime.list_clickable_candidates(page, limit=15)
        form_schema = await self.runtime.inspect_form(page)
        progress.append(
            f"Page has {len(candidates)} interactive elements, {summary.get('word_count', 0)} words, "
            f"and {int(form_schema.get('form_count', 0))} form(s)."
        )
        await self._emit_step(user_id, match.recipe_name, "enumerate_elements", progress[-1])
        elements_text = "\n".join(
            f"  [{idx}] {'<' + item['tag'] + '>'} \"" + (item.get('text') or item.get('aria_label') or '') + '"'
            for idx, item in enumerate(candidates[:10])
        )
        return BrowserWorkflowResult(
            recipe_name=match.recipe_name,
            status="needs_followup",
            response_text=(
                f"Opened {target_url}. Here are the first interactive elements I found:\n{elements_text}\n"
                "Tell me what you'd like me to click or fill."
            ),
            progress_lines=progress,
            steps=steps,
            state_update=browser_task_state_update(
                active_task=self._build_workflow_state(
                    recipe_name=match.recipe_name,
                    site_name=self._site_from_url(page.url),
                    query=match.query,
                    target_url=page.url,
                    execution_mode=execution_mode,
                    awaiting_followup="site_action",
                )
            ),
            payload={
                "tab_id": self.runtime.current_tab_id,
                "page_summary": summary,
                "clickable_candidates": candidates[:10],
                "form_schema": form_schema,
            },
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Phase B — page_read_summarize
    # ──────────────────────────────────────────────────────────────────────────

    async def _run_page_read_summarize(
        self,
        match: BrowserWorkflowMatch,
        *,
        user_id: str,
        task_state: dict[str, Any],
    ) -> BrowserWorkflowResult:
        target_url = str(match.details.get("target_url", "") or match.query or "").strip()
        if not target_url:
            return BrowserWorkflowResult(
                recipe_name=match.recipe_name,
                status="needs_followup",
                response_text="Tell me the URL or page you want me to read and summarize.",
            )
        if not target_url.startswith("http"):
            target_url = f"https://{target_url}"
        execution_mode = self._desired_mode_for_match(match)
        page = await self._open_site(target_url, user_id=user_id, target_url=target_url, headless=self._mode_headless(execution_mode))
        progress = [f"Opened {target_url}."]
        steps = [WorkflowPlanStep(name="open_site", detail=f"Open {target_url}.", status="completed")]
        await self._emit_step(user_id, match.recipe_name, "open_site", progress[-1])
        blocked = await self._detect_blocker(page)
        if blocked:
            return self._blocked_result(match, blocked, progress, steps)
        summary = await self.runtime.page_summarize_rich(page)
        page_text = str(summary.get("text", "")).strip()[:6000]
        progress.append(f"Extracted {summary.get('word_count', 0)} words from the page.")
        await self._emit_step(user_id, match.recipe_name, "extract_text", progress[-1])
        # Ask the LLM to summarize the page content
        llm_summary = page_text
        try:
            llm_result = await self.tool_registry.dispatch(
                "llm_task",
                {
                    "prompt": (
                        f"Summarize the following web page content in 3-5 concise bullet points.\n"
                        f"Page title: {summary.get('title', 'Unknown')}\n"
                        f"URL: {target_url}\n\nContent:\n{page_text}"
                    ),
                    "model": "cheap",
                },
            )
            llm_summary = str(llm_result.get("content", page_text)).strip() or page_text
        except Exception:
            pass
        progress.append("Generated summary.")
        await self._emit_step(user_id, match.recipe_name, "summarize", progress[-1])
        return BrowserWorkflowResult(
            recipe_name=match.recipe_name,
            status="completed",
            response_text=f"**Summary of {summary.get('title', target_url)}**\n\n{llm_summary}",
            progress_lines=progress,
            steps=steps,
            state_update=browser_task_state_update(
                active_task=self._build_workflow_state(
                    recipe_name=match.recipe_name,
                    site_name=self._site_from_url(page.url),
                    query=target_url,
                    target_url=page.url,
                    execution_mode=execution_mode,
                )
            ),
            payload={"tab_id": self.runtime.current_tab_id, "title": summary.get("title", ""), "url": page.url},
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Phase B — multi_tab_research
    # ──────────────────────────────────────────────────────────────────────────

    async def _run_multi_tab_research(
        self,
        match: BrowserWorkflowMatch,
        *,
        user_id: str,
        task_state: dict[str, Any],
    ) -> BrowserWorkflowResult:
        raw_urls: list[str] = list(match.details.get("urls") or [])
        # Also extract URLs embedded directly in the query
        if not raw_urls and match.query:
            raw_urls = re.findall(r'https?://[^\s,;]+', match.query)
        if not raw_urls:
            return BrowserWorkflowResult(
                recipe_name=match.recipe_name,
                status="needs_followup",
                response_text="Tell me the URLs you want me to open and compare (separate them with commas or spaces).",
            )
        # Limit to 5 tabs at once
        urls = [u.strip().rstrip('"\',') for u in raw_urls if u.strip()][:5]
        execution_mode = self._desired_mode_for_match(match)
        headless = self._mode_headless(execution_mode)
        summaries: list[dict[str, Any]] = []
        progress: list[str] = []
        steps: list[WorkflowPlanStep] = []
        async def _summarize_target(index: int, raw_url: str) -> tuple[int, dict[str, Any], str]:
            url = raw_url
            if not url.startswith("http"):
                url = f"https://{url}"
            try:
                tab_result = await self.runtime.open_tab(url=url, user_id=user_id, headless=headless, timeout_seconds=30)
                tab_id = str(tab_result.get("tab_id", ""))
                page = await self.runtime.get_page(tab_id=tab_id, user_id=user_id, headless=headless)
                auto_dismiss = getattr(self.runtime, "auto_dismiss_consent", None)
                if getattr(self.config.browser_workflows, "auto_dismiss_consent", True) and callable(auto_dismiss):
                    await auto_dismiss(page)
                page_summary = await self.runtime.page_summarize_rich(page)
                summary = {"url": url, "title": page_summary.get("title", url), "text": page_summary.get("text", "")[:1500]}
                msg = f"Tab {index + 1}: Opened and read {page_summary.get('title', url)}."
            except Exception as exc:
                summary = {"url": url, "title": url, "text": f"Error: {exc}"}
                msg = f"Tab {index + 1}: Could not open {url}."
            return index, summary, msg

        results = await asyncio.gather(*(_summarize_target(i, url) for i, url in enumerate(urls)))
        for index, summary, msg in sorted(results, key=lambda item: item[0]):
            summaries.append(summary)
            progress.append(msg)
            steps.append(WorkflowPlanStep(name=f"open_tab_{index + 1}", detail=msg, status="completed"))
            await self._emit_step(user_id, match.recipe_name, f"open_tab_{index + 1}", msg)
        # Synthesize
        combined_text = "\n\n".join(
            f"[{s['title']}]({s['url']}):\n{s['text']}" for s in summaries
        )
        synthesis = combined_text
        try:
            llm_result = await self.tool_registry.dispatch(
                "llm_task",
                {
                    "prompt": (
                        f"You opened {len(summaries)} web pages. Compare and synthesize their key information.\n\n{combined_text}"
                    ),
                    "model": "cheap",
                },
            )
            synthesis = str(llm_result.get("content", combined_text)).strip() or combined_text
        except Exception:
            pass
        return BrowserWorkflowResult(
            recipe_name=match.recipe_name,
            status="completed",
            response_text=f"Opened {len(summaries)} tabs and synthesized their content:\n\n{synthesis}",
            progress_lines=progress,
            steps=steps,
            state_update=browser_task_state_update(
                active_task=self._build_workflow_state(
                    recipe_name=match.recipe_name,
                    site_name=None,
                    query=str(urls),
                    target_url=urls[0] if urls else "",
                    execution_mode=execution_mode,
                )
            ),
            payload={"tabs_opened": len(summaries), "urls": urls},
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Phase D — twitter_search_scroll
    # ──────────────────────────────────────────────────────────────────────────

    async def _run_twitter_search_scroll(
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
                response_text="Tell me what topic or hashtag you want me to search on Twitter/X.",
            )
        search_url = f"https://twitter.com/search?q={query.replace(' ', '%20')}&src=typed_query&f=live"
        page = await self._open_site("twitter", user_id=user_id, target_url=search_url, headless=self._mode_headless(execution_mode))
        progress = ["Opened Twitter/X search."]
        steps = [WorkflowPlanStep(name="open_site", detail="Open Twitter/X search.", status="completed")]
        await self._emit_step(user_id, match.recipe_name, "open_site", progress[-1])
        blocked = await self._detect_blocker(page)
        if blocked:
            return self._blocked_result(match, blocked, progress, steps)
        # Scroll 3 times to load more tweets
        for scroll_i in range(3):
            scroll_result = await self.runtime.scroll_page(page, direction="down", pixels=800)
            await self.runtime.post_action_wait(page, None, 2)
            if scroll_result.get("at_bottom"):
                break
        await self.runtime.refresh_active_tab(user_id)
        snapshot = await self.runtime.capture_dom_snapshot(page)
        progress.append(f'Scrolled Twitter/X search results for "{query}" and captured the feed.')
        steps.append(WorkflowPlanStep(name="scroll_feed", detail="Scroll the Twitter/X feed.", status="completed"))
        await self._emit_step(user_id, match.recipe_name, "scroll_feed", progress[-1])
        feed_text = str(snapshot.get("text", ""))[:2000]
        return BrowserWorkflowResult(
            recipe_name=match.recipe_name,
            status="completed",
            response_text=(f'Searched Twitter/X for "{query}" and scrolled through results.\n\n{feed_text}'),
            progress_lines=progress,
            steps=steps,
            state_update=browser_task_state_update(
                active_task=self._build_workflow_state(
                    recipe_name=match.recipe_name,
                    site_name="twitter",
                    query=query,
                    target_url=page.url,
                    execution_mode=execution_mode,
                )
            ),
            payload={"tab_id": self.runtime.current_tab_id, "site_name": "twitter", "query": query},
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Phase D — reddit_search_open
    # ──────────────────────────────────────────────────────────────────────────

    async def _run_reddit_search_open(
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
                response_text="Tell me what topic you want me to search on Reddit.",
            )
        search_url = f"https://www.reddit.com/search/?q={query.replace(' ', '+')}&sort=relevance"
        page = await self._open_site("reddit", user_id=user_id, target_url=search_url, headless=self._mode_headless(execution_mode))
        progress = ["Opened Reddit search."]
        steps = [WorkflowPlanStep(name="open_site", detail="Open Reddit search.", status="completed")]
        await self._emit_step(user_id, match.recipe_name, "open_site", progress[-1])
        blocked = await self._detect_blocker(page)
        if blocked:
            return self._blocked_result(match, blocked, progress, steps)
        results = await self.runtime.extract_search_results(page, max_results=8)
        if not results:
            snapshot = await self.runtime.capture_dom_snapshot(page)
            return BrowserWorkflowResult(
                recipe_name=match.recipe_name,
                status="completed",
                response_text=f'Searched Reddit for "{query}".\n\n{str(snapshot.get("text", ""))[:1500]}',
                progress_lines=progress,
                steps=steps,
                payload={"tab_id": self.runtime.current_tab_id, "site_name": "reddit"},
            )
        chosen = await self.runtime.click_best_match(page, query, results, site_name="reddit", timeout_seconds=30)
        await self.runtime.refresh_active_tab(user_id)
        progress.append(f"Opened top Reddit result: {chosen.get('title', 'post')}.")
        steps.append(WorkflowPlanStep(name="open_result", detail="Open the top Reddit result.", status="completed"))
        await self._emit_step(user_id, match.recipe_name, "open_result", progress[-1])
        return BrowserWorkflowResult(
            recipe_name=match.recipe_name,
            status="completed",
            response_text=f'Searched Reddit for "{query}" and opened the top result: {chosen.get("title", "post")}.',
            progress_lines=progress,
            steps=steps,
            state_update=browser_task_state_update(
                active_task=self._build_workflow_state(
                    recipe_name=match.recipe_name,
                    site_name="reddit",
                    query=query,
                    target_url=page.url,
                    execution_mode=execution_mode,
                    last_result_title=chosen.get("title", ""),
                )
            ),
            payload={"tab_id": self.runtime.current_tab_id, "site_name": "reddit", "query": query},
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Phase D — amazon_search_buy
    # ──────────────────────────────────────────────────────────────────────────

    async def _run_amazon_search_buy(
        self,
        match: BrowserWorkflowMatch,
        *,
        user_id: str,
        task_state: dict[str, Any],
    ) -> BrowserWorkflowResult:
        query = (match.query or "").strip()
        site_name = (match.site_name or "amazon").lower()
        if site_name not in {"amazon", "flipkart"}:
            site_name = "amazon"
        execution_mode = self._desired_mode_for_match(match)
        if not query:
            return BrowserWorkflowResult(
                recipe_name=match.recipe_name,
                status="needs_followup",
                response_text=f"Tell me what product you want me to search for on {site_name.title()}.",
            )
        preview = self._maybe_preview_workflow_plan(
            match,
            task_state,
            execution_mode=execution_mode,
            target_url=SITE_URLS.get(site_name, ""),
            steps=[
                WorkflowPlanStep(name="open_store", detail=f"Open {site_name.title()}."),
                WorkflowPlanStep(name="search_product", detail=f'Search for "{query}".'),
                WorkflowPlanStep(name="review_results", detail="Summarize the top product results."),
                WorkflowPlanStep(name="pause_before_purchase", detail="Pause before any purchase action."),
            ],
            resume_details={"site_name": site_name, "query": query},
        )
        if preview is not None:
            return preview
        page = await self._open_site(site_name, user_id=user_id, headless=self._mode_headless(execution_mode))
        progress = [f"Opened {site_name.title()}."]
        steps = [WorkflowPlanStep(name="open_site", detail=f"Open {site_name.title()}.", status="completed")]
        await self._emit_step(user_id, match.recipe_name, "open_site", progress[-1])
        blocked = await self._detect_blocker(page)
        if blocked:
            return self._blocked_result(match, blocked, progress, steps)
        search_box, _ = await self._find_search_input(page, site_name=site_name)
        await self._step_with_watchdog(search_box.fill(query))
        await self._step_with_watchdog(self.runtime.press_key(page, "Enter"))
        await self._step_with_watchdog(self.runtime.post_action_wait(page, "networkidle", 30))
        await self._step_with_watchdog(self.runtime.refresh_active_tab(user_id))
        progress.append(f'Searched {site_name.title()} for "{query}".')
        steps.append(WorkflowPlanStep(name="search", detail="Search for product.", status="completed"))
        await self._emit_step(user_id, match.recipe_name, "search", progress[-1])
        snapshot = await self.runtime.capture_dom_snapshot(page)
        results_text = str(snapshot.get("text", ""))[:2500]
        return BrowserWorkflowResult(
            recipe_name=match.recipe_name,
            status="blocked",
            response_text=(
                f'Searched {site_name.title()} for "{query}". Here are the top results:\n\n{results_text}\n\n'
                "I've paused before any purchase action. Say \"continue\" or \"confirm\" to proceed, or \"cancel\" to stop."
            ),
            progress_lines=progress,
            steps=steps,
            state_update=browser_task_state_update(
                active_task=self._build_workflow_state(
                    recipe_name=match.recipe_name,
                    site_name=site_name,
                    query=query,
                    target_url=page.url,
                    execution_mode=execution_mode,
                    awaiting_followup="confirmation",
                    blocked_reason="purchase_confirmation",
                )
            ),
            payload={"tab_id": self.runtime.current_tab_id, "site_name": site_name, "query": query, "blocking_reason": "purchase_confirmation"},
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Phase D — web_form_fill_submit
    # ──────────────────────────────────────────────────────────────────────────

    async def _fill_first_candidate(self, page: Any, selectors: list[str], value: str) -> bool:
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                await locator.wait_for(state="visible", timeout=3000)
                await locator.click(timeout=3000)
                await locator.fill("")
                await locator.type(value, delay=90 if getattr(self.config.browser_execution, "human_simulation", False) else 0)
                return True
            except Exception:
                continue
        return False

    async def _click_first_candidate(self, page: Any, selectors: list[str]) -> bool:
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                await locator.wait_for(state="visible", timeout=3000)
                await locator.click(timeout=3000)
                return True
            except Exception:
                continue
        return False

    async def _run_train_search_book(
        self,
        match: BrowserWorkflowMatch,
        *,
        user_id: str,
        task_state: dict[str, Any],
    ) -> BrowserWorkflowResult:
        source = str(match.details.get("source", "") or "").strip()
        destination = str(match.details.get("destination", "") or "").strip()
        travel_date = str(match.details.get("travel_date", "") or "").strip()
        preview = self._maybe_preview_workflow_plan(
            match,
            task_state,
            execution_mode="headed",
            target_url=SITE_URLS.get("irctc", ""),
            steps=[
                WorkflowPlanStep(name="open_irctc", detail="Open IRCTC."),
                WorkflowPlanStep(name="fill_route", detail=f"Fill train route from {source} to {destination}."),
                WorkflowPlanStep(name="search_trains", detail="Search trains and stop before any irreversible booking action."),
            ],
            resume_details={"source": source, "destination": destination, "travel_date": travel_date},
        )
        if preview is not None:
            return preview
        page = await self._open_site("irctc", user_id=user_id, headless=False)
        progress = ["Opened IRCTC."]
        steps = [WorkflowPlanStep(name="open_irctc", detail="Open IRCTC.", status="completed")]
        await self._emit_step(user_id, match.recipe_name, "open_irctc", progress[-1])
        blocked = await self._detect_blocker(page)
        if blocked:
            return self._blocked_result(match, blocked, progress, steps)
        await self._fill_first_candidate(page, [
            "input[placeholder*='From' i]",
            "input[aria-label*='From' i]",
            "input[formcontrolname*='from' i]",
        ], source)
        await self._fill_first_candidate(page, [
            "input[placeholder*='To' i]",
            "input[aria-label*='To' i]",
            "input[formcontrolname*='dest' i]",
        ], destination)
        if travel_date:
            await self._fill_first_candidate(page, [
                "input[placeholder*='DD/MM/YYYY' i]",
                "input[aria-label*='Date' i]",
                "input[formcontrolname*='journeyDate' i]",
            ], travel_date)
        await self._click_first_candidate(page, [
            "button:has-text('Search Trains')",
            "button[type='submit']",
            "button.search_btn",
        ])
        await self.runtime.post_action_wait(page, "domcontentloaded", 20)
        await self.runtime.refresh_active_tab(user_id)
        progress.append(f"Filled train search from {source} to {destination}.")
        steps.append(WorkflowPlanStep(name="fill_route", detail="Fill the train route.", status="completed"))
        await self._emit_step(user_id, match.recipe_name, "fill_route", progress[-1])
        return BrowserWorkflowResult(
            recipe_name=match.recipe_name,
            status="blocked",
            response_text=(
                f'Searched IRCTC for trains from "{source}" to "{destination}". '
                "I've stopped before any booking or payment step."
            ),
            progress_lines=progress,
            steps=steps,
            state_update=browser_task_state_update(
                active_task=self._build_workflow_state(
                    recipe_name=match.recipe_name,
                    site_name="irctc",
                    query=f"{source} to {destination}",
                    target_url=page.url,
                    execution_mode="headed",
                    awaiting_followup="site_action",
                    blocked_reason="review_required",
                )
            ),
            payload={"tab_id": self.runtime.current_tab_id, "source": source, "destination": destination},
        )

    async def _run_flight_search_book(
        self,
        match: BrowserWorkflowMatch,
        *,
        user_id: str,
        task_state: dict[str, Any],
    ) -> BrowserWorkflowResult:
        site_name = str(match.site_name or match.details.get("site_name") or "makemytrip").strip() or "makemytrip"
        source = str(match.details.get("source", "") or "").strip()
        destination = str(match.details.get("destination", "") or "").strip()
        travel_date = str(match.details.get("travel_date", "") or "").strip()
        preview = self._maybe_preview_workflow_plan(
            match,
            task_state,
            execution_mode="headed",
            target_url=SITE_URLS.get(site_name, ""),
            steps=[
                WorkflowPlanStep(name="open_travel_site", detail=f"Open {site_name}."),
                WorkflowPlanStep(name="fill_route", detail=f"Fill flight route from {source} to {destination}."),
                WorkflowPlanStep(name="search_flights", detail="Search flights and stop before passenger/payment confirmation."),
            ],
            resume_details={"site_name": site_name, "source": source, "destination": destination, "travel_date": travel_date},
        )
        if preview is not None:
            return preview
        page = await self._open_site(site_name, user_id=user_id, headless=False)
        progress = [f"Opened {site_name.title()}."]
        steps = [WorkflowPlanStep(name="open_travel_site", detail=f"Open {site_name.title()}.", status="completed")]
        await self._emit_step(user_id, match.recipe_name, "open_travel_site", progress[-1])
        blocked = await self._detect_blocker(page)
        if blocked:
            return self._blocked_result(match, blocked, progress, steps)
        await self._fill_first_candidate(page, [
            "input[placeholder*='From' i]",
            "input[aria-label*='From' i]",
            "input[placeholder*='Where from' i]",
        ], source)
        await self._fill_first_candidate(page, [
            "input[placeholder*='To' i]",
            "input[aria-label*='To' i]",
            "input[placeholder*='Where to' i]",
        ], destination)
        if travel_date:
            await self._fill_first_candidate(page, [
                "input[placeholder*='Departure' i]",
                "input[aria-label*='Date' i]",
                "input[placeholder*='Date' i]",
            ], travel_date)
        await self._click_first_candidate(page, ["button:has-text('Search')", "button[type='submit']"])
        await self.runtime.post_action_wait(page, "domcontentloaded", 20)
        await self.runtime.refresh_active_tab(user_id)
        progress.append(f"Filled flight search from {source} to {destination}.")
        steps.append(WorkflowPlanStep(name="fill_route", detail="Fill flight route.", status="completed"))
        await self._emit_step(user_id, match.recipe_name, "fill_route", progress[-1])
        return BrowserWorkflowResult(
            recipe_name=match.recipe_name,
            status="blocked",
            response_text=(
                f'Searched {site_name.title()} for flights from "{source}" to "{destination}". '
                "I've paused before any passenger or payment confirmation."
            ),
            progress_lines=progress,
            steps=steps,
            state_update=browser_task_state_update(
                active_task=self._build_workflow_state(
                    recipe_name=match.recipe_name,
                    site_name=site_name,
                    query=f"{source} to {destination}",
                    target_url=page.url,
                    execution_mode="headed",
                    awaiting_followup="site_action",
                    blocked_reason="review_required",
                )
            ),
            payload={"tab_id": self.runtime.current_tab_id, "site_name": site_name},
        )

    async def _run_food_search_order(
        self,
        match: BrowserWorkflowMatch,
        *,
        user_id: str,
        task_state: dict[str, Any],
    ) -> BrowserWorkflowResult:
        site_name = str(match.site_name or match.details.get("site_name") or "swiggy").strip() or "swiggy"
        query = str(match.query or match.details.get("query") or "").strip()
        page = await self._open_site(site_name, user_id=user_id, headless=False)
        progress = [f"Opened {site_name.title()}."]
        steps = [WorkflowPlanStep(name="open_food_site", detail=f"Open {site_name.title()}.", status="completed")]
        await self._emit_step(user_id, match.recipe_name, "open_food_site", progress[-1])
        blocked = await self._detect_blocker(page)
        if blocked:
            return self._blocked_result(match, blocked, progress, steps)
        search_box, _ = await self._find_search_input(page, site_name=site_name)
        await self._step_with_watchdog(search_box.fill(query))
        await self._step_with_watchdog(self.runtime.press_key(page, "Enter"))
        await self._step_with_watchdog(self.runtime.post_action_wait(page, "networkidle", 20))
        await self.runtime.refresh_active_tab(user_id)
        progress.append(f'Searched {site_name.title()} for "{query}".')
        steps.append(WorkflowPlanStep(name="search_food", detail="Search for the requested food or restaurant.", status="completed"))
        await self._emit_step(user_id, match.recipe_name, "search_food", progress[-1])
        return BrowserWorkflowResult(
            recipe_name=match.recipe_name,
            status="blocked",
            response_text=(
                f'Searched {site_name.title()} for "{query}". '
                "I've paused before placing any order or checkout."
            ),
            progress_lines=progress,
            steps=steps,
            state_update=browser_task_state_update(
                active_task=self._build_workflow_state(
                    recipe_name=match.recipe_name,
                    site_name=site_name,
                    query=query,
                    target_url=page.url,
                    execution_mode="headed",
                    awaiting_followup="site_action",
                    blocked_reason="review_required",
                )
            ),
            payload={"tab_id": self.runtime.current_tab_id, "site_name": site_name, "query": query},
        )

    async def _run_bill_payment_review(
        self,
        match: BrowserWorkflowMatch,
        *,
        user_id: str,
        task_state: dict[str, Any],
    ) -> BrowserWorkflowResult:
        site_name = str(match.site_name or match.details.get("site_name") or "").strip()
        page = await self._open_site(site_name, user_id=user_id, headless=False)
        progress = [f"Opened {site_name.title()} in read-only review mode."]
        steps = [WorkflowPlanStep(name="open_finance_site", detail=f"Open {site_name.title()}.", status="completed")]
        await self._emit_step(user_id, match.recipe_name, "open_finance_site", progress[-1])
        blocked = await self._detect_blocker(page)
        if blocked:
            return self._blocked_result(match, blocked, progress, steps)
        return BrowserWorkflowResult(
            recipe_name=match.recipe_name,
            status="blocked",
            response_text=(
                f"Opened {site_name.title()} in read-only review mode. "
                "I will not perform any money movement or irreversible payment action from this workflow."
            ),
            progress_lines=progress,
            steps=steps,
            state_update=browser_task_state_update(
                active_task=self._build_workflow_state(
                    recipe_name=match.recipe_name,
                    site_name=site_name,
                    query=str(match.query or ""),
                    target_url=page.url,
                    execution_mode="headed",
                    awaiting_followup="site_action",
                    blocked_reason="read_only_review",
                )
            ),
            payload={"tab_id": self.runtime.current_tab_id, "read_only": True, "site_name": site_name},
        )

    async def _run_web_form_fill_submit(
        self,
        match: BrowserWorkflowMatch,
        *,
        user_id: str,
        task_state: dict[str, Any],
    ) -> BrowserWorkflowResult:
        target_url = str(match.details.get("target_url", "") or "").strip()
        fields: dict[str, str] = dict(match.details.get("fields") or {})
        execution_mode = self._desired_mode_for_match(match)
        if not target_url:
            return BrowserWorkflowResult(
                recipe_name=match.recipe_name,
                status="needs_followup",
                response_text="Tell me the URL of the form you want me to fill, and the field values.",
            )
        if not target_url.startswith("http"):
            target_url = f"https://{target_url}"
        preview = self._maybe_preview_workflow_plan(
            match,
            task_state,
            execution_mode="headed",
            target_url=target_url,
            steps=[
                WorkflowPlanStep(name="open_form", detail=f"Open the form at {target_url}."),
                WorkflowPlanStep(name="fill_form", detail="Fill the provided form fields."),
                WorkflowPlanStep(name="review_form", detail="Pause in a visible browser window for review."),
                WorkflowPlanStep(name="submit_form", detail="Wait for confirm before submitting."),
            ],
            resume_details={"target_url": target_url, "fields": fields},
        )
        if preview is not None:
            return preview
        page = await self._open_site(target_url, user_id=user_id, target_url=target_url, headless=False)
        progress = [f"Opened form at {target_url}."]
        steps = [WorkflowPlanStep(name="open_site", detail="Open the form URL.", status="completed")]
        await self._emit_step(user_id, match.recipe_name, "open_site", progress[-1])
        blocked = await self._detect_blocker(page)
        if blocked:
            return self._blocked_result(match, blocked, progress, steps)
        form_schema = await self.runtime.inspect_form(page)
        if fields:
            filled: list[str] = []
            normalized_fields = {
                re.sub(r"[^a-z0-9]+", "", str(key).lower()): str(value)
                for key, value in fields.items()
            }
            discovered_fields = list(form_schema.get("fields") or [])
            for selector, value in fields.items():
                try:
                    locator, _ = await self.runtime.resolve_locator(page, selector, timeout_seconds=8)
                    await locator.fill(str(value), timeout=8000)
                    filled.append(selector)
                except Exception:
                    compact_selector = re.sub(r"[^a-z0-9]+", "", str(selector).lower())
                    best_match = None
                    for candidate in discovered_fields:
                        hints = " ".join(
                            str(candidate.get(key, "") or "")
                            for key in ("label", "name", "id", "placeholder", "aria_label")
                        ).lower()
                        if compact_selector and compact_selector in re.sub(r"[^a-z0-9]+", "", hints):
                            best_match = candidate
                            break
                    if best_match and str(best_match.get("selector_hint", "")).strip():
                        try:
                            locator, _ = await self.runtime.resolve_locator(
                                page,
                                str(best_match.get("selector_hint", "")).strip(),
                                timeout_seconds=8,
                            )
                            await locator.fill(str(value), timeout=8000)
                            filled.append(str(best_match.get("selector_hint", "")).strip())
                        except Exception:
                            continue
            if not filled and discovered_fields:
                mapped = 0
                for candidate in discovered_fields:
                    hints = [
                        re.sub(r"[^a-z0-9]+", "", str(candidate.get(key, "")).lower())
                        for key in ("label", "name", "id", "placeholder", "aria_label")
                    ]
                    selector_hint = str(candidate.get("selector_hint", "")).strip()
                    if not selector_hint:
                        continue
                    value = next((normalized_fields.get(hint) for hint in hints if hint and normalized_fields.get(hint)), None)
                    if value is None:
                        continue
                    try:
                        locator, _ = await self.runtime.resolve_locator(page, selector_hint, timeout_seconds=8)
                        await locator.fill(str(value), timeout=8000)
                        mapped += 1
                    except Exception:
                        continue
                if mapped:
                    filled.append(f"{mapped} mapped fields")
            progress.append(f"Filled {len(filled)} form fields.")
            steps.append(WorkflowPlanStep(name="fill_fields", detail="Fill form fields.", status="completed"))
            await self._emit_step(user_id, match.recipe_name, "fill_fields", progress[-1])
        elif form_schema.get("fields"):
            field_descriptions = []
            for field in list(form_schema.get("fields") or [])[:10]:
                label = str(field.get("label") or field.get("placeholder") or field.get("name") or field.get("id") or field.get("tag") or "field")
                field_descriptions.append(f"- {label}")
            return BrowserWorkflowResult(
                recipe_name=match.recipe_name,
                status="needs_followup",
                response_text="I inspected the form, but I still need the values to fill.\nAvailable fields:\n" + "\n".join(field_descriptions),
                progress_lines=progress,
                steps=steps,
                state_update=browser_task_state_update(
                    active_task=self._build_workflow_state(
                        recipe_name=match.recipe_name,
                        site_name=self._site_from_url(page.url),
                        query=target_url,
                        target_url=page.url,
                        execution_mode="headed",
                        awaiting_followup="site_action",
                    )
                ),
                payload={"form_schema": form_schema, "target_url": target_url},
            )
        pending = await self.runtime.prepare_protected_action(
            "submit",
            selector="button[type='submit'], input[type='submit'], button:has-text('Submit')",
            target=target_url,
            description="Submit the web form",
            user_id=user_id,
        )
        return BrowserWorkflowResult(
            recipe_name=match.recipe_name,
            status="blocked",
            response_text=(
                f"Filled the form at {target_url} and paused before submitting. "
                'Review it in the visible browser window, then say "confirm" to submit or "cancel" to stop.'
            ),
            progress_lines=progress,
            steps=steps,
            state_update=browser_task_state_update(
                active_task=self._build_workflow_state(
                    recipe_name=match.recipe_name,
                    site_name=self._site_from_url(page.url),
                    query=target_url,
                    target_url=page.url,
                    execution_mode="headed",
                    awaiting_followup="confirmation",
                ),
                pending_confirmation={**pending, "target_url": target_url},
            ),
            payload={"pending_action": pending, "target_url": target_url, "fields_filled": fields},
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Phase D — calendar_book
    # ──────────────────────────────────────────────────────────────────────────

    async def _run_calendar_book(
        self,
        match: BrowserWorkflowMatch,
        *,
        user_id: str,
        task_state: dict[str, Any],
    ) -> BrowserWorkflowResult:
        title = str(match.details.get("title", match.query or "")).strip()
        start_time = str(match.details.get("start_time", "")).strip()
        preview = self._maybe_preview_workflow_plan(
            match,
            task_state,
            execution_mode="headed",
            target_url="https://calendar.google.com/",
            steps=[
                WorkflowPlanStep(name="open_calendar", detail="Open Google Calendar in a visible browser window."),
                WorkflowPlanStep(name="fill_event", detail=f'Fill the event details for "{title or "new event"}".'),
                WorkflowPlanStep(name="review_event", detail="Pause for review before saving the event."),
            ],
            resume_details={"title": title, "start_time": start_time},
        )
        if preview is not None:
            return preview
        # Always headed for calendar events
        page = await self._open_site("google calendar", user_id=user_id, headless=False)
        progress = ["Opened Google Calendar."]
        steps = [WorkflowPlanStep(name="open_site", detail="Open Google Calendar.", status="completed")]
        await self._emit_step(user_id, match.recipe_name, "open_site", progress[-1])
        blocked = await self._detect_blocker(page)
        if blocked:
            return self._blocked_result(match, blocked, progress, steps)
        # Click the new-event button
        try:
            new_event_btn = page.locator("button[data-view='1'], [aria-label*='Create'], button:has-text('+')").first
            await new_event_btn.click(timeout=5000)
            await page.wait_for_timeout(800)
        except Exception:
            pass
        # Fill title if provided
        if title:
            try:
                title_input = await self.runtime.resolve_locator(page, "input[placeholder*='Title'], input[aria-label*='Title']", timeout_seconds=5)
                await title_input[0].fill(title, timeout=5000)
                progress.append(f'Filled event title: "{title}".')
                steps.append(WorkflowPlanStep(name="fill_title", detail="Fill event title.", status="completed"))
                await self._emit_step(user_id, match.recipe_name, "fill_title", progress[-1])
            except Exception:
                pass
        pending = await self.runtime.prepare_protected_action(
            "submit",
            selector="button[data-view='1'], [data-prober='Save'], button:has-text('Save')",
            target=page.url,
            description=f"Save calendar event: {title or 'new event'}",
            user_id=user_id,
        )
        return BrowserWorkflowResult(
            recipe_name=match.recipe_name,
            status="blocked",
            response_text=(
                f"Opened Google Calendar and started creating event '{title or 'new event'}'. "
                'Review the details in the visible window, then say "confirm" to save or "cancel" to discard.'
            ),
            progress_lines=progress,
            steps=steps,
            state_update=browser_task_state_update(
                active_task=self._build_workflow_state(
                    recipe_name=match.recipe_name,
                    site_name="google calendar",
                    query=title,
                    target_url=page.url,
                    execution_mode="headed",
                    awaiting_followup="confirmation",
                ),
                pending_confirmation={**pending, "title": title, "start_time": start_time},
            ),
            payload={"pending_action": pending, "title": title, "start_time": start_time},
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Phase D — email_compose_send
    # ──────────────────────────────────────────────────────────────────────────

    async def _run_email_compose_send(
        self,
        match: BrowserWorkflowMatch,
        *,
        user_id: str,
        task_state: dict[str, Any],
    ) -> BrowserWorkflowResult:
        to_addr = str(match.details.get("to", "") or "").strip()
        subject = str(match.details.get("subject", match.query or "") or "").strip()
        body = str(match.details.get("body", "") or "").strip()
        preview = self._maybe_preview_workflow_plan(
            match,
            task_state,
            execution_mode="headed",
            target_url="https://mail.google.com/mail/u/0/#compose",
            steps=[
                WorkflowPlanStep(name="open_compose", detail="Open Gmail compose in a visible browser window."),
                WorkflowPlanStep(name="fill_email", detail=f"Fill the email to {to_addr or 'the recipient'} with the provided subject and body."),
                WorkflowPlanStep(name="review_email", detail="Pause for review before sending."),
            ],
            resume_details={"to": to_addr, "subject": subject, "body": body},
        )
        if preview is not None:
            return preview
        # Always headed for email compose
        compose_url = "https://mail.google.com/mail/u/0/#compose"
        page = await self._open_site("gmail", user_id=user_id, target_url=compose_url, headless=False)
        progress = ["Opened Gmail compose window."]
        steps = [WorkflowPlanStep(name="open_compose", detail="Open Gmail compose.", status="completed")]
        await self._emit_step(user_id, match.recipe_name, "open_compose", progress[-1])
        blocked = await self._detect_blocker(page)
        if blocked:
            return self._blocked_result(match, blocked, progress, steps)
        # Fill To field
        if to_addr:
            try:
                to_field = page.locator("[name='to'], [aria-label*='To'], [data-hovered*='to']").first
                await to_field.fill(to_addr, timeout=5000)
                progress.append(f"Filled To: {to_addr}.")
            except Exception:
                pass
        # Fill Subject
        if subject:
            try:
                subj_field = page.locator("[name='subjectbox'], [aria-label*='Subject']").first
                await subj_field.fill(subject, timeout=5000)
                progress.append(f'Filled Subject: "{subject}".')
            except Exception:
                pass
        # Fill Body
        if body:
            try:
                body_field = page.locator("[role='textbox'][aria-label*='Message Body'], div[contenteditable='true']").first
                await body_field.fill(body, timeout=5000)
                progress.append("Filled message body.")
            except Exception:
                pass
        steps.append(WorkflowPlanStep(name="fill_fields", detail="Fill email fields.", status="completed"))
        await self._emit_step(user_id, match.recipe_name, "fill_fields", "Filled email fields.")
        pending = await self.runtime.prepare_protected_action(
            "send",
            selector="[aria-label*='Send'], div[role='button']:has-text('Send')",
            target=compose_url,
            description=f"Send email to {to_addr or 'recipient'}: {subject or 'no subject'}",
            user_id=user_id,
        )
        return BrowserWorkflowResult(
            recipe_name=match.recipe_name,
            status="blocked",
            response_text=(
                f"Composed an email to '{to_addr or 'recipient'}' with subject '{subject or 'no subject'}'. "
                'Review the email in the visible window, then say "confirm" to send or "cancel" to discard.'
            ),
            progress_lines=progress,
            steps=steps,
            state_update=browser_task_state_update(
                active_task=self._build_workflow_state(
                    recipe_name=match.recipe_name,
                    site_name="gmail",
                    query=f"email to {to_addr}",
                    target_url=compose_url,
                    execution_mode="headed",
                    awaiting_followup="confirmation",
                ),
                pending_confirmation={**pending, "to": to_addr, "subject": subject},
            ),
            payload={"pending_action": pending, "to": to_addr, "subject": subject},
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Phase C — youtube_media_control
    # ──────────────────────────────────────────────────────────────────────────

    async def _run_youtube_media_control(
        self,
        match: BrowserWorkflowMatch,
        *,
        user_id: str,
        task_state: dict[str, Any],
    ) -> BrowserWorkflowResult:
        action = str(match.action or match.details.get("media_action") or "pause").strip().lower()
        seek_seconds = int(match.details.get("seek_seconds", 10))
        # Map natural language to API action
        action_map = {
            "pause": "pause", "stop": "pause",
            "play": "play", "resume": "play", "unpause": "play",
            "mute": "mute", "silent": "mute",
            "unmute": "unmute", "volume": "unmute",
            "skip": "seek", "forward": "seek", "ahead": "seek", "seek": "seek",
            "back": "back", "rewind": "back",
            "fullscreen": "fullscreen", "fullscreen_toggle": "fullscreen",
        }
        resolved_action = action_map.get(action, action)
        # Find the YouTube tab
        existing = self.runtime.find_matching_tab(site_name="youtube")
        if existing is None:
            return BrowserWorkflowResult(
                recipe_name=match.recipe_name,
                status="needs_followup",
                response_text="I don't see an open YouTube tab right now. Open a YouTube video first, then ask me to control it.",
            )
        existing_url = str(existing.get("url", "") or "")
        if not re.search(r"(youtube\.com/(watch|shorts)|youtu\.be/)", existing_url, flags=re.IGNORECASE):
            return BrowserWorkflowResult(
                recipe_name=match.recipe_name,
                status="needs_followup",
                response_text=(
                    "I can see an open YouTube tab, but it is not on a video watch page yet. "
                    "Tell me what to search for first, for example: open youtube and play MrBeast."
                ),
                state_update=browser_task_state_update(
                    active_task=self._build_workflow_state(
                        recipe_name="site_open_and_search",
                        site_name="youtube",
                        query="",
                        target_url=existing_url,
                        execution_mode=str(existing.get("mode", "headless") or "headless"),
                        awaiting_followup="site_action",
                    )
                ),
                payload={"tab_id": existing.get("tab_id"), "site_name": "youtube"},
            )
        page = await self.runtime.switch_tab(str(existing["tab_id"]), user_id=user_id)
        # switch_tab returns a dict, get the actual page object
        actual_page_state = self.runtime._tabs.get(str(existing["tab_id"]))
        if actual_page_state is None:
            return BrowserWorkflowResult(
                recipe_name=match.recipe_name,
                status="error",
                response_text="Could not access the YouTube tab's page object.",
            )
        result = await self.runtime.media_control(actual_page_state.page, resolved_action, seek_seconds=seek_seconds)
        await self.runtime.refresh_active_tab(user_id)
        label = {
            "play": "resumed", "pause": "paused", "mute": "muted",
            "unmute": "unmuted", "seek": f"skipped ahead {seek_seconds}s",
            "back": f"rewound {seek_seconds}s", "fullscreen": "toggled fullscreen",
        }.get(resolved_action, resolved_action)
        return BrowserWorkflowResult(
            recipe_name=match.recipe_name,
            status="completed",
            response_text=f"YouTube video {label}.",
            progress_lines=[f"YouTube video {label}."],
            steps=[WorkflowPlanStep(name="media_control", detail=f"YouTube {label}.", status="completed")],
            state_update=browser_task_state_update(
                active_task=self._build_workflow_state(
                    recipe_name=match.recipe_name,
                    site_name="youtube",
                    query=action,
                    target_url=str(existing.get("url", "")),
                    execution_mode="headless",
                )
            ),
            payload={"action": resolved_action, "result": result, "tab_id": existing["tab_id"]},
        )
