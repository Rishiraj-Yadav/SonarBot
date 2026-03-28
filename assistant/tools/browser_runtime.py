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

from assistant.browser_workflows.site_adapters import get_site_adapter

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
class BrowserModeState:
    browser: Any | None = None
    context: Any | None = None
    current_profile_key: str | None = None
    current_tab_id: str | None = None
    current_user_id: str | None = None
    tabs: dict[str, BrowserTabState] = field(default_factory=dict)
    page_tab_ids: dict[int, str] = field(default_factory=dict)


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
    _pending_protected_action: dict[str, Any] | None = field(init=False, default=None)
    _mode_states: dict[str, BrowserModeState] = field(init=False, default_factory=dict)
    _active_mode: str = field(init=False, default="headless")
    _headed_idle_close_task: asyncio.Task[None] | None = field(init=False, default=None)
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
        self._mode_states = {
            "headless": BrowserModeState(),
            "headed": BrowserModeState(),
        }
        self._active_mode = self.default_mode()
        self.browser = None
        self.context = None
        self.current_profile_key = None
        self.current_tab_id = None
        self.current_headless = self._active_mode == "headless"
        self.current_user_id = None
        self._tabs = self._mode_states[self._active_mode].tabs
        self._page_tab_ids = self._mode_states[self._active_mode].page_tab_ids

    def default_mode(self) -> str:
        configured = str(getattr(self.config.browser_execution, "default_mode", "") or "").strip().lower()
        if configured in {"headless", "headed"}:
            return configured
        return "headless" if bool(getattr(self.config.tools, "browser_headless", True)) else "headed"

    def current_mode(self) -> str:
        return self._active_mode

    def _mode_from_headless(self, headless: bool | None) -> str:
        if headless is None:
            return self.default_mode()
        return "headless" if headless else "headed"

    def _mode_headless(self, mode: str) -> bool:
        return mode == "headless"

    def _active_state(self) -> BrowserModeState:
        return self._mode_states[self._active_mode]

    def _snapshot_active_mode(self) -> None:
        state = self._mode_states.get(self._active_mode)
        if state is None:
            return
        state.browser = self.browser
        state.context = self.context
        state.current_profile_key = self.current_profile_key
        state.current_tab_id = self.current_tab_id
        state.current_user_id = self.current_user_id
        state.tabs = self._tabs
        state.page_tab_ids = self._page_tab_ids

    def _activate_mode(self, mode: str, *, user_id: str | None = None) -> BrowserModeState:
        if mode not in self._mode_states:
            raise RuntimeError(f"Unknown browser execution mode '{mode}'.")
        self._snapshot_active_mode()
        self._active_mode = mode
        state = self._mode_states[mode]
        self.browser = state.browser
        self.context = state.context
        self.current_profile_key = state.current_profile_key
        self.current_tab_id = state.current_tab_id
        self.current_headless = self._mode_headless(mode)
        self.current_user_id = user_id or state.current_user_id or self.current_user_id
        self._tabs = state.tabs
        self._page_tab_ids = state.page_tab_ids
        state.current_user_id = self.current_user_id
        return state

    def _state_for_mode(self, mode: str) -> BrowserModeState:
        if mode == self._active_mode:
            self._snapshot_active_mode()
        return self._mode_states[mode]

    def _find_tab_mode(self, tab_id: str) -> str | None:
        self._snapshot_active_mode()
        for mode, state in self._mode_states.items():
            if tab_id in state.tabs:
                return mode
        return None

    def _state_has_activity(self, mode: str) -> bool:
        state = self._state_for_mode(mode)
        return state.context is not None and bool(state.tabs)

    def _normalized_tab_url(self, url: str | None) -> str:
        if not url:
            return ""
        parsed = urlparse(str(url))
        if not parsed.scheme or not parsed.netloc:
            return str(url).strip().lower()
        path = re.sub(r";jsessionid=[^/?#]+", "", parsed.path or "", flags=re.IGNORECASE)
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{path}"

    def _tab_matches_target(self, tab: BrowserTabState, *, target_url: str | None = None, site_name: str | None = None) -> bool:
        normalized_tab_url = self._normalized_tab_url(tab.url)
        if target_url:
            normalized_target = self._normalized_tab_url(target_url)
            if normalized_target and normalized_tab_url == normalized_target:
                return True
        if site_name:
            site_host = site_name.strip().lower()
            parsed = urlparse(tab.url or "")
            tab_host = parsed.netloc.lower()
            if tab_host and (
                tab_host == site_host
                or tab_host.endswith(f".{site_host}")
                or site_host.endswith(f".{tab_host}")
            ):
                return True
        return False

    def find_matching_tab(
        self,
        *,
        target_url: str | None = None,
        site_name: str | None = None,
        prefer_mode: str | None = None,
    ) -> dict[str, Any] | None:
        self._snapshot_active_mode()
        ordered_modes = [prefer_mode] if prefer_mode in self._mode_states else []
        ordered_modes.extend(mode for mode in ("headed", "headless") if mode not in ordered_modes)
        for mode in ordered_modes:
            state = self._mode_states[mode]
            for tab_id, tab in state.tabs.items():
                if self._tab_matches_target(tab, target_url=target_url, site_name=site_name):
                    payload = self.tab_payload(tab_id, mode=mode)
                    payload["mode"] = mode
                    return payload
        return None

    async def switch_to_matching_tab(
        self,
        *,
        target_url: str | None = None,
        site_name: str | None = None,
        prefer_mode: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any] | None:
        match = self.find_matching_tab(target_url=target_url, site_name=site_name, prefer_mode=prefer_mode)
        if match is None:
            return None
        await self.switch_tab(str(match["tab_id"]), user_id=user_id)
        return match

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
        desired_headless = self._mode_headless(self._mode_from_headless(headless))
        desired_mode = self._mode_from_headless(headless)
        self._activate_mode(desired_mode, user_id=user_id)
        desired_profile = self.match_profile(target_url, profile_name=profile_name)

        if (
            self.context is None
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

        existing_match = self.find_matching_tab(
            target_url=target_url,
            site_name=urlparse(target_url).netloc.lower() if target_url else None,
            prefer_mode=desired_mode,
        )
        if existing_match is not None and str(existing_match.get("mode")) == desired_mode:
            self.current_tab_id = str(existing_match["tab_id"])
            await self._refresh_tab_state(self.current_tab_id, user_id or self.current_user_id)
            return self._tabs[self.current_tab_id].page

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
        await self._cancel_headed_idle_close()
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
        self._pending_login = None
        await self._emit_state(self.current_user_id)
        return dict(index[profile_key])

    async def open_visible_intervention(
        self,
        url: str,
        *,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        current_profile_key = self.current_profile_key
        profile = self._load_index().get(self.current_profile_key or "", None)
        storage_state = str(profile.get("storage_path", "")) or None if profile else None
        await self._cancel_headed_idle_close()
        await self._reset_context(
            storage_state=storage_state,
            headless=False,
            profile=profile,
            user_id=user_id,
        )
        if profile is None and current_profile_key:
            self.current_profile_key = current_profile_key
        assert self.current_tab_id is not None
        page = self._tabs[self.current_tab_id].page
        await page.goto(url, wait_until="domcontentloaded")
        await self._refresh_tab_state(self.current_tab_id, user_id)
        await self._close_matching_tabs_in_mode(
            "headless",
            target_url=url,
            site_name=urlparse(url).netloc.lower() or None,
        )
        return {
            "url": page.url,
            "tab_id": self.current_tab_id,
            "headless": False,
        }

    async def finalize_pending_login_if_complete(self, *, user_id: str | None = None) -> dict[str, Any] | None:
        self._activate_mode("headed", user_id=user_id)
        if self._pending_login is None or self.current_tab_id is None or self.current_tab_id not in self._tabs:
            return None
        page = self._tabs[self.current_tab_id].page
        login_url = str(self._pending_login.get("login_url", "") or "")
        current_url = str(getattr(page, "url", "") or "")
        starting_domain = urlparse(login_url).netloc.lower()
        current_domain = urlparse(current_url).netloc.lower()
        if not current_url or looks_like_login_url(current_url):
            return None
        if not (
            current_domain == starting_domain
            or current_domain.endswith(f".{starting_domain}")
            or starting_domain.endswith(f".{current_domain}")
        ):
            return None
        saved = await self.save_login_session(
            str(self._pending_login.get("site_name", "")),
            str(self._pending_login.get("profile_name", "default")),
            login_url,
        )
        target_mode = self.default_mode()
        target_headless = self._mode_headless(target_mode)
        if not getattr(self.config.browser_execution, "revert_to_headless_after_manual_step", True):
            target_headless = False
        await self._reset_context(
            storage_state=str(saved.get("storage_path", "")) or None,
            headless=target_headless,
            profile=saved,
            user_id=user_id or self.current_user_id,
        )
        if target_headless:
            await self._close_mode("headed")
        return saved

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
        headless: bool | None = None,
    ) -> dict[str, Any]:
        await self.get_page(target_url=url, profile_name=profile_name, user_id=user_id, headless=headless)
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
        mode = self._find_tab_mode(tab_id)
        if mode is None:
            raise RuntimeError(f"Unknown browser tab '{tab_id}'.")
        self._activate_mode(mode, user_id=user_id)
        self.current_tab_id = tab_id
        await self._refresh_tab_state(tab_id, user_id or self.current_user_id)
        return self.tab_payload(tab_id)

    async def close_tab(self, tab_id: str, *, user_id: str | None = None) -> dict[str, Any]:
        mode = self._find_tab_mode(tab_id)
        if mode is None:
            raise RuntimeError(f"Unknown browser tab '{tab_id}'.")
        self._activate_mode(mode, user_id=user_id)
        await self._tabs[tab_id].page.close()
        self._drop_tab(tab_id)
        if self.current_tab_id not in self._tabs:
            self.current_tab_id = next(iter(self._tabs), None)
        await self._emit_state(user_id or self.current_user_id)
        return {"tab_id": tab_id, "closed": True, "current_tab_id": self.current_tab_id, "mode": mode}

    def list_tabs(self) -> list[dict[str, Any]]:
        self._snapshot_active_mode()
        deduped: dict[str, dict[str, Any]] = {}
        for mode in ("headless", "headed"):
            state = self._mode_states[mode]
            for tab_id in state.tabs:
                payload = self.tab_payload(tab_id, mode=mode)
                dedupe_key = self._normalized_tab_url(str(payload.get("url", ""))) or f"{mode}:{tab_id}"
                existing = deduped.get(dedupe_key)
                if existing is None:
                    deduped[dedupe_key] = payload
                    continue
                existing_active = bool(existing.get("active"))
                payload_active = bool(payload.get("active"))
                if payload_active and not existing_active:
                    deduped[dedupe_key] = payload
                    continue
                if not existing_active and str(payload.get("mode", "")) == "headed" and str(existing.get("mode", "")) != "headed":
                    deduped[dedupe_key] = payload
        tabs = list(deduped.values())
        tabs.sort(key=lambda item: (not bool(item.get("active")), str(item.get("created_at", ""))), reverse=False)
        return tabs

    def list_logs(self, limit: int = 50) -> list[dict[str, Any]]:
        items = [self._redact_log_entry(dict(item)) for item in self._recent_logs if self._is_meaningful_log(item)]
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
        self._snapshot_active_mode()
        active_state = self._active_state()
        active_tab = (
            self.tab_payload(active_state.current_tab_id, mode=self._active_mode)
            if active_state.current_tab_id and active_state.current_tab_id in active_state.tabs
            else None
        )
        profile = self._profile_for_active_tab(active_state, active_tab)
        return {
            "active": any(state.context is not None for state in self._mode_states.values()),
            "headless": self._active_mode == "headless",
            "current_mode": self._active_mode,
            "active_profile": profile,
            "current_tab_id": active_state.current_tab_id,
            "active_tab": active_tab,
            "tabs": self.list_tabs(),
            "recent_logs": self.list_logs(limit=8),
            "recent_downloads": self.list_downloads(limit=8),
            "streaming": bool(self._stream_task and not self._stream_task.done()),
            "modes": {
                mode: {
                    "active": state.context is not None,
                    "tab_count": len(state.tabs),
                    "current_tab_id": state.current_tab_id,
                }
                for mode, state in self._mode_states.items()
            },
            "pending_login": dict(self._pending_login) if self._pending_login else None,
            "pending_protected_action": dict(self._pending_protected_action) if self._pending_protected_action else None,
        }

    def _profile_for_active_tab(
        self,
        active_state: BrowserModeState,
        active_tab: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        url = str((active_tab or {}).get("url", "") or "")
        matched = self.match_profile(url) if url else None
        if matched is not None:
            return matched
        current_key = active_state.current_profile_key or self.current_profile_key
        if not current_key:
            return None
        record = self._load_index().get(current_key, None)
        if record is None:
            return None
        domain = str(record.get("domain", "")).lower()
        active_host = urlparse(url).netloc.lower() if url else ""
        if active_host and domain and not (
            active_host == domain
            or active_host.endswith(f".{domain}")
            or domain.endswith(f".{active_host}")
        ):
            return None
        return record

    def _is_meaningful_log(self, entry: dict[str, Any]) -> bool:
        kind = str(entry.get("kind", "")).lower()
        level = str(entry.get("level", "")).lower()
        message = str(entry.get("message", "")).strip()
        lowered = message.lower()
        if not message:
            return False
        if kind == "network" and any(token in lowered for token in ("google-analytics.com", "googletagmanager.com", "doubleclick.net")):
            return False
        if kind == "console":
            if lowered in {"undefined", "warningmsg", "jshandle@node", "home.htm"}:
                return False
            if lowered.startswith("jshandle@") or lowered.startswith("[object "):
                return False
            if len(message) < 4 and level not in {"error", "warning"}:
                return False
        return True

    def _redact_log_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        entry["message"] = redact_browser_text(str(entry.get("message", "")))
        if entry.get("url"):
            entry["url"] = redact_browser_url(str(entry.get("url", "")))
        return entry

    async def stream_screenshot_payload(self) -> dict[str, Any] | None:
        headed_state = self._state_for_mode("headed")
        if headed_state.current_tab_id is None or headed_state.current_tab_id not in headed_state.tabs:
            return None
        page = headed_state.tabs[headed_state.current_tab_id].page
        screenshot_bytes = await page.screenshot(type="jpeg", quality=65)
        return {
            "tab_id": headed_state.current_tab_id,
            "url": headed_state.tabs[headed_state.current_tab_id].url,
            "title": headed_state.tabs[headed_state.current_tab_id].title,
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

    async def press_key(self, page: Any, key: str, *, delay_ms: int = 0) -> None:
        await page.keyboard.press(key, delay=delay_ms)

    async def wait_for_url_match(self, page: Any, pattern: str, *, timeout_seconds: int = 10) -> bool:
        timeout_ms = max(1000, int(timeout_seconds * 1000))
        current_url = str(getattr(page, "url", "") or "")
        if re.search(pattern, current_url, flags=re.IGNORECASE):
            return True
        try:
            await page.wait_for_url(re.compile(pattern, re.IGNORECASE), timeout=timeout_ms)
            return True
        except Exception:
            return False

    async def _locator_supports_fill(self, locator: Any, *, timeout_ms: int) -> bool:
        try:
            await locator.wait_for(state="visible", timeout=timeout_ms)
            return bool(
                await locator.evaluate(
                    """(element) => {
                        const tag = (element.tagName || '').toLowerCase();
                        const isEditable =
                          tag === 'input' ||
                          tag === 'textarea' ||
                          element.isContentEditable ||
                          element.getAttribute('contenteditable') === '' ||
                          element.getAttribute('contenteditable') === 'true';
                        if (!isEditable) {
                          return false;
                        }
                        if (element.disabled || element.readOnly) {
                          return false;
                        }
                        return (element.getAttribute('aria-readonly') || '').toLowerCase() !== 'true';
                    }"""
                )
            )
        except Exception:
            return False

    async def _click_visible_if_present(self, locator: Any, *, timeout_ms: int) -> bool:
        try:
            await locator.wait_for(state="visible", timeout=timeout_ms)
            await locator.click(timeout=timeout_ms)
            return True
        except Exception:
            return False

    def _search_input_candidates(self, page: Any, *, site_name: str | None = None) -> list[tuple[str, Any]]:
        adapter = get_site_adapter(site_name)
        candidates: list[tuple[str, Any]] = []
        if adapter is not None:
            for selector in adapter.search_input_selectors:
                candidates.append((f"adapter:{selector}", page.locator(selector).first))
        candidates.extend(
            [
                ("css:input[type=search]", page.locator("input[type=search]").first),
                ("css:input[name=q]", page.locator("input[name='q']").first),
                ("css:textarea[name=q]", page.locator("textarea[name='q']").first),
                ("css:input[placeholder*=Search]", page.locator("input[placeholder*='Search' i]").first),
                ("css:input[aria-label*=Search]", page.locator("input[aria-label*='Search' i]").first),
                ("css:textarea[aria-label*=Search]", page.locator("textarea[aria-label*='Search' i]").first),
                ("role:searchbox", page.get_by_role("searchbox").first),
                ("role:textbox", page.get_by_role("textbox", name=re.compile("search", re.IGNORECASE)).first),
                ("placeholder:Search", page.get_by_placeholder(re.compile("search", re.IGNORECASE)).first),
                ("label:Search", page.get_by_label(re.compile("search", re.IGNORECASE)).first),
            ]
        )
        return candidates

    async def find_search_input(self, page: Any, *, site_name: str | None = None, timeout_seconds: int = 10):
        timeout_ms = max(1000, int(timeout_seconds * 1000))
        adapter = get_site_adapter(site_name)
        for strategy, locator in self._search_input_candidates(page, site_name=site_name):
            if await self._locator_supports_fill(locator, timeout_ms=timeout_ms):
                return locator, strategy
        if adapter is not None:
            for selector in adapter.search_expand_selectors:
                expanded = await self._click_visible_if_present(page.locator(selector).first, timeout_ms=timeout_ms)
                if not expanded:
                    continue
                await page.wait_for_timeout(200)
                for strategy, locator in self._search_input_candidates(page, site_name=site_name):
                    if await self._locator_supports_fill(locator, timeout_ms=timeout_ms):
                        return locator, strategy
        raise RuntimeError("I couldn't find an editable search input on the current page.")

    async def list_clickable_candidates(self, page: Any, *, limit: int = 20) -> list[dict[str, Any]]:
        items = await page.evaluate(
            """(maxItems) => {
                const nodes = Array.from(document.querySelectorAll('a, button, [role="button"]'));
                return nodes
                  .map((node, index) => {
                    const text = (node.innerText || node.textContent || '').replace(/\\s+/g, ' ').trim();
                    const href = node.href || '';
                    const aria = node.getAttribute('aria-label') || '';
                    return {
                      index,
                      text,
                      href,
                      tag: (node.tagName || '').toLowerCase(),
                      aria_label: aria,
                    };
                  })
                  .filter((item) => (item.text || item.aria_label) && item.text.length < 220)
                  .slice(0, maxItems);
            }""",
            max(1, min(limit, 50)),
        )
        return list(items or [])

    async def extract_search_results(
        self,
        page: Any,
        *,
        site_name: str | None = None,
        max_results: int = 8,
    ) -> list[dict[str, Any]]:
        adapter = get_site_adapter(site_name)
        if adapter is not None:
            if adapter.result_strategy == "youtube":
                results = await self._extract_youtube_results(page, max_results=max_results)
                if results:
                    return results
            if adapter.result_strategy == "google":
                results = await self._extract_google_results(page, max_results=max_results)
                if results:
                    return results
            if adapter.result_strategy == "leetcode":
                results = await self._extract_leetcode_results(page, max_results=max_results)
                if results:
                    return results
        raw_items = await page.evaluate(
            """(maxItems) => {
                const nodes = Array.from(document.querySelectorAll('a'));
                return nodes
                  .map((node, index) => {
                    const text = (node.innerText || node.textContent || '').replace(/\\s+/g, ' ').trim();
                    const href = node.href || '';
                    const aria = node.getAttribute('aria-label') || '';
                    return {
                      index,
                      title: text || aria,
                      href,
                      aria_label: aria,
                    };
                  })
                  .filter((item) => item.title && item.href && !item.href.startsWith('javascript:'))
                  .slice(0, maxItems * 8);
            }""",
            max(1, min(max_results, 20)),
        )
        filtered: list[dict[str, Any]] = []
        for item in raw_items or []:
            href = str(item.get("href", "")).strip()
            title = str(item.get("title", "")).strip()
            if not href or not title:
                continue
            lowered_href = href.lower()
            if site_name == "youtube" and "/watch" not in lowered_href:
                continue
            if site_name == "google" and any(token in lowered_href for token in ("google.com/search", "accounts.google.com")):
                continue
            filtered.append(
                {
                    "index": int(item.get("index", len(filtered))),
                    "title": title,
                    "href": href,
                    "snippet": "",
                    "ranking_hints": {
                        "compact_title": compact_text(title),
                        "youtube_watch": site_name == "youtube" and "/watch" in lowered_href,
                    },
                }
            )
            if len(filtered) >= max_results:
                break
        return filtered

    async def _extract_youtube_results(self, page: Any, *, max_results: int) -> list[dict[str, Any]]:
        raw_items = await page.evaluate(
            """(maxItems) => {
                const nodes = Array.from(document.querySelectorAll(
                  'a#video-title, ytd-video-renderer a#video-title, a#video-title-link, ytd-rich-item-renderer a#video-title-link, ytd-rich-grid-media a#video-title-link'
                ));
                return nodes.slice(0, maxItems * 4).map((node, index) => {
                    const title = (node.getAttribute('title') || node.textContent || '').replace(/\\s+/g, ' ').trim();
                    const href = node.href || '';
                    const renderer = node.closest('ytd-video-renderer, ytd-rich-item-renderer, ytd-rich-grid-media');
                    const metaNode = renderer ? renderer.querySelector('#metadata-line') : null;
                    const meta = (metaNode?.innerText || '').replace(/\\s+/g, ' ').trim();
                    return { index, title, href, snippet: meta };
                }).filter((item) => item.title && item.href && item.href.includes('/watch'));
            }""",
            max(1, min(max_results, 20)),
        )
        results: list[dict[str, Any]] = []
        for item in raw_items or []:
            title = str(item.get("title", "")).strip()
            href = str(item.get("href", "")).strip()
            if not title or not href:
                continue
            results.append(
                {
                    "index": int(item.get("index", len(results))),
                    "title": title,
                    "href": href,
                    "snippet": str(item.get("snippet", "")).strip(),
                    "ranking_hints": {
                        "compact_title": compact_text(title),
                        "youtube_watch": True,
                        "freshness": 1 if re.search(r"\\b(?:minute|hour|day|week|month|year)s?\\b", str(item.get("snippet", "")), flags=re.IGNORECASE) else 0,
                    },
                }
            )
            if len(results) >= max_results:
                break
        return results

    async def _extract_google_results(self, page: Any, *, max_results: int) -> list[dict[str, Any]]:
        raw_items = await page.evaluate(
            """(maxItems) => {
                const nodes = Array.from(document.querySelectorAll('a h3, .g a h3, [data-snc] a h3'));
                return nodes.slice(0, maxItems * 4).map((node, index) => {
                    const anchor = node.closest('a');
                    const title = (node.innerText || node.textContent || '').replace(/\\s+/g, ' ').trim();
                    const href = anchor?.href || '';
                    const container = anchor?.closest('.g, div[data-snc], div[data-hveid], div[lang]');
                    const snippetNode = container ? container.querySelector('span, div[data-sncf], div[style*="line-clamp"]') : null;
                    const snippet = (snippetNode?.innerText || '').replace(/\\s+/g, ' ').trim();
                    return { index, title, href, snippet };
                }).filter((item) => item.title && item.href);
            }""",
            max(1, min(max_results, 20)),
        )
        results: list[dict[str, Any]] = []
        for item in raw_items or []:
            title = str(item.get("title", "")).strip()
            href = str(item.get("href", "")).strip()
            if not title or not href:
                continue
            lowered_href = href.lower()
            if any(token in lowered_href for token in ("google.com/search", "accounts.google.com")):
                continue
            results.append(
                {
                    "index": int(item.get("index", len(results))),
                    "title": title,
                    "href": href,
                    "snippet": str(item.get("snippet", "")).strip(),
                    "ranking_hints": {
                        "compact_title": compact_text(title),
                        "compact_href": compact_text(href),
                    },
                }
            )
            if len(results) >= max_results:
                break
        return results

    async def _extract_leetcode_results(self, page: Any, *, max_results: int) -> list[dict[str, Any]]:
        raw_items = await page.evaluate(
            """(maxItems) => {
                const nodes = Array.from(document.querySelectorAll('a[href*="/problems/"]'));
                return nodes.slice(0, maxItems * 6).map((node, index) => {
                    const title = (node.innerText || node.textContent || '').replace(/\\s+/g, ' ').trim();
                    const href = node.href || '';
                    return { index, title, href };
                }).filter((item) => item.title && item.href);
            }""",
            max(1, min(max_results, 20)),
        )
        results: list[dict[str, Any]] = []
        for item in raw_items or []:
            title = str(item.get("title", "")).strip()
            href = str(item.get("href", "")).strip()
            if not title or not href:
                continue
            results.append(
                {
                    "index": int(item.get("index", len(results))),
                    "title": title,
                    "href": href,
                    "snippet": "",
                    "ranking_hints": {
                        "compact_title": compact_text(title),
                    },
                }
            )
            if len(results) >= max_results:
                break
        return results

    def find_best_candidate_by_text(self, query: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not candidates:
            return None
        compact_query = compact_text(query)
        query_tokens = {token for token in re.split(r"[^a-z0-9]+", query.lower()) if token}
        scored: list[tuple[float, dict[str, Any]]] = []
        for position, item in enumerate(candidates):
            title = str(item.get("title", ""))
            snippet = str(item.get("snippet", ""))
            href = str(item.get("href", ""))
            compact_title = compact_text(title)
            compact_snippet = compact_text(snippet)
            compact_href = compact_text(href)
            title_tokens = {token for token in re.split(r"[^a-z0-9]+", title.lower()) if token}
            overlap = len(query_tokens & title_tokens)
            score = float(overlap * 12)
            if compact_query and compact_title == compact_query:
                score += 120
            elif compact_query and compact_query in compact_title:
                score += 70
            elif compact_query and compact_query in compact_snippet:
                score += 32
            elif compact_query and compact_query in compact_href:
                score += 28
            if item.get("ranking_hints", {}).get("youtube_watch"):
                score += 10
            if item.get("ranking_hints", {}).get("freshness"):
                score += 6
            score += max(0, 15 - position)
            scored.append((score, item))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return scored[0][1] if scored else candidates[0]

    async def click_best_match(
        self,
        page: Any,
        query: str,
        candidates: list[dict[str, Any]],
        *,
        site_name: str | None = None,
        open_first_result: bool = False,
        timeout_seconds: int = 30,
    ) -> dict[str, Any]:
        if not candidates:
            raise RuntimeError("I couldn't find any clickable results on the page.")
        chosen = candidates[0] if open_first_result else (self.find_best_candidate_by_text(query, candidates) or candidates[0])
        href = str(chosen.get("href", "")).strip()
        if href:
            await page.goto(href, wait_until=self.wait_state_for_navigation("domcontentloaded"))
            await self.post_action_wait(page, "networkidle", timeout_seconds)
            return chosen
        locator, _strategy = await self.resolve_locator(page, str(chosen.get("title", "")), timeout_seconds=timeout_seconds)
        await locator.click(timeout=max(1000, timeout_seconds * 1000))
        await self.post_action_wait(page, "networkidle", timeout_seconds)
        return chosen

    async def detect_blocking_state(self, page: Any) -> dict[str, Any] | None:
        current_url = str(getattr(page, "url", "") or "")
        lowered_url = current_url.lower()
        html = await page.content()
        visible_text = extract_visible_text(html).lower()
        has_password_field = bool(
            re.search(r'type\s*=\s*["\']password["\']', html, flags=re.IGNORECASE)
            or re.search(r'autocomplete\s*=\s*["\'](?:current|new)-password["\']', html, flags=re.IGNORECASE)
        )
        if looks_like_login_url(current_url):
            return {
                "kind": "login",
                "message": "The browser is blocked by a login page.",
                "url": current_url,
            }
        if has_password_field and any(phrase in visible_text for phrase in ("sign in", "log in", "login", "email address", "password")):
            return {
                "kind": "login",
                "message": "The browser is blocked by a login page.",
                "url": current_url,
            }
        if any(token in lowered_url for token in ("/sorry/", "recaptcha", "/challenge", "/checkpoint")) or any(
            phrase in visible_text for phrase in ("captcha", "unusual traffic", "verify you are human")
        ):
            return {
                "kind": "captcha",
                "message": "The browser is blocked by a captcha or human-verification page.",
                "url": current_url,
            }
        if any(token in lowered_url for token in ("consent.", "consent.google", "beforeyoucontinue")) or any(
            phrase in visible_text for phrase in ("before you continue", "accept all", "reject all", "cookie settings")
        ):
            return {
                "kind": "consent",
                "message": "The browser is blocked by a consent page.",
                "url": current_url,
            }
        if any(token in lowered_url for token in ("security", "challenge", "checkpoint")) or any(
            phrase in visible_text for phrase in ("security check", "challenge", "suspicious activity")
        ):
            return {
                "kind": "security",
                "message": "The browser is blocked by a security-check page.",
                "url": current_url,
            }
        return None

    def safe_action_requires_confirmation(self, action_type: str, target: str | None = None) -> bool:
        lowered = action_type.strip().lower()
        if lowered in {"submit", "send", "purchase", "publish", "delete", "merge", "approve"}:
            return True
        if target and any(token in target.lower() for token in ("checkout", "buy", "confirm", "delete", "publish")):
            return True
        return False

    def pending_protected_action(self) -> dict[str, Any] | None:
        return dict(self._pending_protected_action) if self._pending_protected_action else None

    async def prepare_protected_action(
        self,
        action_type: str,
        *,
        selector: str | None = None,
        target: str | None = None,
        description: str | None = None,
        wait_for: str | None = None,
        timeout_seconds: int = 30,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        if not self.safe_action_requires_confirmation(action_type, target or selector):
            raise RuntimeError("This browser action does not require a protected review checkpoint.")
        current_url = ""
        if self.current_tab_id and self.current_tab_id in self._tabs:
            current_url = str(self._tabs[self.current_tab_id].url or getattr(self._tabs[self.current_tab_id].page, "url", "") or "")
        headed = await self.open_visible_intervention(current_url or target or "about:blank", user_id=user_id)
        self._pending_protected_action = {
            "action_type": action_type,
            "selector": selector or "",
            "target": target or current_url,
            "description": description or action_type,
            "tab_id": headed.get("tab_id"),
            "url": headed.get("url"),
            "wait_for": wait_for or "networkidle",
            "timeout_seconds": max(1, int(timeout_seconds)),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "awaiting_followup": "confirmation",
        }
        await self._emit_state(user_id or self.current_user_id)
        return dict(self._pending_protected_action)

    async def confirm_pending_action(self, *, user_id: str | None = None) -> dict[str, Any]:
        action = self._pending_protected_action
        if action is None:
            raise RuntimeError("There is no protected browser action waiting for confirmation.")
        self._activate_mode("headed", user_id=user_id)
        selector = str(action.get("selector", "")).strip()
        if selector and self.current_tab_id and self.current_tab_id in self._tabs:
            page = self._tabs[self.current_tab_id].page
            locator, _strategy = await self.resolve_locator(page, selector, timeout_seconds=10)
            await locator.click(timeout=10000)
            await self.post_action_wait(
                page,
                optional_string(action.get("wait_for")) or "networkidle",
                int(action.get("timeout_seconds", 30)),
            )
            await self.refresh_active_tab(user_id or self.current_user_id)
        result = dict(action)
        self._pending_protected_action = None
        if getattr(self.config.browser_execution, "revert_to_headless_after_manual_step", True):
            self._activate_mode("headless", user_id=user_id)
            await self._schedule_headed_idle_close()
        await self._emit_state(user_id or self.current_user_id)
        return result

    async def cancel_pending_action(self, *, user_id: str | None = None) -> dict[str, Any]:
        action = self._pending_protected_action
        if action is None:
            raise RuntimeError("There is no protected browser action waiting for cancellation.")
        self._pending_protected_action = None
        if getattr(self.config.browser_execution, "revert_to_headless_after_manual_step", True):
            self._activate_mode("headless", user_id=user_id)
            await self._schedule_headed_idle_close()
        await self._emit_state(user_id or self.current_user_id)
        return dict(action)

    async def refresh_active_tab(self, user_id: str | None = None) -> None:
        if self.current_tab_id is None:
            return
        await self._refresh_tab_state(self.current_tab_id, user_id or self.current_user_id)

    async def try_start_media_playback(self, page: Any) -> None:
        try:
            play_button = page.locator("button[aria-label*='Play' i]").first
            await play_button.click(timeout=1500)
            return
        except Exception:
            pass
        try:
            await self.press_key(page, "k")
        except Exception:
            return

    async def emit_workflow_event(self, user_id: str, event_name: str, payload: dict[str, Any]) -> None:
        await self._emit_browser_event(user_id, event_name, payload)

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
        await self._cancel_headed_idle_close()
        self._snapshot_active_mode()
        for state in self._mode_states.values():
            if state.context is not None:
                await state.context.close()
            if state.browser is not None:
                await state.browser.close()
            state.browser = None
            state.context = None
            state.current_profile_key = None
            state.current_tab_id = None
            state.current_user_id = None
            state.tabs.clear()
            state.page_tab_ids.clear()
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
        self._pending_protected_action = None

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
        desired_mode = self._mode_from_headless(headless)
        desired_headless = self._mode_headless(desired_mode)
        self._activate_mode(desired_mode, user_id=user_id)
        if desired_mode == "headed":
            await self._cancel_headed_idle_close()
        if self.browser is None:
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
        self._snapshot_active_mode()

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
        self._snapshot_active_mode()
        mode = self._find_tab_mode(tab_id)
        if mode is None:
            return
        state = self._mode_states[mode]
        tab_state = state.tabs.pop(tab_id, None)
        if tab_state is None:
            return
        state.page_tab_ids.pop(id(tab_state.page), None)
        if state.current_tab_id == tab_id:
            state.current_tab_id = next(iter(state.tabs), None)
        if self._active_mode == mode:
            self._tabs = state.tabs
            self._page_tab_ids = state.page_tab_ids
            self.current_tab_id = state.current_tab_id

    async def _close_matching_tabs_in_mode(
        self,
        mode: str,
        *,
        target_url: str | None = None,
        site_name: str | None = None,
        exclude_tab_id: str | None = None,
    ) -> None:
        if mode not in self._mode_states:
            return
        self._snapshot_active_mode()
        state = self._mode_states[mode]
        candidates = [
            tab_id
            for tab_id, tab in list(state.tabs.items())
            if tab_id != exclude_tab_id and self._tab_matches_target(tab, target_url=target_url, site_name=site_name)
        ]
        for tab_id in candidates:
            tab = state.tabs.get(tab_id)
            if tab is None:
                continue
            try:
                await tab.page.close()
            except Exception:
                self._drop_tab(tab_id)

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
        mode = self._find_tab_mode(tab_id) or self._active_mode
        state = self._state_for_mode(mode)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "level": level,
            "message": message,
            "tab_id": tab_id,
            "url": getattr(page, "url", ""),
            "profile_key": state.current_profile_key,
            "mode": mode,
        }
        self._recent_logs.append(entry)
        target_user = user_id or state.current_user_id or self.current_user_id
        if target_user:
            asyncio.create_task(self._emit_browser_event(target_user, "browser.log", entry))

    async def _handle_download(self, tab_id: str, page: Any, download: Any, user_id: str | None) -> None:
        mode = self._find_tab_mode(tab_id) or self._active_mode
        state = self._state_for_mode(mode)
        profile = self._load_index().get(state.current_profile_key or "", None)
        profile_dir = self.profile_download_dir(profile)
        profile_dir.mkdir(parents=True, exist_ok=True)
        target = dedupe_path(profile_dir / str(download.suggested_filename))
        await download.save_as(str(target))
        entry = {
            "path": str(target),
            "filename": target.name,
            "profile_key": state.current_profile_key,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "size": target.stat().st_size if target.exists() else 0,
            "tab_id": tab_id,
            "url": getattr(page, "url", ""),
            "mode": mode,
        }
        self._recent_downloads.append(entry)
        target_user = user_id or state.current_user_id or self.current_user_id
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

    async def _close_mode(self, mode: str) -> None:
        if mode == "headed":
            await self._stop_streaming()
        state = self._mode_states[mode]
        if state.context is not None:
            await state.context.close()
        if state.browser is not None:
            await state.browser.close()
        state.browser = None
        state.context = None
        state.current_profile_key = None
        state.current_tab_id = None
        state.tabs.clear()
        state.page_tab_ids.clear()
        if self._active_mode == mode:
            self.browser = None
            self.context = None
            self.current_profile_key = None
            self.current_tab_id = None
            self.current_headless = self._mode_headless(mode)

    async def _cancel_headed_idle_close(self) -> None:
        if self._headed_idle_close_task is None:
            return
        self._headed_idle_close_task.cancel()
        try:
            await self._headed_idle_close_task
        except asyncio.CancelledError:
            pass
        self._headed_idle_close_task = None

    async def _schedule_headed_idle_close(self) -> None:
        await self._cancel_headed_idle_close()
        keep_alive = max(0, int(getattr(self.config.browser_execution, "keep_headed_browser_alive_seconds", 60)))
        if keep_alive <= 0:
            await self._close_mode("headed")
            return

        async def _runner() -> None:
            try:
                await asyncio.sleep(keep_alive)
                if self._pending_login is None and self._pending_protected_action is None:
                    await self._close_mode("headed")
            except asyncio.CancelledError:
                raise

        self._headed_idle_close_task = asyncio.create_task(_runner())

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
            headed_state = self._state_for_mode("headed")
            if headed_state.context is None or headed_state.current_tab_id not in headed_state.tabs:
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

    def tab_payload(self, tab_id: str, *, mode: str | None = None) -> dict[str, Any]:
        state_mode = mode or self._active_mode
        state = self._state_for_mode(state_mode).tabs[tab_id]
        return {
            "tab_id": state.tab_id,
            "title": state.title,
            "url": state.url,
            "created_at": state.created_at,
            "active": state.tab_id == self._state_for_mode(state_mode).current_tab_id and state_mode == self._active_mode,
            "mode": state_mode,
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


def redact_browser_url(url: str) -> str:
    if not url:
        return url
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return re.sub(r";jsessionid=[^/?#]+", "", url, flags=re.IGNORECASE)
    path = re.sub(r";jsessionid=[^/?#]+", "", parsed.path or "", flags=re.IGNORECASE)
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def redact_browser_text(text: str) -> str:
    if not text:
        return text
    return re.sub(
        r"https?://[^\s]+",
        lambda match: redact_browser_url(match.group(0)),
        text,
    )


def optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def compact_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


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
