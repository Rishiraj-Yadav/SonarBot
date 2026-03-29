"""Hybrid NLP matcher for browser workflows."""

from __future__ import annotations

import json
import re
from urllib.parse import urlparse
from typing import Any

from assistant.browser_workflows.models import BrowserWorkflowMatch
from assistant.browser_workflows.recipes import RECIPE_BY_NAME, SITE_ALIASES, SITE_URLS
from assistant.browser_workflows.state import active_browser_task, normalize_browser_task_state


def _normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _normalize_quotes(value: str) -> str:
    return value.strip().strip("\"'")


def normalize_site_name(value: str | None) -> str | None:
    if not value:
        return None
    lowered = value.strip().lower()
    for canonical, aliases in SITE_ALIASES.items():
        if lowered == canonical or lowered in aliases:
            return canonical
    return None


def normalize_browser_target(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    raw = value.strip()
    canonical = normalize_site_name(raw)
    if canonical is not None:
        return canonical, None

    candidate = raw
    if not re.match(r"^https?://", candidate, flags=re.IGNORECASE):
        candidate = f"https://{candidate}"
    parsed = urlparse(candidate)
    host = parsed.netloc.strip().lower()
    if not host or "." not in host:
        return None, None
    normalized_url = f"{parsed.scheme or 'https'}://{host}{parsed.path or ''}"
    if parsed.query:
        normalized_url = f"{normalized_url}?{parsed.query}"
    return host, normalized_url


def infer_site_from_runtime(runtime_state: dict[str, Any] | None) -> str | None:
    if not runtime_state:
        return None
    active_profile = runtime_state.get("active_profile") or {}
    active_site = normalize_site_name(str(active_profile.get("site_name", "")))
    if active_site:
        return active_site
    active_tab = runtime_state.get("active_tab") or {}
    url = str(active_tab.get("url", "") or "")
    lowered = url.lower()
    for canonical, aliases in SITE_ALIASES.items():
        if any(alias in lowered for alias in aliases):
            return canonical
    if url:
        parsed = urlparse(url)
        host = parsed.netloc.strip().lower()
        if host:
            return host
    return None


class BrowserWorkflowNLP:
    def __init__(self, config, tool_registry) -> None:
        self.config = config
        self.tool_registry = tool_registry

    def standalone_execution_override(self, message: str) -> str | None:
        override, stripped = self._extract_execution_override(_normalize_spaces(message))
        if override and not stripped:
            return override
        return None

    async def match(
        self,
        message: str,
        *,
        runtime_state: dict[str, Any] | None = None,
        previous_state: dict[str, Any] | None = None,
        force: bool = False,
    ) -> BrowserWorkflowMatch | None:
        stripped = _normalize_spaces(message)
        if not stripped:
            return None
        task_state = normalize_browser_task_state(previous_state)
        active_task = active_browser_task(task_state)
        session_override = str(task_state.get("next_task_mode_override", "")).strip().lower() or None
        execution_mode_override, stripped = self._extract_execution_override(stripped)
        if not stripped:
            return None
        deterministic = self._match_deterministic(stripped, runtime_state=runtime_state, task_state=task_state, active_task=active_task)
        if deterministic is not None:
            return self._apply_match_context(
                deterministic,
                execution_mode_override=execution_mode_override or session_override,
                task_state=task_state,
            )
        if not self.config.browser_workflows.llm_classifier_enabled:
            return None
        if not force and not self._looks_browserish(stripped, runtime_state=runtime_state, task_state=task_state):
            return None
        classified = await self._classify_with_llm(stripped, runtime_state=runtime_state, task_state=task_state)
        return self._apply_match_context(
            classified,
            execution_mode_override=execution_mode_override or session_override,
            task_state=task_state,
        )

    def _match_deterministic(
        self,
        message: str,
        *,
        runtime_state: dict[str, Any] | None,
        task_state: dict[str, Any],
        active_task: dict[str, Any],
    ) -> BrowserWorkflowMatch | None:
        lowered = message.lower()
        inferred_site = infer_site_from_runtime(runtime_state)
        normalized = _normalize_spaces(lowered)
        pending_confirmation = dict(task_state.get("pending_confirmation") or {})
        pending_login = dict(task_state.get("pending_login") or {})
        pending_disambiguation = dict(task_state.get("pending_disambiguation") or {})
        awaiting_followup = str(active_task.get("awaiting_followup", "")).strip().lower()

        if normalized in {"confirm", "confirm it", "submit", "submit it", "send it", "approve it"}:
            if pending_confirmation or awaiting_followup == "confirmation":
                return BrowserWorkflowMatch(
                    recipe_name="browser_continue_last_task",
                    confidence=0.97,
                    site_name=str((pending_confirmation or active_task).get("site_name", "")) or inferred_site,
                    query=active_task.get("query"),
                    action="confirm",
                    details={"task_state": task_state},
                )
            return None

        if normalized in {"cancel", "cancel it", "abort", "abort it", "stop it", "don't do it"}:
            if pending_confirmation or awaiting_followup in {"confirmation", "workflow_plan"}:
                return BrowserWorkflowMatch(
                    recipe_name="browser_continue_last_task",
                    confidence=0.97,
                    site_name=str((pending_confirmation or active_task).get("site_name", "")) or inferred_site,
                    query=active_task.get("query"),
                    action="cancel",
                    details={"task_state": task_state},
                )
            return None

        if normalized in {"yes", "ok", "okay", "do it", "go ahead"}:
            if pending_confirmation:
                return BrowserWorkflowMatch(
                    recipe_name="browser_continue_last_task",
                    confidence=0.92,
                    site_name=str(pending_confirmation.get("site_name", "")) or inferred_site,
                    query=active_task.get("query"),
                    action="confirm",
                    details={"task_state": task_state},
                )
            if pending_disambiguation:
                disambiguation_details = {
                    key: value
                    for key, value in dict(pending_disambiguation.get("details") or {}).items()
                    if key not in {"needs_disambiguation", "intended_action", "disambiguation_confirmed"}
                }
                return BrowserWorkflowMatch(
                    recipe_name=str(pending_disambiguation.get("recipe_name", "")).strip() or "site_open_and_search",
                    confidence=0.9,
                    site_name=str(pending_disambiguation.get("site_name", "")) or inferred_site,
                    query=str(pending_disambiguation.get("query", "")).strip() or None,
                    action=str(pending_disambiguation.get("action", "")).strip() or None,
                    open_first_result=bool(pending_disambiguation.get("open_first_result")),
                    details={
                        **disambiguation_details,
                        "task_state": task_state,
                        "disambiguation_confirmed": True,
                    },
                )
            if awaiting_followup == "workflow_plan":
                return BrowserWorkflowMatch(
                    recipe_name="browser_continue_last_task",
                    confidence=0.96,
                    site_name=str(active_task.get("site_name", "")) or inferred_site,
                    query=active_task.get("query"),
                    action="confirm",
                    details={"task_state": task_state},
                )
            if pending_login or awaiting_followup == "continue":
                return BrowserWorkflowMatch(
                    recipe_name="browser_continue_last_task",
                    confidence=0.9,
                    site_name=str((pending_login or active_task).get("site_name", "")) or inferred_site,
                    query=active_task.get("query"),
                    details={"task_state": task_state},
                )
            return None

        if self._looks_like_continue(lowered):
            details: dict[str, Any] = {"task_state": task_state}
            if "first result" in lowered:
                details["open_first_result"] = True
            if "play" in lowered:
                details["action"] = "play"
            return BrowserWorkflowMatch(
                recipe_name="browser_continue_last_task",
                confidence=0.99,
                site_name=str(active_task.get("site_name", "")) or inferred_site,
                query=active_task.get("query"),
                action=details.get("action"),
                open_first_result=bool(details.get("open_first_result")),
                details=details,
            )

        browser_open = self._extract_open_browser_request(
            message,
            runtime_state=runtime_state,
            active_task=active_task,
            task_state=task_state,
        )
        if browser_open is not None:
            return browser_open

        if re.search(r"\b(?:login|log in|sign in)(?:\s+(?:to|into))?\s+(?:it|there|this site|this page)\b", lowered):
            site_name = inferred_site or str(active_task.get("site_name", "")).strip() or None
            if site_name:
                return BrowserWorkflowMatch(
                    recipe_name="site_login_then_continue",
                    confidence=0.95,
                    site_name=site_name,
                    action="login",
                    details={"task_state": task_state},
                )

        login_match = re.search(r"\b(?:login|log in|log into|sign in|signin)\s+(?:to\s+)?([a-z0-9 ._:/-]+)", lowered)
        if login_match:
            site_name = normalize_site_name(login_match.group(1))
            raw_site, raw_url = normalize_browser_target(login_match.group(1))
            site_name = site_name or raw_site or inferred_site
            if site_name:
                return BrowserWorkflowMatch(
                    recipe_name="site_login_then_continue",
                    confidence=0.97,
                    site_name=site_name,
                    action="login",
                    details={"task_state": task_state, "target_url": raw_url},
                )

        explicit_open = self._extract_explicit_open_target(message)
        if explicit_open is not None:
            site_name, target_url = explicit_open
            return BrowserWorkflowMatch(
                recipe_name="site_open_exact_url_or_path",
                confidence=0.95,
                site_name=site_name,
                action="open",
                details={"task_state": task_state, "target_url": target_url},
            )

        leetcode_problem = self._extract_leetcode_problem(message)
        if leetcode_problem is not None:
            return BrowserWorkflowMatch(
                recipe_name="leetcode_open_problem",
                confidence=0.95,
                site_name="leetcode",
                query=leetcode_problem,
                action="open_problem",
                details={"task_state": task_state},
            )

        github_issue = self._extract_github_issue_request(message)
        if github_issue is not None:
            return BrowserWorkflowMatch(
                recipe_name="github_issue_compose",
                confidence=0.91,
                site_name="github",
                action="open_issue",
                details={"task_state": task_state, **github_issue},
            )

        github_inspect = self._extract_github_repo_inspect_request(message, inferred_site=inferred_site)
        if github_inspect is not None:
            return BrowserWorkflowMatch(
                recipe_name="github_repo_inspect",
                confidence=0.9,
                site_name="github",
                action="inspect_repo",
                details={"task_state": task_state, **github_inspect},
            )

        youtube_latest = self._extract_youtube_latest_query(message)
        if youtube_latest:
            return BrowserWorkflowMatch(
                recipe_name="youtube_latest_video",
                confidence=0.95,
                site_name="youtube",
                query=youtube_latest,
                action="play_latest",
                details={"task_state": task_state},
            )

        youtube_query = self._extract_youtube_query(message)
        if youtube_query:
            return BrowserWorkflowMatch(
                recipe_name="youtube_search_play",
                confidence=0.96,
                site_name="youtube",
                query=youtube_query,
                action="play",
                details={"task_state": task_state},
            )

        inferred_youtube_query = self._extract_inferred_youtube_query(
            message,
            inferred_site=inferred_site,
            active_task=active_task,
        )
        if inferred_youtube_query:
            return BrowserWorkflowMatch(
                recipe_name="youtube_search_play",
                confidence=0.9,
                site_name="youtube",
                query=inferred_youtube_query,
                action="play",
                details={"task_state": task_state},
            )

        google_query, open_first_result = self._extract_google_query(message)
        if google_query:
            return BrowserWorkflowMatch(
                recipe_name="google_search_open",
                confidence=0.95,
                site_name="google",
                query=google_query,
                action="open_result",
                open_first_result=open_first_result,
                details={"task_state": task_state},
            )

        # ── New Phase D deterministic patterns ───────────────────────────────────

        # youtube_media_control
        media_action = self._extract_youtube_media_action(message, runtime_state=runtime_state, active_task=active_task)
        if media_action:
            return BrowserWorkflowMatch(
                recipe_name="youtube_media_control",
                confidence=0.97,
                site_name="youtube",
                action=media_action["action"],
                details={"task_state": task_state, **media_action},
            )

        # twitter_search_scroll
        twitter_query = self._extract_twitter_query(message)
        if twitter_query:
            return BrowserWorkflowMatch(
                recipe_name="twitter_search_scroll",
                confidence=0.94,
                site_name="twitter",
                query=twitter_query,
                action="search_scroll",
                details={"task_state": task_state},
            )

        # reddit_search_open
        reddit_query = self._extract_reddit_query(message)
        if reddit_query:
            return BrowserWorkflowMatch(
                recipe_name="reddit_search_open",
                confidence=0.94,
                site_name="reddit",
                query=reddit_query,
                action="search",
                details={"task_state": task_state},
            )

        # amazon_search_buy
        amazon_match = self._extract_amazon_query(message)
        if amazon_match:
            return BrowserWorkflowMatch(
                recipe_name="amazon_search_buy",
                confidence=0.93,
                site_name=amazon_match["site_name"],
                query=amazon_match["query"],
                action="search_buy",
                details={"task_state": task_state},
            )

        train_match = self._extract_train_search(message)
        if train_match:
            return BrowserWorkflowMatch(
                recipe_name="train_search_book",
                confidence=0.94,
                site_name="irctc",
                query=str(train_match.get("route", "")).strip() or None,
                action="search_trains",
                details={"task_state": task_state, **train_match},
            )

        flight_match = self._extract_flight_search(message)
        if flight_match:
            return BrowserWorkflowMatch(
                recipe_name="flight_search_book",
                confidence=0.94,
                site_name=str(flight_match.get("site_name", "")).strip() or "makemytrip",
                query=str(flight_match.get("route", "")).strip() or None,
                action="search_flights",
                details={"task_state": task_state, **flight_match},
            )

        food_match = self._extract_food_order(message)
        if food_match:
            return BrowserWorkflowMatch(
                recipe_name="food_search_order",
                confidence=0.92,
                site_name=str(food_match.get("site_name", "")).strip() or "swiggy",
                query=str(food_match.get("query", "")).strip() or None,
                action="search_food",
                details={"task_state": task_state, **food_match},
            )

        bill_match = self._extract_bill_payment_review(message)
        if bill_match:
            return BrowserWorkflowMatch(
                recipe_name="bill_payment_review",
                confidence=0.92,
                site_name=str(bill_match.get("site_name", "")).strip() or None,
                query=str(bill_match.get("query", "")).strip() or None,
                action="review",
                details={"task_state": task_state, **bill_match},
            )

        # page_read_summarize
        summarize_url = self._extract_page_summarize_url(message)
        if summarize_url:
            return BrowserWorkflowMatch(
                recipe_name="page_read_summarize",
                confidence=0.93,
                site_name=self._site_from_url_quick(summarize_url),
                query=summarize_url,
                action="summarize",
                details={"task_state": task_state, "target_url": summarize_url},
            )

        # multi_tab_research: only via LLM (complex intent); handled below

        site_search = self._extract_site_search(message, inferred_site=inferred_site, task_state=task_state)
        if site_search is not None:
            return site_search
        return None

    def _extract_youtube_query(self, message: str) -> str | None:
        normalized = _normalize_spaces(message)
        patterns = (
            r"^(?:please\s+)?open\s+youtube(?:\.com)?(?:\s+and)?\s+(?:play|watch|open|run)\s+(.+)$",
            r"^(?:please\s+)?search\s+youtube(?:\.com)?\s+for\s+(.+?)(?:\s+and\s+(?:play|watch|open)(?:\s+the)?\s*(?:best match|first result)?)?$",
            r"^(?:please\s+)?(?:play|watch)\s+(.+?)\s+(?:on|in)\s+youtube(?:\.com)?$",
        )
        for pattern in patterns:
            match = re.match(pattern, normalized, flags=re.IGNORECASE)
            if not match:
                continue
            query = _normalize_quotes(match.group(1))
            if query:
                return query
        return None

    def _extract_youtube_latest_query(self, message: str) -> str | None:
        normalized = _normalize_spaces(message)
        patterns = (
            r"^(?:please\s+)?(?:play|open|run|watch)\s+(?:the\s+)?latest\s+(?:video\s+)?(?:of|from)\s+(.+)$",
            r"^(?:please\s+)?open\s+youtube(?:\.com)?(?:\s+and)?\s+(?:play|run|open)\s+(?:the\s+)?latest\s+(.+?)\s+video$",
            r"^(?:please\s+)?now\s+run\s+(?:the\s+)?latest\s+video\s+of\s+(.+)$",
        )
        for pattern in patterns:
            match = re.match(pattern, normalized, flags=re.IGNORECASE)
            if not match:
                continue
            query = _normalize_quotes(match.group(1))
            if query:
                return query
        return None

    def _extract_inferred_youtube_query(
        self,
        message: str,
        *,
        inferred_site: str | None,
        active_task: dict[str, Any],
    ) -> str | None:
        active_site = normalize_site_name(str(active_task.get("site_name", ""))) or None
        if (inferred_site or active_site) != "youtube":
            return None
        normalized = _normalize_spaces(message)
        lowered = normalized.lower()
        if lowered.startswith("open "):
            open_target = _normalize_quotes(normalized[5:])
            explicit_site = normalize_site_name(open_target)
            explicit_raw_site, explicit_url = normalize_browser_target(open_target)
            if explicit_site or explicit_raw_site or explicit_url:
                return None
            if any(token in lowered for token in (" website", " site", " browser")):
                return None
        patterns = (
            r"^(?:now\s+)?search\s+(?:for\s+)?(.+?)\s+(?:and\s+)?(?:play|watch|open)(?:\s+it)?$",
            r"^(?:now\s+)?search\s+(?:for\s+)?(.+?)$",
            r"^(?:now\s+)?(?:play|watch)\s+(.+?)(?:\s+video)?$",
        )
        for pattern in patterns:
            match = re.match(pattern, normalized, flags=re.IGNORECASE)
            if not match:
                continue
            query = _normalize_quotes(match.group(1))
            if query:
                return query
        return None

    def _extract_google_query(self, message: str) -> tuple[str | None, bool]:
        normalized = _normalize_spaces(message)
        patterns = (
            r"^(?:please\s+)?search\s+google(?:\.com)?\s+for\s+(.+?)(?:\s+and\s+open\s+(?:the\s+)?(first result|best result|best match))?$",
            r"^(?:please\s+)?google\s+(.+?)(?:\s+and\s+open\s+(?:the\s+)?(first result|best result|best match))?$",
        )
        for pattern in patterns:
            match = re.match(pattern, normalized, flags=re.IGNORECASE)
            if not match:
                continue
            query = _normalize_quotes(match.group(1))
            if not query:
                continue
            open_directive = (match.group(2) or "").lower()
            return query, "first result" in open_directive
        return None, False

    def _extract_open_browser_request(
        self,
        message: str,
        *,
        runtime_state: dict[str, Any] | None,
        active_task: dict[str, Any],
        task_state: dict[str, Any],
    ) -> BrowserWorkflowMatch | None:
        normalized = _normalize_spaces(message)
        if not re.match(r"^(?:please\s+)?open(?:\s+the)?\s+browser(?:\s+window)?(?:\s+now)?$", normalized, flags=re.IGNORECASE):
            return None

        active_tab = (runtime_state or {}).get("active_tab") or {}
        target_url = str(
            active_task.get("target_url", "")
            or active_task.get("active_url", "")
            or active_tab.get("url", "")
            or ""
        ).strip()
        site_name = (
            normalize_site_name(str(active_task.get("site_name", "")).strip())
            or infer_site_from_runtime(runtime_state)
            or self._site_from_url_quick(target_url)
        )
        if not target_url and site_name:
            canonical_site = normalize_site_name(site_name) or site_name
            target_url = SITE_URLS.get(canonical_site, "")
        if not target_url and not site_name:
            return None
        return BrowserWorkflowMatch(
            recipe_name="site_open_exact_url_or_path",
            confidence=0.95,
            site_name=site_name,
            action="open",
            details={"task_state": task_state, **({"target_url": target_url} if target_url else {})},
        )

    def _extract_explicit_open_target(self, message: str) -> tuple[str, str] | None:
        normalized = _normalize_spaces(message)
        match = re.match(
            r"^(?:please\s+)?open\s+((?:https?://)?[a-z0-9][a-z0-9._/-]*\.[a-z]{2,}(?:[/?#][^\s]*)?)$",
            normalized,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        site_name, target_url = normalize_browser_target(match.group(1))
        if site_name and target_url:
            return site_name, target_url
        return None

    def _extract_leetcode_problem(self, message: str) -> str | None:
        normalized = _normalize_spaces(message)
        patterns = (
            r"^(?:please\s+)?open\s+(?:the\s+)?(?:leetcode\s+)?problem\s+([a-z0-9 -]+)$",
            r"^(?:please\s+)?open\s+(?:the\s+)?([a-z0-9 -]+)\s+problem\s+(?:on|in)\s+leetcode$",
            r"^(?:please\s+)?open\s+leetcode\s+problem\s+([a-z0-9 -]+)$",
        )
        for pattern in patterns:
            match = re.match(pattern, normalized, flags=re.IGNORECASE)
            if match:
                return _normalize_quotes(match.group(1))
        return None

    def _extract_github_issue_request(self, message: str) -> dict[str, Any] | None:
        lowered = message.lower()
        if "issue" not in lowered or "repo" not in lowered:
            return None
        if not any(token in lowered for token in ("open", "create", "new")):
            return None
        repo_hint = self._extract_repo_name_hint(message)
        direct_repo = self._extract_owner_repo(message)
        return {
            "repo_hint": repo_hint or "",
            "owner": direct_repo[0] if direct_repo else "",
            "repo": direct_repo[1] if direct_repo else "",
        }

    def _extract_github_repo_inspect_request(self, message: str, *, inferred_site: str | None) -> dict[str, Any] | None:
        lowered = _normalize_spaces(message.lower())
        if not any(phrase in lowered for phrase in ("tell me about", "what about", "can you tell about", "describe")):
            return None
        if "repo" not in lowered and inferred_site != "github":
            return None
        repo_hint = self._extract_repo_name_hint(message)
        direct_repo = self._extract_owner_repo(message)
        return {
            "repo_hint": repo_hint or "",
            "owner": direct_repo[0] if direct_repo else "",
            "repo": direct_repo[1] if direct_repo else "",
        }

    def _extract_site_search(
        self,
        message: str,
        *,
        inferred_site: str | None,
        task_state: dict[str, Any],
    ) -> BrowserWorkflowMatch | None:
        normalized = _normalize_spaces(message)
        patterns = (
            r"^(?:please\s+)?open\s+([a-z0-9][a-z0-9 ._:/-]*?)(?:\.com)?\s+website(?:\s+(?:and\s+(?:search|find|look for)|for)\s+(.+))?$",
            r"^(?:please\s+)?open\s+([a-z0-9][a-z0-9 ._:/-]*?)(?:\.com)?(?:\s+and\s+(?:search|find|look for)\s+(.+))?$",
            r"^(?:please\s+)?(?:search|find|look for)\s+(.+?)\s+(?:on|in)\s+([a-z0-9][a-z0-9 ._:/-]*?)(?:\.com)?$",
        )
        for pattern in patterns:
            match = re.match(pattern, normalized, flags=re.IGNORECASE)
            if not match:
                continue
            if pattern.startswith("^(?:please\\s+)?open"):
                site_name = normalize_site_name(match.group(1))
                raw_site, raw_url = normalize_browser_target(match.group(1))
                query = _normalize_quotes(match.group(2) or "")
            else:
                site_name = normalize_site_name(match.group(2))
                raw_site, raw_url = normalize_browser_target(match.group(2))
                query = _normalize_quotes(match.group(1))
            if site_name or raw_site:
                recipe_name = "site_open_exact_url_or_path" if raw_url and not query else "site_open_and_search"
                return BrowserWorkflowMatch(
                    recipe_name=recipe_name,
                    confidence=0.9 if query else 0.86,
                    site_name=site_name or raw_site,
                    query=query or None,
                    action="search" if query else "open",
                    details={"task_state": task_state, **({"target_url": raw_url} if raw_url else {})},
                )

        if inferred_site and any(token in normalized.lower() for token in ("search", "find", "look for")):
            search_match = re.search(r"\b(?:search|find|look for)\s+(.+)$", normalized, flags=re.IGNORECASE)
            if search_match:
                query = _normalize_quotes(search_match.group(1))
                if query:
                    return BrowserWorkflowMatch(
                        recipe_name="site_open_and_search",
                        confidence=0.84,
                        site_name=inferred_site,
                        query=query,
                        action="search",
                        details={"task_state": task_state},
                    )
        return None

    def _looks_browserish(
        self,
        message: str,
        *,
        runtime_state: dict[str, Any] | None,
        task_state: dict[str, Any],
    ) -> bool:
        lowered = message.lower()
        if re.search(
            r"\b[a-z0-9._/-]+\.(?:md|txt|pdf|json|yaml|yml|toml|csv|xml|py|js|ts|tsx|jsx|html|doc|docx)\b",
            lowered,
        ) and not any(token in lowered for token in ("http://", "https://", "www.", "browser", "website", "page", "tab", "site")):
            return False
        browser_verbs = (
            "open", "search", "find", "play", "watch", "login", "log in", "sign in",
            "switch", "download", "pause", "mute", "unmute", "resume", "skip", "rewind",
            "scroll", "read", "summarize", "compare", "research", "tweet", "reddit",
            "amazon", "flipkart", "book", "schedule", "compose email", "send email",
            "fill", "submit form",
        )
        if any(token in lowered for token in browser_verbs):
            return True
        for aliases in SITE_ALIASES.values():
            if any(alias in lowered for alias in aliases):
                return True
        active_task = active_browser_task(task_state)
        if active_task and any(
            token in lowered
            for token in ("browser", "tab", "page", "site", "result", "video", "there", "here", "that one", "it")
        ):
            return True
        return bool(infer_site_from_runtime(runtime_state))

    def _extract_execution_override(self, message: str) -> tuple[str | None, str]:
        working = message
        override: str | None = None
        headed_patterns = (
            r"\bshow me what you(?:'| a)re doing\b",
            r"\bshow me what you're doing\b",
            r"\bshow me w(?:ha|ah)t you(?:'| a)?re doing\b",
            r"\bshow me what are you doing(?: now)?\b",
            r"\bshow me w(?:ha|ah)t are you doing(?: now)?\b",
            r"\bshow what you(?:'| a)re doing(?: now)?\b",
            r"\bshow what are you doing(?: now)?\b",
            r"\bshow me the browser\b",
            r"\bopen it visibly\b",
            r"\bwatch it\b",
            r"\bon screen\b",
            r"\bvisibly\b",
        )
        headless_patterns = (
            r"\brun silently\b",
            r"\brun silently now\b",
            r"\bdo it in the background\b",
            r"\bin the background\b",
            r"\brun it in the background\b",
            r"\bheadless\b",
        )
        for pattern in headed_patterns:
            updated, count = re.subn(pattern, " ", working, count=1, flags=re.IGNORECASE)
            if count:
                working = updated
                override = "headed"
        for pattern in headless_patterns:
            updated, count = re.subn(pattern, " ", working, count=1, flags=re.IGNORECASE)
            if count:
                working = updated
                override = "headless"
        cleaned = re.sub(r"^\s*(?:and then|and)\b", " ", working, flags=re.IGNORECASE)
        cleaned = re.sub(r"\b(?:and then|and)\b\s*$", "", cleaned, flags=re.IGNORECASE)
        cleaned = _normalize_spaces(cleaned)
        return override, cleaned

    def _apply_match_context(
        self,
        match: BrowserWorkflowMatch | None,
        *,
        execution_mode_override: str | None,
        task_state: dict[str, Any],
    ) -> BrowserWorkflowMatch | None:
        if match is None:
            return None
        details = dict(match.details)
        details["task_state"] = task_state
        if execution_mode_override:
            details["execution_mode_override"] = execution_mode_override
        match.details = details
        return match

    async def _classify_with_llm(
        self,
        message: str,
        *,
        runtime_state: dict[str, Any] | None,
        task_state: dict[str, Any],
    ) -> BrowserWorkflowMatch | None:
        has_tool = getattr(self.tool_registry, "has", None)
        if callable(has_tool) and not has_tool("llm_task"):
            return None
        prompt = "\n".join(
            [
                "Classify the user's browser-automation intent.",
                "Return strict JSON only.",
                (
                    '{"recipe_name":"<recipe-or-none>","confidence":0.0,'
                    '"site_name":"<site-or-empty>","query":"<query-or-empty>",'
                    '"action":"<action-or-empty>","open_first_result":false,'
                    '"urls":[],"fields":{}}'
                ),
                (
                    "Allowed recipes: site_open_exact_url_or_path, youtube_search_play, youtube_latest_video, "
                    "google_search_open, site_open_and_search, leetcode_open_problem, github_repo_inspect, "
                    "github_issue_compose, site_login_then_continue, browser_continue_last_task, "
                    "generic_page_interact, page_read_summarize, multi_tab_research, "
                    "twitter_search_scroll, reddit_search_open, amazon_search_buy, "
                    "web_form_fill_submit, calendar_book, email_compose_send, youtube_media_control, "
                    "train_search_book, flight_search_book, food_search_order, bill_payment_review, none."
                ),
                "For multi_tab_research: extract URLs into the urls[] array.",
                "For web_form_fill_submit: extract field values into the fields{} object, target URL into site_name.",
                "For calendar_book: set query to event title, action to time/date string.",
                "For email_compose_send: set query to subject, site_name to To address.",
                f"Current browser site context: {infer_site_from_runtime(runtime_state) or ''}",
                f"Previous browser task state: {json.dumps(task_state or {}, ensure_ascii=False)}",
                f"User message: {message}",
            ]
        )
        try:
            result = await self.tool_registry.dispatch("llm_task", {"prompt": prompt, "model": "cheap"})
        except Exception:
            return None
        payload = self._parse_classifier_payload(str(result.get("content", "")).strip())
        if payload is None:
            return None
        recipe_name = str(payload.get("recipe_name", "none")).strip()
        if recipe_name not in {*RECIPE_BY_NAME.keys(), "none"}:
            return None
        try:
            confidence = float(payload.get("confidence", 0))
        except (TypeError, ValueError):
            return None
        low_confidence = float(getattr(self.config.browser_workflows, "disambiguation_confidence_low", 0.5))
        high_confidence = float(getattr(self.config.browser_workflows, "disambiguation_confidence_high", self.config.browser_workflows.classifier_confidence_threshold))
        effective_high = high_confidence
        active_task = active_browser_task(task_state)
        if active_task and self._looks_short_followup(message):
            effective_high = low_confidence

        if recipe_name == "none" or confidence < low_confidence:
            return None
        site_raw = str(payload.get("site_name", "")).strip()
        normalized_site = normalize_site_name(site_raw)
        raw_site, raw_url = normalize_browser_target(site_raw)
        site_name = normalized_site or raw_site or infer_site_from_runtime(runtime_state)
        query = _normalize_quotes(str(payload.get("query", "")).strip()) or None
        action = _normalize_quotes(str(payload.get("action", "")).strip()) or None
        details: dict[str, Any] = {"task_state": task_state}
        if raw_url:
            details["target_url"] = raw_url
        # Carry through LLM-extracted structured fields
        if payload.get("urls"):
            details["urls"] = list(payload["urls"])
        if payload.get("fields"):
            details["fields"] = dict(payload["fields"])
        # For email: site_name used as To address hack
        if recipe_name == "email_compose_send" and site_raw and "@" in site_raw:
            details["to"] = site_raw
            details["subject"] = query or ""
            site_name = "gmail"
        # For calendar_book: action used as start time
        if recipe_name == "calendar_book" and action:
            details["title"] = query or ""
            details["start_time"] = action
        if confidence < effective_high:
            details["needs_disambiguation"] = True
            details["intended_action"] = action or ""
            return BrowserWorkflowMatch(
                recipe_name=recipe_name,
                confidence=confidence,
                site_name=site_name,
                query=query,
                action="disambiguate",
                open_first_result=bool(payload.get("open_first_result")),
                details=details,
            )
        return BrowserWorkflowMatch(
            recipe_name=recipe_name,
            confidence=confidence,
            site_name=site_name,
            query=query,
            action=action,
            open_first_result=bool(payload.get("open_first_result")),
            details=details,
        )

    def _parse_classifier_payload(self, content: str) -> dict[str, Any] | None:
        if not content:
            return None
        candidate = content
        fenced = re.search(r"\{.*\}", candidate, flags=re.DOTALL)
        if fenced is not None:
            candidate = fenced.group(0)
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def _looks_short_followup(self, message: str) -> bool:
        compact = _normalize_spaces(message)
        if not compact:
            return False
        if len(compact.split()) <= 4:
            return True
        return compact.lower() in {"this", "that", "it", "there", "here", "the first one", "the second one"}

    def _extract_train_search(self, message: str) -> dict[str, Any] | None:
        normalized = _normalize_spaces(message)
        if not any(token in normalized.lower() for token in ("irctc", "train", "railway")):
            return None
        match = re.search(
            r"\b(?:train|trains|ticket|tickets).+?\bfrom\s+(.+?)\s+\bto\s+(.+?)(?:\s+\bon\s+(.+))?$",
            normalized,
            flags=re.IGNORECASE,
        )
        if match is None:
            match = re.search(
                r"\bfrom\s+(.+?)\s+\bto\s+(.+?)(?:\s+\bon\s+(.+))?(?:\s+\bon\s+irctc|\s+train.*)$",
                normalized,
                flags=re.IGNORECASE,
            )
        if match is None:
            return None
        source = _normalize_quotes(match.group(1))
        destination = _normalize_quotes(match.group(2))
        travel_date = _normalize_quotes(match.group(3) or "")
        if not source or not destination:
            return None
        return {
            "source": source,
            "destination": destination,
            "travel_date": travel_date,
            "route": f"{source} to {destination}",
        }

    def _extract_flight_search(self, message: str) -> dict[str, Any] | None:
        normalized = _normalize_spaces(message)
        lowered = normalized.lower()
        if "flight" not in lowered:
            return None
        site_name = "makemytrip" if "makemytrip" in lowered or "make my trip" in lowered else (
            "cleartrip" if "cleartrip" in lowered or "clear trip" in lowered else "makemytrip"
        )
        match = re.search(r"\bfrom\s+(.+?)\s+\bto\s+(.+?)(?:\s+\bon\s+(.+))?$", normalized, flags=re.IGNORECASE)
        if match is None:
            return None
        source = _normalize_quotes(match.group(1))
        destination = _normalize_quotes(match.group(2))
        travel_date = _normalize_quotes(match.group(3) or "")
        if not source or not destination:
            return None
        return {
            "site_name": site_name,
            "source": source,
            "destination": destination,
            "travel_date": travel_date,
            "route": f"{source} to {destination}",
        }

    def _extract_food_order(self, message: str) -> dict[str, Any] | None:
        normalized = _normalize_spaces(message)
        lowered = normalized.lower()
        site_name = None
        if "swiggy" in lowered:
            site_name = "swiggy"
        elif "zomato" in lowered:
            site_name = "zomato"
        if site_name is None:
            return None
        match = re.search(r"\b(?:search|find|order|get)\s+(.+?)\s+\b(?:on|in)\s+(swiggy|zomato)\b", normalized, flags=re.IGNORECASE)
        if match:
            query = _normalize_quotes(match.group(1))
        else:
            query = normalized
            for prefix in ("open swiggy and ", "open zomato and "):
                if lowered.startswith(prefix):
                    query = normalized[len(prefix):]
                    break
            query = re.sub(r"\b(?:search|find|order|get)\b", "", query, flags=re.IGNORECASE).strip()
        query = _normalize_quotes(query)
        return {"site_name": site_name, "query": query or ""}

    def _extract_bill_payment_review(self, message: str) -> dict[str, Any] | None:
        normalized = _normalize_spaces(message)
        lowered = normalized.lower()
        site_name = None
        for candidate in ("paytm", "hdfc netbanking", "sbi netbanking", "hdfc", "sbi"):
            if candidate in lowered:
                site_name = normalize_site_name(candidate) or candidate
                break
        if site_name is None:
            return None
        if not any(token in lowered for token in ("bill", "payment", "netbanking", "banking", "login", "review", "account")):
            return None
        return {
            "site_name": site_name,
            "query": normalized,
            "read_only": True,
        }

    def _looks_like_continue(self, lowered: str) -> bool:
        normalized = _normalize_spaces(lowered)
        direct = {
            "continue",
            "continue browser task",
            "continue the browser task",
            "open the first result",
            "play that one",
            "play it",
        }
        if normalized in direct:
            return True
        return normalized.startswith("continue ")

    def _extract_owner_repo(self, message: str) -> tuple[str, str] | None:
        match = re.search(r"\b([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.-]+)\b", message)
        if not match:
            return None
        return match.group(1), match.group(2)

    def _extract_repo_name_hint(self, message: str) -> str | None:
        patterns = (
            r"\bthe\s+(.+?)\s+repo\b",
            r"\babout\s+(.+?)\s+repo\b",
            r"\bissue\s+on\s+the\s+(.+?)\s+repo\b",
            r"\bissue\s+in\s+the\s+(.+?)\s+repo\b",
        )
        for pattern in patterns:
            match = re.search(pattern, message, flags=re.IGNORECASE)
            if not match:
                continue
            value = _normalize_quotes(match.group(1))
            if value and value.lower() not in {"this", "that"}:
                return value
        if re.search(r"\bthis repo\b", message, flags=re.IGNORECASE):
            return "this repo"
        return None

    # ── New recipe extractors ─────────────────────────────────────────────────────────

    def _extract_youtube_media_action(
        self,
        message: str,
        *,
        runtime_state: dict[str, Any] | None,
        active_task: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Detect media control commands for a YouTube video."""
        normalized = _normalize_spaces(message.lower())
        active_tab = ((runtime_state or {}).get("active_tab") or {}) if isinstance(runtime_state or {}, dict) else {}
        active_url = str(active_tab.get("url", "") or active_task.get("target_url", "") or "")
        has_active_youtube_video = bool(
            re.search(r"(youtube\.com/(watch|shorts)|youtu\.be/)", active_url, flags=re.IGNORECASE)
        )
        # Pause
        if has_active_youtube_video and re.search(r"\b(?:pause|stop)(?: the)?(?: video| music| youtube| playback)?\b", normalized):
            return {"action": "pause"}
        # Resume/play (only if no search keyword present)
        if has_active_youtube_video and re.search(r"\b(?:resume|unpause|play(?: the)?(?: video| youtube| music)?)\b", normalized) and "search" not in normalized:
            return {"action": "play"}
        # Mute
        if has_active_youtube_video and re.search(r"\bmute(?: the)?(?: video| youtube)?\b", normalized):
            return {"action": "mute"}
        # Unmute
        if has_active_youtube_video and re.search(r"\bunmute(?: the)?(?: video| youtube)?\b", normalized):
            return {"action": "unmute"}
        # Seek forward
        seek_m = re.search(r"\b(?:skip|forward|seek)(?: ahead)?\s+(\d+)\s*(?:s|sec(?:ond)?s?)\b", normalized) if has_active_youtube_video else None
        if seek_m:
            return {"action": "seek", "seek_seconds": int(seek_m.group(1))}
        # Rewind
        back_m = re.search(r"\b(?:rewind|go back|back)(?: by)?\s+(\d+)\s*(?:s|sec(?:ond)?s?)\b", normalized) if has_active_youtube_video else None
        if back_m:
            return {"action": "back", "seek_seconds": int(back_m.group(1))}
        # Fullscreen
        if has_active_youtube_video and re.search(r"\b(?:fullscreen|full screen|full-screen)\b", normalized):
            return {"action": "fullscreen"}
        return None

    def _extract_twitter_query(self, message: str) -> str | None:
        """Detect Twitter/X search intent."""
        normalized = _normalize_spaces(message)
        patterns = (
            r"^(?:please\s+)?search\s+(?:twitter|x\.?com|x)\s+for\s+(.+)$",
            r"^(?:please\s+)?(?:open|go to)\s+(?:twitter|x\.?com)(?:\s+and\s+(?:search|look up|find))\s+(.+)$",
            r"^(?:please\s+)?(?:look up|find)\s+(.+?)\s+on\s+(?:twitter|x\.?com|x)$",
            r"^(?:please\s+)?(?:search|find|look up)\s+(#[\w]+)(?:\s+on\s+(?:twitter|x))?$",
        )
        for pattern in patterns:
            match = re.match(pattern, normalized, flags=re.IGNORECASE)
            if match:
                return _normalize_quotes(match.group(1))
        return None

    def _extract_reddit_query(self, message: str) -> str | None:
        """Detect Reddit search intent."""
        normalized = _normalize_spaces(message)
        patterns = (
            r"^(?:please\s+)?search\s+reddit\s+for\s+(.+)$",
            r"^(?:please\s+)?(?:open\s+)?reddit(?:\.com)?(?:\s+and\s+)?(?:search|find|look for|look up)\s+(.+)$",
            r"^(?:please\s+)?(?:find|look for|search for)\s+(.+?)\s+on\s+reddit$",
            r"^(?:please\s+)?what does reddit say about\s+(.+)$",
        )
        for pattern in patterns:
            match = re.match(pattern, normalized, flags=re.IGNORECASE)
            if match:
                return _normalize_quotes(match.group(1))
        return None

    def _extract_amazon_query(self, message: str) -> dict[str, Any] | None:
        """Detect Amazon/Flipkart product search intent."""
        normalized = _normalize_spaces(message)
        # Flipkart
        flipkart_m = re.search(
            r"(?:search|find|look for|look up)\s+(.+?)\s+on\s+flipkart"
            r"|flipkart(?:\.com)?(?:\s+and\s+)?(?:search|find\s+for)?\s+(.+)",
            normalized, flags=re.IGNORECASE,
        )
        if flipkart_m:
            query = _normalize_quotes(flipkart_m.group(1) or flipkart_m.group(2) or "")
            if query:
                return {"site_name": "flipkart", "query": query}
        # Amazon
        amazon_m = re.search(
            r"(?:search|find|look for|look up)\s+(.+?)\s+on\s+amazon"
            r"|amazon(?:\.(?:com|in))?(?:\s+and)?\s+(?:search|find|look for)?\s+(.+)",
            normalized, flags=re.IGNORECASE,
        )
        if amazon_m:
            query = _normalize_quotes(amazon_m.group(1) or amazon_m.group(2) or "")
            if query:
                return {"site_name": "amazon", "query": query}
        return None

    def _extract_page_summarize_url(self, message: str) -> str | None:
        """Detect page-read/summarize intent with a URL."""
        def _normalize_page_url(candidate: str) -> str | None:
            value = candidate.strip()
            if not value:
                return None
            lowered = value.lower()
            common_file_extensions = {
                ".md",
                ".txt",
                ".pdf",
                ".doc",
                ".docx",
                ".json",
                ".yaml",
                ".yml",
                ".toml",
                ".csv",
                ".xml",
                ".py",
                ".js",
                ".ts",
                ".tsx",
                ".jsx",
                ".html",
            }
            if not lowered.startswith(("http://", "https://", "www.")) and any(lowered.endswith(ext) for ext in common_file_extensions):
                return None
            if not value.startswith("http"):
                value = f"https://{value}"
            return value

        normalized = _normalize_spaces(message)
        # Explicit summarize/read + URL
        m = re.search(
            r"(?:summarize|read|summarise|what does this page say)[:\s]+"
            r"((?:https?://)?[a-z0-9][a-z0-9._/-]*\.[a-z]{2,}(?:[/?#][^\s]*)?)",
            normalized, flags=re.IGNORECASE,
        )
        if m:
            return _normalize_page_url(m.group(1))
        # Pattern: "summarize <url>"
        m2 = re.match(
            r"^(?:please\s+)?(?:summarize|summarise|read and summarize|read)\s+"
            r"((?:https?://)?[a-z0-9][a-z0-9._/-]*\.[a-z]{2,}(?:[/?#][^\s]*)?)$",
            normalized, flags=re.IGNORECASE,
        )
        if m2:
            return _normalize_page_url(m2.group(1))
        return None

    def _site_from_url_quick(self, url: str) -> str | None:
        """Quick netloc extraction without normalization."""
        try:
            from urllib.parse import urlparse as _up
            return _up(url).netloc.strip().lower() or None
        except Exception:
            return None
