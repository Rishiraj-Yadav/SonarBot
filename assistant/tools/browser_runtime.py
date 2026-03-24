"""Stateful Playwright runtime used by browser automation tools."""

from __future__ import annotations

import asyncio
import base64
import json
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

try:  # pragma: no cover - optional dependency
    from bs4 import BeautifulSoup
except Exception:  # pragma: no cover - optional dependency
    BeautifulSoup = None


BrowserEventEmitter = Callable[[str, str, dict[str, Any]], Awaitable[Any]]
BrowserViewerChecker = Callable[[str], bool]


@dataclass(slots=True)
class BrowserTabState:
    tab_id: str
    page: Any
    created_at: str
    title: str = ""
    url: str = ""
    dom_snapshot: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class BrowserRuntime:
    config: Any
    event_emitter: BrowserEventEmitter | None = None
    viewer_checker: BrowserViewerChecker | None = None
    playwright: Any | None = None
    browser: Any | None = None
    context: Any | None = None
    current_profile_key: str | None = None
    current_tab_id: str | None = None
    current_headless: bool | None = None
    current_user_id: str | None = None
    sessions_dir: Path = field(init=False)
    session_index_path: Path = field(init=False)
    screenshots_dir: Path = field(init=False)
    downloads_dir: Path = field(init=False)
    _tabs: dict[str, BrowserTabState] = field(init=False, default_factory=dict)
    _page_tab_ids: dict[int, str] = field(init=False, default_factory=dict)
    _recent_logs: deque[dict[str, Any]] = field(init=False)
    _recent_downloads: deque[dict[str, Any]] = field(init=False)
    _stream_task: asyncio.Task[None] | None = field(init=False, default=None)
    _streaming_user_id: str | None = field(init=False, default=None)
    _pending_login: dict[str, Any] | None = field(init=False, default=None)
    _tab_counter: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        self.sessions_dir = self._resolve_workspace_subdir(self.config.tools.browser_profiles_subdir)
        self.session_index_path = self.sessions_dir / "index.json"
        self.screenshots_dir = self._resolve_workspace_subdir(self.config.tools.browser_screenshots_subdir)
        self.downloads_dir = self._resolve_workspace_subdir(self.config.tools.browser_downloads_subdir)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        retention = max(20, int(getattr(self.config.tools, "browser_log_retention", 200)))
        self._recent_logs = deque(maxlen=retention)
        self._recent_downloads = deque(maxlen=retention)

    async def get_page(
        self,
        target_url: str | None = None,
        *,
        profile_name: str | None = None,
        tab_id: str | None = None,
        user_id: str | None = None,
        headless: bool | None = None,
    ):
        await self._ensure_playwright()
        desired_headless = self.config.tools.browser_headless if headless is None else headless
        desired_profile = self.match_profile(target_url, profile_name=profile_name)

        if (
            self.context is None
            or self.current_headless != desired_headless
            or (
                desired_profile is not None
                and desired_profile.get("profile_key") != self.current_profile_key
            )
            or (desired_profile is not None and self.current_profile_key is None)
        ):
            await self._reset_context(
                storage_state=str(desired_profile.get("storage_path", "")) or None if desired_profile else None,
                headless=desired_headless,
                profile=desired_profile,
                user_id=user_id,
            )

        if self.context is None:
            await self._reset_context(headless=desired_headless, profile=desired_profile, user_id=user_id)

        if tab_id and tab_id in self._tabs:
            self.current_tab_id = tab_id
            await self._emit_state(user_id or self.current_user_id)
            return self._tabs[tab_id].page

        if self.current_tab_id and self.current_tab_id in self._tabs:
            return self._tabs[self.current_tab_id].page

        assert self.context is not None
        page = await self.context.new_page()
        state = await self._register_page(page, make_current=True, user_id=user_id or self.current_user_id)
        await self._refresh_tab_state(state.tab_id, user_id or self.current_user_id)
        return page

    async def start_login(self, site_name: str, profile_name: str, login_url: str, *, user_id: str | None = None):
        await self._ensure_playwright()
        self._pending_login = {
            "site_name": site_name,
            "profile_name": self._normalize_profile_name(profile_name),
            "login_url": login_url,
        }
        await self._reset_context(storage_state=None, headless=False, profile=None, user_id=user_id)
        assert self.current_tab_id is not None
        page = self._tabs[self.current_tab_id].page
        await page.goto(login_url, wait_until="domcontentloaded")
        await self._refresh_tab_state(self.current_tab_id, user_id)
        return page

    async def save_login_session(self, site_name: str, profile_name: str, login_url: str) -> dict[str, Any]:
        if self.context is None or self.current_tab_id is None:
            raise RuntimeError("No active browser context is available for login.")
        page = self._tabs[self.current_tab_id].page
        normalized_profile = self._normalize_profile_name(profile_name)
        profile_key = profile_key_for(site_name, normalized_profile)
        state_path = self.sessions_dir / f"{slugify(site_name)}--{slugify(normalized_profile)}.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        await self.context.storage_state(path=str(state_path))
        final_url = page.url or login_url
        domain = urlparse(final_url or login_url).netloc.lower()
        index = self._load_index()
        index[profile_key] = {
            "profile_key": profile_key,
            "site_name": site_name,
            "profile_name": normalized_profile,
            "domain": domain,
            "storage_path": str(state_path),
            "login_url": login_url,
            "status": "active",
            "last_error": "",
            "last_used_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save_index(index)
        self.current_profile_key = profile_key
        await self._emit_state(self.current_user_id)
        return dict(index[profile_key])

    def list_sessions(self) -> list[dict[str, Any]]:
        sessions = list(self._load_index().values())
        sessions.sort(
            key=lambda item: (
                str(item.get("status", "active")) == "active",
                str(item.get("last_used_at", "")),
            ),
            reverse=True,
        )
        return sessions

    def match_profile(self, url: str | None, *, profile_name: str | None = None) -> dict[str, Any] | None:
        if not url:
            return None
        target_domain = urlparse(url).netloc.lower()
        if not target_domain:
            return None
        normalized_profile = self._normalize_profile_name(profile_name) if profile_name else None
        candidates: list[dict[str, Any]] = []
        for session in self._load_index().values():
            session_domain = str(session.get("domain", "")).lower()
            session_profile = self._normalize_profile_name(str(session.get("profile_name", "default")))
            if normalized_profile is not None and normalized_profile != session_profile:
                continue
            if session_domain and (
                target_domain == session_domain
                or target_domain.endswith(f".{session_domain}")
                or session_domain.endswith(f".{target_domain}")
            ):
                candidates.append(session)
        if not candidates:
            return None
        candidates.sort(
            key=lambda item: (
                str(item.get("status", "active")) == "active",
                str(item.get("last_used_at", "")),
            ),
            reverse=True,
        )
        return candidates[0]

    def match_session(self, url: str) -> dict[str, Any] | None:
        return self.match_profile(url)

    def touch_session(self, site_name: str, profile_name: str = "default") -> None:
        key = profile_key_for(site_name, self._normalize_profile_name(profile_name))
        index = self._load_index()
        if key not in index:
            return
        index[key]["last_used_at"] = datetime.now(timezone.utc).isoformat()
        if str(index[key].get("status", "active")) != "expired":
            index[key]["status"] = "active"
        self._save_index(index)

    def mark_profile_status(
        self,
        site_name: str,
        profile_name: str,
        *,
        status: str,
        last_error: str = "",
    ) -> dict[str, Any] | None:
        key = profile_key_for(site_name, self._normalize_profile_name(profile_name))
        index = self._load_index()
        record = index.get(key)
        if record is None:
            return None
        record["status"] = status
        record["last_error"] = last_error
        record["last_used_at"] = datetime.now(timezone.utc).isoformat()
        self._save_index(index)
        return dict(record)

    def expire_session(self, site_name: str, profile_name: str = "default") -> None:
        self.mark_profile_status(site_name, profile_name, status="expired")

    async def open_tab(
        self,
        *,
        url: str | None = None,
        profile_name: str | None = None,
        user_id: str | None = None,
        wait_for: str | None = None,
        timeout_seconds: int = 30,
    ) -> dict[str, Any]:
        await self.get_page(target_url=url, profile_name=profile_name, user_id=user_id)
        if self.context is None:
            raise RuntimeError("No active browser context is available.")
        new_page = await self.context.new_page()
        state = await self._register_page(new_page, make_current=True, user_id=user_id or self.current_user_id)
        if url:
            await new_page.goto(url, wait_until=self.wait_state_for_navigation(wait_for))
            await self.post_action_wait(new_page, wait_for, timeout_seconds)
        await self._refresh_tab_state(state.tab_id, user_id or self.current_user_id)
        return self.tab_payload(state.tab_id)

    async def switch_tab(self, tab_id: str, *, user_id: str | None = None) -> dict[str, Any]:
        if tab_id not in self._tabs:
            raise RuntimeError(f"Unknown browser tab '{tab_id}'.")
        self.current_tab_id = tab_id
        await self._refresh_tab_state(tab_id, user_id or self.current_user_id)
        return self.tab_payload(tab_id)

    async def close_tab(self, tab_id: str, *, user_id: str | None = None) -> dict[str, Any]:
        if tab_id not in self._tabs:
            raise RuntimeError(f"Unknown browser tab '{tab_id}'.")
        await self._tabs[tab_id].page.close()
        self._drop_tab(tab_id)
        if self.current_tab_id not in self._tabs:
            self.current_tab_id = next(iter(self._tabs), None)
        await self._emit_state(user_id or self.current_user_id)
        return {"tab_id": tab_id, "closed": True}

    def list_tabs(self) -> list[dict[str, Any]]:
        return [self.tab_payload(tab_id) for tab_id in self._tabs]

    def list_logs(self, limit: int = 50) -> list[dict[str, Any]]:
        items = list(self._recent_logs)
        items.sort(key=lambda item: str(item.get("timestamp", "")), reverse=True)
        return items[: max(1, min(limit, 200))]

    def list_downloads(self, limit: int = 50) -> list[dict[str, Any]]:
        items = list(self._recent_downloads)
        if self.downloads_dir.exists():
            for candidate in self.downloads_dir.glob("**/*"):
                if not candidate.is_file():
                    continue
                items.append(
                    {
                        "path": str(candidate),
                        "filename": candidate.name,
                        "profile_key": self.profile_key_from_download_path(candidate),
                        "size": candidate.stat().st_size,
                        "created_at": datetime.fromtimestamp(candidate.stat().st_mtime, tz=timezone.utc).isoformat(),
                    }
                )
        deduped: dict[str, dict[str, Any]] = {}
        for item in items:
            deduped[str(item.get("path", ""))] = item
        values = list(deduped.values())
        values.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
        return values[: max(1, min(limit, 200))]

    def current_state(self) -> dict[str, Any]:
        profile = self._load_index().get(self.current_profile_key or "", None)
        return {
            "active": self.context is not None,
            "headless": bool(self.current_headless),
            "active_profile": profile,
            "current_tab_id": self.current_tab_id,
            "tabs": self.list_tabs(),
            "recent_logs": self.list_logs(limit=8),
            "recent_downloads": self.list_downloads(limit=8),
            "streaming": bool(self._stream_task and not self._stream_task.done()),
        }

    async def stream_screenshot_payload(self) -> dict[str, Any] | None:
        if self.current_tab_id is None or self.current_tab_id not in self._tabs:
            return None
        page = self._tabs[self.current_tab_id].page
        screenshot_bytes = await page.screenshot(type="jpeg", quality=65)
        return {
            "tab_id": self.current_tab_id,
            "url": self._tabs[self.current_tab_id].url,
            "title": self._tabs[self.current_tab_id].title,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "image_data_url": "data:image/jpeg;base64," + base64.b64encode(screenshot_bytes).decode("ascii"),
        }

    async def capture_dom_snapshot(self, page: Any) -> dict[str, Any]:
        title = await page.title()
        html = await page.content()
        snapshot: dict[str, Any] = {
            "url": page.url,
            "title": title,
            "text": extract_visible_text(html)[:1600],
            "buttons": [],
            "links": [],
            "inputs": [],
        }
        if BeautifulSoup is not None:
            soup = BeautifulSoup(html, "html.parser")
            snapshot["buttons"] = [item.get_text(" ", strip=True) for item in soup.find_all("button")[:8] if item.get_text(" ", strip=True)]
            snapshot["links"] = [
                item.get_text(" ", strip=True) or item.get("href", "")
                for item in soup.find_all("a")[:8]
                if (item.get_text(" ", strip=True) or item.get("href", ""))
            ]
            inputs: list[str] = []
            for item in soup.find_all(["input", "textarea", "select"])[:8]:
                inputs.append(
                    item.get("aria-label")
                    or item.get("placeholder")
                    or item.get("name")
                    or item.get("id")
                    or item.name
                )
            snapshot["inputs"] = [value for value in inputs if value]
        return snapshot

    async def resolve_locator(self, page: Any, selector: str, *, timeout_seconds: int = 10, state: str = "visible"):
        timeout_ms = max(1000, int(timeout_seconds * 1000))
        candidates: list[tuple[str, Any]] = [("css", page.locator(selector).first)]
        candidates.append(("label", page.get_by_label(selector).first))
        candidates.append(("placeholder", page.get_by_placeholder(selector).first))
        candidates.append(("text", page.get_by_text(selector, exact=False).first))
        for role in ("button", "link", "textbox", "combobox", "checkbox", "radio"):
            candidates.append((f"role:{role}", page.get_by_role(role, name=selector, exact=False).first))
        for strategy, locator in candidates:
            try:
                await locator.wait_for(state=state, timeout=timeout_ms)
                return locator, strategy
            except Exception:
                continue
        raise RuntimeError(f"Could not find a browser element for '{selector}'.")

    async def ensure_workspace_file(self, relative_path: str) -> Path:
        candidate = Path(relative_path).expanduser()
        if not candidate.is_absolute():
            candidate = self.config.agent.workspace_dir / candidate
        resolved = candidate.resolve()
        workspace_root = self.config.agent.workspace_dir.resolve()
        if not resolved.is_relative_to(workspace_root):
            raise RuntimeError("Browser uploads must come from inside the workspace directory.")
        if not resolved.exists():
            raise RuntimeError(f"Workspace file not found: {resolved}")
        return resolved

    async def close(self) -> None:
        await self._stop_streaming()
        if self.context is not None:
            await self.context.close()
        if self.browser is not None:
            await self.browser.close()
        if self.playwright is not None:
            await self.playwright.stop()
        self.context = None
        self.browser = None
        self.playwright = None
        self.current_profile_key = None
        self.current_tab_id = None
        self.current_headless = None
        self.current_user_id = None
        self._tabs.clear()
        self._page_tab_ids.clear()

    async def _ensure_playwright(self) -> None:
        if self.playwright is not None:
            return
        try:
            from playwright.async_api import async_playwright  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "Playwright is not installed. Run `uv sync --extra dev` and `playwright install chromium`."
            ) from exc
        self.playwright = await async_playwright().start()

    async def _reset_context(
        self,
        *,
        storage_state: str | None = None,
        headless: bool | None = None,
        profile: dict[str, Any] | None = None,
        user_id: str | None = None,
    ) -> None:
        await self._ensure_playwright()
        desired_headless = self.config.tools.browser_headless if headless is None else headless
        if self.browser is None or self.current_headless != desired_headless:
            if self.browser is not None:
                await self.browser.close()
            assert self.playwright is not None
            self.browser = await self.playwright.chromium.launch(headless=desired_headless)
        await self._stop_streaming()
        if self.context is not None:
            await self.context.close()
        context_kwargs: dict[str, Any] = {"accept_downloads": True}
        if storage_state:
            context_kwargs["storage_state"] = storage_state
        self.context = await self.browser.new_context(**context_kwargs)
        self.context.on("page", lambda page: asyncio.create_task(self._register_page(page, make_current=True, user_id=user_id)))
        self._tabs.clear()
        self._page_tab_ids.clear()
        self.current_profile_key = str(profile.get("profile_key", "")) or None if profile else None
        self.current_tab_id = None
        self.current_headless = desired_headless
        self.current_user_id = user_id or self.current_user_id
        page = await self.context.new_page()
        state = await self._register_page(page, make_current=True, user_id=user_id or self.current_user_id)
        await self._refresh_tab_state(state.tab_id, user_id or self.current_user_id)
        if not desired_headless and (user_id or self.current_user_id):
            await self._start_streaming(user_id or self.current_user_id)

    async def _register_page(self, page: Any, *, make_current: bool = False, user_id: str | None = None) -> BrowserTabState:
        existing_id = self._page_tab_ids.get(id(page))
        if existing_id is not None and existing_id in self._tabs:
            if make_current:
                self.current_tab_id = existing_id
            return self._tabs[existing_id]
        self._tab_counter += 1
        tab_id = f"tab-{self._tab_counter}"
        state = BrowserTabState(tab_id=tab_id, page=page, created_at=datetime.now(timezone.utc).isoformat())
        self._tabs[tab_id] = state
        self._page_tab_ids[id(page)] = tab_id
        if make_current or self.current_tab_id is None:
            self.current_tab_id = tab_id
        page.on("console", lambda message: self._record_console_log(tab_id, page, message, user_id))
        page.on("pageerror", lambda error: self._record_log(tab_id, page, "pageerror", str(error), "error", user_id))
        page.on("requestfailed", lambda request: self._record_request_failed(tab_id, page, request, user_id))
        page.on("response", lambda response: self._record_response(tab_id, page, response, user_id))
        page.on("download", lambda download: asyncio.create_task(self._handle_download(tab_id, page, download, user_id)))
        page.on("close", lambda: self._drop_tab(tab_id))
        return state

    def _drop_tab(self, tab_id: str) -> None:
        state = self._tabs.pop(tab_id, None)
        if state is None:
            return
        self._page_tab_ids.pop(id(state.page), None)
        if self.current_tab_id == tab_id:
            self.current_tab_id = next(iter(self._tabs), None)

    def _record_console_log(self, tab_id: str, page: Any, message: Any, user_id: str | None) -> None:
        try:
            text = message.text
        except Exception:
            text = str(message)
        level = getattr(message, "type", "log")
        self._record_log(tab_id, page, "console", str(text), str(level), user_id)

    def _record_request_failed(self, tab_id: str, page: Any, request: Any, user_id: str | None) -> None:
        failure = getattr(request, "failure", None)
        failure_text = ""
        if callable(failure):
            try:
                info = failure()
                if isinstance(info, dict):
                    failure_text = str(info.get("errorText", ""))
            except Exception:
                failure_text = ""
        method = getattr(request, "method", "GET")
        url = getattr(request, "url", "")
        self._record_log(tab_id, page, "network", f"Request failed: {method} {url} {failure_text}".strip(), "error", user_id)

    def _record_response(self, tab_id: str, page: Any, response: Any, user_id: str | None) -> None:
        try:
            status = int(response.status)
        except Exception:
            return
        if status < 400:
            return
        self._record_log(tab_id, page, "network", f"HTTP {status} {getattr(response, 'url', '')}".strip(), "warning", user_id)

    def _record_log(self, tab_id: str, page: Any, kind: str, message: str, level: str, user_id: str | None) -> None:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "level": level,
            "message": message,
            "tab_id": tab_id,
            "url": getattr(page, "url", ""),
            "profile_key": self.current_profile_key,
        }
        self._recent_logs.append(entry)
        target_user = user_id or self.current_user_id
        if target_user:
            asyncio.create_task(self._emit_browser_event(target_user, "browser.log", entry))

    async def _handle_download(self, tab_id: str, page: Any, download: Any, user_id: str | None) -> None:
        profile = self._load_index().get(self.current_profile_key or "", None)
        profile_dir = self.profile_download_dir(profile)
        profile_dir.mkdir(parents=True, exist_ok=True)
        target = dedupe_path(profile_dir / str(download.suggested_filename))
        await download.save_as(str(target))
        entry = {
            "path": str(target),
            "filename": target.name,
            "profile_key": self.current_profile_key,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "size": target.stat().st_size if target.exists() else 0,
            "tab_id": tab_id,
            "url": getattr(page, "url", ""),
        }
        self._recent_downloads.append(entry)
        target_user = user_id or self.current_user_id
        if target_user:
            await self._emit_browser_event(target_user, "browser.download", entry)
            await self._emit_state(target_user)

    async def _refresh_tab_state(self, tab_id: str, user_id: str | None = None) -> None:
        state = self._tabs.get(tab_id)
        if state is None:
            return
        try:
            state.title = await state.page.title()
        except Exception:
            state.title = state.title or ""
        try:
            state.url = state.page.url or state.url
        except Exception:
            state.url = state.url or ""
        try:
            state.dom_snapshot = await self.capture_dom_snapshot(state.page)
        except Exception:
            state.dom_snapshot = {"url": state.url, "title": state.title, "text": "", "buttons": [], "links": [], "inputs": []}
        if self.current_tab_id == tab_id:
            await self._emit_state(user_id or self.current_user_id)

    async def _emit_state(self, user_id: str | None) -> None:
        if not user_id:
            return
        await self._emit_browser_event(user_id, "browser.state", self.current_state())

    async def _emit_browser_event(self, user_id: str, event_name: str, payload: dict[str, Any]) -> None:
        if self.event_emitter is None:
            return
        try:
            await self.event_emitter(user_id, event_name, payload)
        except Exception:
            return

    async def _start_streaming(self, user_id: str) -> None:
        self._streaming_user_id = user_id
        if self._stream_task is not None and not self._stream_task.done():
            return
        self._stream_task = asyncio.create_task(self._stream_loop())

    async def _stop_streaming(self) -> None:
        if self._stream_task is None:
            return
        self._stream_task.cancel()
        try:
            await self._stream_task
        except asyncio.CancelledError:
            pass
        self._stream_task = None
        self._streaming_user_id = None

    async def _stream_loop(self) -> None:
        interval = max(1, int(getattr(self.config.tools, "browser_screenshot_stream_interval_seconds", 3)))
        while True:
            await asyncio.sleep(interval)
            if self.context is None or self.current_headless or self.current_tab_id not in self._tabs:
                continue
            user_id = self._streaming_user_id or self.current_user_id
            if not user_id:
                continue
            if self.viewer_checker is not None and not self.viewer_checker(user_id):
                continue
            payload = await self.stream_screenshot_payload()
            if payload is None:
                continue
            await self._emit_browser_event(user_id, "browser.screenshot", payload)
            await self._emit_state(user_id)

    async def post_action_wait(self, page: Any, wait_for: str | None, timeout_seconds: int) -> None:
        timeout_ms = max(1000, int(timeout_seconds * 1000))
        normalized = (wait_for or "").strip().lower()
        if normalized in {"load", "domcontentloaded", "networkidle"}:
            await page.wait_for_load_state(normalized, timeout=timeout_ms)
            return
        if normalized == "stable":
            await page.wait_for_timeout(300)
            return
        await page.wait_for_timeout(150)

    def wait_state_for_navigation(self, wait_for: str | None) -> str:
        normalized = (wait_for or "domcontentloaded").strip().lower()
        if normalized in {"load", "domcontentloaded", "networkidle"}:
            return normalized
        return "domcontentloaded"

    def tab_payload(self, tab_id: str) -> dict[str, Any]:
        state = self._tabs[tab_id]
        return {
            "tab_id": state.tab_id,
            "title": state.title,
            "url": state.url,
            "created_at": state.created_at,
            "active": state.tab_id == self.current_tab_id,
        }

    def profile_download_dir(self, profile: dict[str, Any] | None) -> Path:
        site_segment = slugify(str(profile.get("site_name", "unscoped"))) if profile else "unscoped"
        profile_segment = slugify(str(profile.get("profile_name", "default"))) if profile else "default"
        return self.downloads_dir / site_segment / profile_segment

    def profile_key_from_download_path(self, path: Path) -> str:
        try:
            relative = path.relative_to(self.downloads_dir)
        except Exception:
            return ""
        parts = relative.parts
        if len(parts) < 3:
            return ""
        return f"{parts[0]}::{parts[1]}"

    def _normalize_profile_name(self, profile_name: str | None) -> str:
        normalized = (profile_name or "default").strip()
        return normalized or "default"

    def _resolve_workspace_subdir(self, configured: str) -> Path:
        candidate = Path(configured).expanduser()
        if not candidate.is_absolute():
            candidate = self.config.agent.workspace_dir / candidate
        return candidate.resolve()

    def _load_index(self) -> dict[str, dict[str, Any]]:
        if not self.session_index_path.exists():
            return {}
        try:
            payload = json.loads(self.session_index_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(payload, dict):
            return {}
        normalized: dict[str, dict[str, Any]] = {}
        for key, value in payload.items():
            if not isinstance(value, dict):
                continue
            record = dict(value)
            site_name = str(record.get("site_name") or key)
            profile_name = self._normalize_profile_name(str(record.get("profile_name", "default")))
            profile_key = str(record.get("profile_key") or profile_key_for(site_name, profile_name))
            normalized[profile_key] = {
                "profile_key": profile_key,
                "site_name": site_name,
                "profile_name": profile_name,
                "domain": str(record.get("domain", "")),
                "storage_path": str(record.get("storage_path", "")),
                "login_url": str(record.get("login_url", "")),
                "status": str(record.get("status", "active")),
                "last_error": str(record.get("last_error", "")),
                "last_used_at": str(record.get("last_used_at", "")),
            }
        return normalized

    def _save_index(self, index: dict[str, dict[str, Any]]) -> None:
        self.session_index_path.parent.mkdir(parents=True, exist_ok=True)
        self.session_index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")


def extract_visible_text(html: str) -> str:
    if BeautifulSoup is not None:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text(" ", strip=True)
        return re.sub(r"\s+", " ", text).strip()
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def extract_table_from_html(html: str, *, max_rows: int = 25) -> tuple[list[str], list[list[str]]]:
    if BeautifulSoup is not None:
        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table")
        if table is None:
            return [], []
        headers = [cell.get_text(" ", strip=True) for cell in table.find_all("th")]
        rows: list[list[str]] = []
        for row in table.find_all("tr"):
            values = [cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"])]
            if values:
                rows.append(values)
            if len(rows) >= max_rows:
                break
        if headers and rows and rows[0] == headers:
            rows = rows[1:]
        return headers, rows
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.IGNORECASE | re.DOTALL)
    parsed_rows: list[list[str]] = []
    for row in rows[:max_rows]:
        values = re.findall(r"<(?:td|th)[^>]*>(.*?)</(?:td|th)>", row, flags=re.IGNORECASE | re.DOTALL)
        cleaned = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value)).strip() for value in values]
        if cleaned:
            parsed_rows.append(cleaned)
    if parsed_rows:
        return parsed_rows[0], parsed_rows[1:]
    return [], []


def looks_like_login_url(url: str) -> bool:
    lowered = url.lower()
    return any(token in lowered for token in ("/login", "/signin", "/sign-in", "/auth", "account/login"))


def profile_key_for(site_name: str, profile_name: str) -> str:
    return f"{slugify(site_name)}::{slugify(profile_name)}"


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-") or "session"


def dedupe_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(1, 1000):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    return path.with_name(f"{stem}-{datetime.now(timezone.utc).strftime('%H%M%S')}{suffix}")


def optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


async def wait_for_manual_login(page: Any, login_url: str, timeout_seconds: int) -> None:
    deadline = datetime.now(timezone.utc).timestamp() + timeout_seconds
    starting_domain = urlparse(login_url).netloc.lower()
    while datetime.now(timezone.utc).timestamp() < deadline:
        await page.wait_for_timeout(2000)
        current_url = page.url or ""
        current_domain = urlparse(current_url).netloc.lower()
        if current_url and not looks_like_login_url(current_url) and (
            current_domain == starting_domain
            or current_domain.endswith(f".{starting_domain}")
            or starting_domain.endswith(f".{current_domain}")
        ):
            return
    raise RuntimeError("Timed out waiting for manual browser login to complete.")
