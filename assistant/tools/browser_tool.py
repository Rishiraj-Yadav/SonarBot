"""Browser automation tools backed by Playwright."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
    page: Any | None = None

    async def get_page(self):
        if self.page is not None:
            return self.page
        try:
            from playwright.async_api import async_playwright  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "Playwright is not installed. Run `uv sync --extra dev` and `playwright install chromium`."
            ) from exc

        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=self.config.tools.browser_headless)
        context = await self.browser.new_context()
        self.page = await context.new_page()
        return self.page

    async def close(self) -> None:
        if self.browser is not None:
            await self.browser.close()
        if self.playwright is not None:
            await self.playwright.stop()
        self.browser = None
        self.playwright = None
        self.page = None


def build_browser_tools(config) -> tuple[list[ToolDefinition], BrowserRuntime]:
    runtime = BrowserRuntime(config=config)
    screenshots_dir = config.agent.workspace_dir / "browser"

    async def browser_navigate(payload: dict[str, Any]) -> dict[str, Any]:
        url = str(payload["url"])
        page = await runtime.get_page()
        await page.goto(url, wait_until="domcontentloaded")
        title = await page.title()
        html = await page.content()
        snapshot = _extract_visible_text(html)
        return {"url": page.url, "title": title, "content": snapshot[:4000]}

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
