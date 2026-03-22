"""Browser automation tools backed by Playwright."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from assistant.tools.registry import ToolDefinition

try:  # pragma: no cover - optional dependency
    from bs4 import BeautifulSoup
except Exception:  # pragma: no cover - optional dependency
    BeautifulSoup = None


@dataclass(slots=True)
class BrowserRuntime:
    config: Any
    playwright: Any | None = None
    browser: Any | None = None
    context: Any | None = None
    page: Any | None = None
    current_session_name: str | None = None
    current_headless: bool | None = None
    sessions_dir: Path = field(init=False)
    session_index_path: Path = field(init=False)

    def __post_init__(self) -> None:
        self.sessions_dir = self.config.agent.workspace_dir / "browser_sessions"
        self.session_index_path = self.sessions_dir / "index.json"

    async def get_page(self, target_url: str | None = None):
        if self.playwright is None:
            try:
                from playwright.async_api import async_playwright  # type: ignore
            except Exception as exc:  # pragma: no cover - optional dependency
                raise RuntimeError(
                    "Playwright is not installed. Run `uv sync --extra dev` and `playwright install chromium`."
                ) from exc
            self.playwright = await async_playwright().start()

        storage_state = None
        desired_session = None
        if target_url:
            matched = self.match_session(target_url)
            if matched is not None:
                desired_session = matched["site_name"]
                storage_state = matched["storage_path"]

        desired_headless = self.config.tools.browser_headless
        if (
            self.page is None
            or self.context is None
            or self.current_session_name != desired_session
            or self.current_headless != desired_headless
        ):
            await self._reset_context(storage_state=storage_state, headless=desired_headless)
            self.current_session_name = desired_session
            self.current_headless = desired_headless

        return self.page

    async def start_login(self, site_name: str, login_url: str):
        if self.playwright is None:
            await self.get_page()
        await self._reset_context(storage_state=None, headless=False)
        self.current_session_name = None
        self.current_headless = False
        assert self.page is not None
        await self.page.goto(login_url, wait_until="domcontentloaded")
        return self.page

    async def save_login_session(self, site_name: str, login_url: str) -> dict[str, Any]:
        if self.context is None or self.page is None:
            raise RuntimeError("No active browser context is available for login.")
        slug = _slugify(site_name)
        state_path = self.sessions_dir / f"{slug}.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        await self.context.storage_state(path=str(state_path))
        final_url = self.page.url
        domain = urlparse(final_url or login_url).netloc.lower()
        index = self._load_index()
        index[site_name] = {
            "site_name": site_name,
            "domain": domain,
            "storage_path": str(state_path),
            "login_url": login_url,
            "last_used_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save_index(index)
        await self._reset_context(headless=self.config.tools.browser_headless)
        self.current_session_name = None
        self.current_headless = self.config.tools.browser_headless
        return index[site_name]

    def list_sessions(self) -> list[dict[str, Any]]:
        sessions = list(self._load_index().values())
        sessions.sort(key=lambda item: item.get("last_used_at", ""), reverse=True)
        return sessions

    def match_session(self, url: str) -> dict[str, Any] | None:
        target_domain = urlparse(url).netloc.lower()
        if not target_domain:
            return None
        for session in self._load_index().values():
            session_domain = str(session.get("domain", "")).lower()
            if session_domain and (
                target_domain == session_domain
                or target_domain.endswith(f".{session_domain}")
                or session_domain.endswith(f".{target_domain}")
            ):
                return session
        return None

    def touch_session(self, site_name: str) -> None:
        index = self._load_index()
        if site_name not in index:
            return
        index[site_name]["last_used_at"] = datetime.now(timezone.utc).isoformat()
        self._save_index(index)

    def expire_session(self, site_name: str) -> None:
        index = self._load_index()
        session = index.pop(site_name, None)
        self._save_index(index)
        if not session:
            return
        storage_path = Path(str(session.get("storage_path", "")))
        storage_path.unlink(missing_ok=True)
        if self.current_session_name == site_name:
            self.current_session_name = None

    async def close(self) -> None:
        if self.context is not None:
            await self.context.close()
        if self.browser is not None:
            await self.browser.close()
        if self.playwright is not None:
            await self.playwright.stop()
        self.context = None
        self.browser = None
        self.playwright = None
        self.page = None
        self.current_session_name = None
        self.current_headless = None

    async def _reset_context(self, storage_state: str | None = None, headless: bool | None = None) -> None:
        if self.browser is None:
            assert self.playwright is not None
            self.browser = await self.playwright.chromium.launch(
                headless=self.config.tools.browser_headless if headless is None else headless
            )
        elif headless is not None and self.current_headless is not None and self.current_headless != headless:
            await self.browser.close()
            assert self.playwright is not None
            self.browser = await self.playwright.chromium.launch(headless=headless)

        if self.context is not None:
            await self.context.close()
        context_kwargs = {}
        if storage_state:
            context_kwargs["storage_state"] = storage_state
        self.context = await self.browser.new_context(**context_kwargs)
        self.page = await self.context.new_page()

    def _load_index(self) -> dict[str, dict[str, Any]]:
        if not self.session_index_path.exists():
            return {}
        try:
            payload = json.loads(self.session_index_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(payload, dict):
            return {}
        return {str(key): dict(value) for key, value in payload.items() if isinstance(value, dict)}

    def _save_index(self, index: dict[str, dict[str, Any]]) -> None:
        self.session_index_path.parent.mkdir(parents=True, exist_ok=True)
        self.session_index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")


def build_browser_tools(config) -> tuple[list[ToolDefinition], BrowserRuntime]:
    runtime = BrowserRuntime(config=config)
    screenshots_dir = config.agent.workspace_dir / "browser"

    async def browser_navigate(payload: dict[str, Any]) -> dict[str, Any]:
        url = str(payload["url"])
        matched_session = runtime.match_session(url)
        page = await runtime.get_page(url)
        await page.goto(url, wait_until="domcontentloaded")
        title = await page.title()
        html = await page.content()
        current_url = page.url
        if matched_session is not None:
            site_name = str(matched_session["site_name"])
            if _looks_like_login_url(current_url):
                runtime.expire_session(site_name)
                raise RuntimeError(
                    f"Saved browser session for {site_name} expired after redirecting to a login page. "
                    "Run browser_login again."
                )
            runtime.touch_session(site_name)
        snapshot = _extract_visible_text(html)
        return {"url": current_url, "title": title, "content": snapshot[:4000]}

    async def browser_click(payload: dict[str, Any]) -> dict[str, Any]:
        selector = str(payload["selector"])
        page = await runtime.get_page()
        await page.click(selector)
        return {"clicked": selector, "url": page.url}

    async def browser_type(payload: dict[str, Any]) -> dict[str, Any]:
        selector = str(payload["selector"])
        text = str(payload["text"])
        page = await runtime.get_page()
        await page.fill(selector, text)
        return {"typed": selector, "length": len(text)}

    async def browser_screenshot(_payload: dict[str, Any]) -> dict[str, Any]:
        page = await runtime.get_page()
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        target = screenshots_dir / f"screenshot-{stamp}.png"
        await page.screenshot(path=str(target), full_page=True)
        return {"path": str(target)}

    async def browser_login(payload: dict[str, Any]) -> dict[str, Any]:
        site_name = str(payload["site_name"]).strip()
        login_url = str(payload.get("url") or f"https://{site_name}").strip()
        timeout_seconds = int(payload.get("timeout_seconds", 300))
        page = await runtime.start_login(site_name, login_url)
        await _wait_for_manual_login(page, login_url, timeout_seconds)
        saved = await runtime.save_login_session(site_name, login_url)
        return {
            "site_name": saved["site_name"],
            "domain": saved["domain"],
            "storage_state": saved["storage_path"],
            "last_used_at": saved["last_used_at"],
        }

    async def browser_sessions_list(_payload: dict[str, Any]) -> dict[str, Any]:
        return {"sessions": runtime.list_sessions()}

    tools = [
        ToolDefinition(
            name="browser_navigate",
            description="Open a URL in the shared browser and capture the visible page content.",
            parameters={
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
            handler=browser_navigate,
        ),
        ToolDefinition(
            name="browser_click",
            description="Click an element in the shared browser using a CSS selector.",
            parameters={
                "type": "object",
                "properties": {"selector": {"type": "string"}},
                "required": ["selector"],
            },
            handler=browser_click,
        ),
        ToolDefinition(
            name="browser_type",
            description="Type text into an element in the shared browser using a CSS selector.",
            parameters={
                "type": "object",
                "properties": {
                    "selector": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["selector", "text"],
            },
            handler=browser_type,
        ),
        ToolDefinition(
            name="browser_screenshot",
            description="Take a screenshot of the current browser page and save it into the workspace.",
            parameters={"type": "object", "properties": {}},
            handler=browser_screenshot,
        ),
        ToolDefinition(
            name="browser_login",
            description="Open a visible browser window, let the user log in manually, then save the session state.",
            parameters={
                "type": "object",
                "properties": {
                    "site_name": {"type": "string"},
                    "url": {"type": "string"},
                    "timeout_seconds": {"type": "integer", "minimum": 30, "default": 300},
                },
                "required": ["site_name"],
            },
            handler=browser_login,
        ),
        ToolDefinition(
            name="browser_sessions_list",
            description="List saved browser login sessions and the last time each was used.",
            parameters={"type": "object", "properties": {}},
            handler=browser_sessions_list,
        ),
    ]
    return tools, runtime


def _extract_visible_text(html: str) -> str:
    if BeautifulSoup is not None:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text(" ", strip=True)
        return re.sub(r"\s+", " ", text).strip()
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _looks_like_login_url(url: str) -> bool:
    lowered = url.lower()
    return any(token in lowered for token in ("/login", "/signin", "/sign-in", "/auth", "account/login"))


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-") or "session"


async def _wait_for_manual_login(page: Any, login_url: str, timeout_seconds: int) -> None:
    deadline = datetime.now(timezone.utc).timestamp() + timeout_seconds
    starting_domain = urlparse(login_url).netloc.lower()
    while datetime.now(timezone.utc).timestamp() < deadline:
        await page.wait_for_timeout(2000)
        current_url = page.url or ""
        current_domain = urlparse(current_url).netloc.lower()
        if current_url and not _looks_like_login_url(current_url) and (
            current_domain == starting_domain
            or current_domain.endswith(f".{starting_domain}")
            or starting_domain.endswith(f".{current_domain}")
        ):
            return
    raise RuntimeError("Timed out waiting for manual browser login to complete.")
