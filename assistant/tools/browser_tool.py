"""Browser automation tools backed by Playwright."""

from __future__ import annotations

from typing import Any

from assistant.tools.browser_runtime import (
    BrowserEventEmitter,
    BrowserRuntime,
    BrowserViewerChecker,
    extract_table_from_html,
    looks_like_login_url,
    optional_string,
    wait_for_manual_login,
)
from assistant.tools.registry import ToolDefinition


def build_browser_tools(
    config,
    *,
    event_emitter: BrowserEventEmitter | None = None,
    viewer_checker: BrowserViewerChecker | None = None,
) -> tuple[list[ToolDefinition], BrowserRuntime]:
    runtime = BrowserRuntime(config=config, event_emitter=event_emitter, viewer_checker=viewer_checker)

    async def browser_navigate(payload: dict[str, Any]) -> dict[str, Any]:
        url = str(payload["url"])
        profile_name = optional_string(payload.get("profile_name"))
        user_id = optional_string(payload.get("user_id"))
        tab_id = optional_string(payload.get("tab_id"))
        timeout_seconds = int(payload.get("timeout_seconds", 30))
        wait_for = optional_string(payload.get("wait_for"))
        matched_profile = runtime.match_profile(url, profile_name=profile_name)
        page = await runtime.get_page(target_url=url, profile_name=profile_name, tab_id=tab_id, user_id=user_id)
        response = await page.goto(url, wait_until=runtime.wait_state_for_navigation(wait_for))
        await runtime.post_action_wait(page, wait_for, timeout_seconds)
        current_url = page.url
        if matched_profile is not None:
            site_name = str(matched_profile.get("site_name", "site"))
            current_profile_name = str(matched_profile.get("profile_name", "default"))
            status_code = getattr(response, "status", None)
            if looks_like_login_url(current_url) or status_code in {401, 403}:
                updated = runtime.mark_profile_status(
                    site_name,
                    current_profile_name,
                    status="stale",
                    last_error=f"Redirected to login or received {status_code} while opening {url}",
                )
                if user_id and updated is not None:
                    await runtime._emit_browser_event(user_id, "browser.session_expired", updated)
                    await runtime._emit_state(user_id)
                raise RuntimeError(
                    f"Saved browser session for {site_name}/{current_profile_name} is stale. "
                    "Run browser_login again with the same site and profile."
                )
            runtime.touch_session(site_name, current_profile_name)
        assert runtime.current_tab_id is not None
        await runtime._refresh_tab_state(runtime.current_tab_id, user_id)
        current_profile = runtime._load_index().get(runtime.current_profile_key or "", None)
        tab_state = runtime._tabs[runtime.current_tab_id]
        return {
            "url": current_url,
            "title": tab_state.title,
            "content": str(tab_state.dom_snapshot.get("text", ""))[:4000],
            "tab_id": runtime.current_tab_id,
            "profile_name": current_profile.get("profile_name") if current_profile else profile_name or "default",
            "session_status": current_profile.get("status") if current_profile else "anonymous",
            "dom_snapshot": tab_state.dom_snapshot,
        }

    async def browser_click(payload: dict[str, Any]) -> dict[str, Any]:
        selector = str(payload["selector"])
        timeout_seconds = int(payload.get("timeout_seconds", 10))
        wait_for = optional_string(payload.get("wait_for"))
        user_id = optional_string(payload.get("user_id"))
        page = await runtime.get_page(
            tab_id=optional_string(payload.get("tab_id")),
            profile_name=optional_string(payload.get("profile_name")),
            user_id=user_id,
        )
        locator, strategy = await runtime.resolve_locator(page, selector, timeout_seconds=timeout_seconds)
        try:
            await locator.click(timeout=max(1000, timeout_seconds * 1000))
        except Exception:
            await page.wait_for_timeout(150)
            locator, strategy = await runtime.resolve_locator(page, selector, timeout_seconds=timeout_seconds)
            await locator.click(timeout=max(1000, timeout_seconds * 1000))
        await runtime.post_action_wait(page, wait_for, timeout_seconds)
        assert runtime.current_tab_id is not None
        await runtime._refresh_tab_state(runtime.current_tab_id, user_id)
        state = runtime._tabs[runtime.current_tab_id]
        return {
            "clicked": selector,
            "selector_strategy": strategy,
            "url": state.url,
            "tab_id": runtime.current_tab_id,
            "dom_snapshot": state.dom_snapshot,
        }

    async def browser_type(payload: dict[str, Any]) -> dict[str, Any]:
        selector = str(payload["selector"])
        text = str(payload["text"])
        timeout_seconds = int(payload.get("timeout_seconds", 10))
        user_id = optional_string(payload.get("user_id"))
        page = await runtime.get_page(
            tab_id=optional_string(payload.get("tab_id")),
            profile_name=optional_string(payload.get("profile_name")),
            user_id=user_id,
        )
        locator, strategy = await runtime.resolve_locator(page, selector, timeout_seconds=timeout_seconds)
        try:
            await locator.fill(text, timeout=max(1000, timeout_seconds * 1000))
        except Exception:
            await page.wait_for_timeout(150)
            locator, strategy = await runtime.resolve_locator(page, selector, timeout_seconds=timeout_seconds)
            await locator.fill(text, timeout=max(1000, timeout_seconds * 1000))
        assert runtime.current_tab_id is not None
        await runtime._refresh_tab_state(runtime.current_tab_id, user_id)
        state = runtime._tabs[runtime.current_tab_id]
        return {
            "typed": selector,
            "length": len(text),
            "selector_strategy": strategy,
            "tab_id": runtime.current_tab_id,
            "dom_snapshot": state.dom_snapshot,
        }

    async def browser_screenshot(payload: dict[str, Any]) -> dict[str, Any]:
        user_id = optional_string(payload.get("user_id"))
        page = await runtime.get_page(
            tab_id=optional_string(payload.get("tab_id")),
            profile_name=optional_string(payload.get("profile_name")),
            user_id=user_id,
        )
        runtime.screenshots_dir.mkdir(parents=True, exist_ok=True)
        target = runtime.screenshots_dir / f"screenshot-{runtime.current_tab_id or 'browser'}.png"
        await page.screenshot(path=str(target), full_page=True)
        assert runtime.current_tab_id is not None
        await runtime._refresh_tab_state(runtime.current_tab_id, user_id)
        return {"path": str(target), "tab_id": runtime.current_tab_id, "url": page.url}

    async def browser_login(payload: dict[str, Any]) -> dict[str, Any]:
        site_name = str(payload["site_name"]).strip()
        profile_name = str(payload.get("profile_name", "default")).strip() or "default"
        login_url = str(payload.get("url") or f"https://{site_name}").strip()
        timeout_seconds = int(payload.get("timeout_seconds", 300))
        user_id = optional_string(payload.get("user_id"))
        page = await runtime.start_login(site_name, profile_name, login_url, user_id=user_id)
        await wait_for_manual_login(page, login_url, timeout_seconds)
        saved = await runtime.save_login_session(site_name, profile_name, login_url)
        await runtime._reset_context(
            storage_state=str(saved.get("storage_path", "")) or None,
            headless=config.tools.browser_headless,
            profile=saved,
            user_id=user_id,
        )
        return saved

    async def browser_sessions_list(_payload: dict[str, Any]) -> dict[str, Any]:
        return {"sessions": runtime.list_sessions()}

    async def browser_tabs_list(_payload: dict[str, Any]) -> dict[str, Any]:
        return {"tabs": runtime.list_tabs(), "current_tab_id": runtime.current_tab_id}

    async def browser_tab_open(payload: dict[str, Any]) -> dict[str, Any]:
        return await runtime.open_tab(
            url=optional_string(payload.get("url")),
            profile_name=optional_string(payload.get("profile_name")),
            user_id=optional_string(payload.get("user_id")),
            wait_for=optional_string(payload.get("wait_for")),
            timeout_seconds=int(payload.get("timeout_seconds", 30)),
        )

    async def browser_tab_switch(payload: dict[str, Any]) -> dict[str, Any]:
        return await runtime.switch_tab(str(payload["tab_id"]), user_id=optional_string(payload.get("user_id")))

    async def browser_tab_close(payload: dict[str, Any]) -> dict[str, Any]:
        return await runtime.close_tab(str(payload["tab_id"]), user_id=optional_string(payload.get("user_id")))

    async def browser_upload(payload: dict[str, Any]) -> dict[str, Any]:
        selector = str(payload["selector"])
        path = await runtime.ensure_workspace_file(str(payload["path"]))
        timeout_seconds = int(payload.get("timeout_seconds", 10))
        user_id = optional_string(payload.get("user_id"))
        page = await runtime.get_page(
            tab_id=optional_string(payload.get("tab_id")),
            profile_name=optional_string(payload.get("profile_name")),
            user_id=user_id,
        )
        locator, strategy = await runtime.resolve_locator(page, selector, timeout_seconds=timeout_seconds, state="attached")
        await locator.set_input_files(str(path), timeout=max(1000, timeout_seconds * 1000))
        return {"selector": selector, "selector_strategy": strategy, "path": str(path), "uploaded": True}

    async def browser_downloads_list(payload: dict[str, Any]) -> dict[str, Any]:
        return {"downloads": runtime.list_downloads(limit=int(payload.get("limit", 20)))}

    async def browser_logs(payload: dict[str, Any]) -> dict[str, Any]:
        return {"logs": runtime.list_logs(limit=int(payload.get("limit", 50)))}

    async def browser_extract_table(payload: dict[str, Any]) -> dict[str, Any]:
        selector = optional_string(payload.get("selector"))
        user_id = optional_string(payload.get("user_id"))
        page = await runtime.get_page(
            tab_id=optional_string(payload.get("tab_id")),
            profile_name=optional_string(payload.get("profile_name")),
            user_id=user_id,
        )
        if selector:
            locator, _ = await runtime.resolve_locator(page, selector, timeout_seconds=int(payload.get("timeout_seconds", 10)), state="attached")
            html = await locator.evaluate("el => el.outerHTML")
        else:
            html = await page.content()
        headers, rows = extract_table_from_html(html, max_rows=int(payload.get("max_rows", 25)))
        return {"headers": headers, "rows": rows, "row_count": len(rows)}

    async def browser_fill_form(payload: dict[str, Any]) -> dict[str, Any]:
        raw_fields = payload.get("fields")
        if not isinstance(raw_fields, dict) or not raw_fields:
            raise RuntimeError("browser_fill_form requires a non-empty 'fields' object.")
        timeout_seconds = int(payload.get("timeout_seconds", 10))
        user_id = optional_string(payload.get("user_id"))
        page = await runtime.get_page(
            tab_id=optional_string(payload.get("tab_id")),
            profile_name=optional_string(payload.get("profile_name")),
            user_id=user_id,
        )
        filled: list[dict[str, Any]] = []
        for selector, value in raw_fields.items():
            locator, strategy = await runtime.resolve_locator(page, str(selector), timeout_seconds=timeout_seconds)
            await locator.fill(str(value), timeout=max(1000, timeout_seconds * 1000))
            filled.append({"selector": str(selector), "strategy": strategy})
        return {"filled": filled, "count": len(filled), "tab_id": runtime.current_tab_id}

    tools = [
        ToolDefinition(
            name="browser_navigate",
            description="Open a URL in the shared browser and capture the visible page content.",
            parameters={"type": "object", "properties": {"url": {"type": "string"}, "profile_name": {"type": "string"}, "tab_id": {"type": "string"}, "timeout_seconds": {"type": "integer", "minimum": 1, "default": 30}, "wait_for": {"type": "string"}}, "required": ["url"]},
            handler=browser_navigate,
        ),
        ToolDefinition(
            name="browser_click",
            description="Click an element in the shared browser using a resilient locator strategy.",
            parameters={"type": "object", "properties": {"selector": {"type": "string"}, "profile_name": {"type": "string"}, "tab_id": {"type": "string"}, "timeout_seconds": {"type": "integer", "minimum": 1, "default": 10}, "wait_for": {"type": "string"}}, "required": ["selector"]},
            handler=browser_click,
        ),
        ToolDefinition(
            name="browser_type",
            description="Type text into an element in the shared browser using CSS, text, label, or role fallback locators.",
            parameters={"type": "object", "properties": {"selector": {"type": "string"}, "text": {"type": "string"}, "profile_name": {"type": "string"}, "tab_id": {"type": "string"}, "timeout_seconds": {"type": "integer", "minimum": 1, "default": 10}}, "required": ["selector", "text"]},
            handler=browser_type,
        ),
        ToolDefinition(
            name="browser_screenshot",
            description="Take a screenshot of the current browser page and save it into the workspace.",
            parameters={"type": "object", "properties": {"profile_name": {"type": "string"}, "tab_id": {"type": "string"}}},
            handler=browser_screenshot,
        ),
        ToolDefinition(
            name="browser_login",
            description="Open a visible browser window, let the user log in manually, then save the named session profile.",
            parameters={"type": "object", "properties": {"site_name": {"type": "string"}, "profile_name": {"type": "string", "default": "default"}, "url": {"type": "string"}, "timeout_seconds": {"type": "integer", "minimum": 30, "default": 300}}, "required": ["site_name"]},
            handler=browser_login,
        ),
        ToolDefinition(name="browser_sessions_list", description="List saved browser login profiles and the last time each was used.", parameters={"type": "object", "properties": {}}, handler=browser_sessions_list),
        ToolDefinition(name="browser_tabs_list", description="List the currently open browser tabs in the active browser context.", parameters={"type": "object", "properties": {}}, handler=browser_tabs_list),
        ToolDefinition(name="browser_tab_open", description="Open a new browser tab, optionally navigating to a URL.", parameters={"type": "object", "properties": {"url": {"type": "string"}, "profile_name": {"type": "string"}, "timeout_seconds": {"type": "integer", "minimum": 1, "default": 30}, "wait_for": {"type": "string"}}}, handler=browser_tab_open),
        ToolDefinition(name="browser_tab_switch", description="Switch the active browser tab by tab id.", parameters={"type": "object", "properties": {"tab_id": {"type": "string"}}, "required": ["tab_id"]}, handler=browser_tab_switch),
        ToolDefinition(name="browser_tab_close", description="Close a browser tab by tab id.", parameters={"type": "object", "properties": {"tab_id": {"type": "string"}}, "required": ["tab_id"]}, handler=browser_tab_close),
        ToolDefinition(name="browser_upload", description="Upload a file from the workspace into the current browser page.", parameters={"type": "object", "properties": {"selector": {"type": "string"}, "path": {"type": "string"}, "profile_name": {"type": "string"}, "tab_id": {"type": "string"}, "timeout_seconds": {"type": "integer", "minimum": 1, "default": 10}}, "required": ["selector", "path"]}, handler=browser_upload),
        ToolDefinition(name="browser_downloads_list", description="List recent browser downloads saved into the workspace inbox.", parameters={"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "default": 20}}}, handler=browser_downloads_list),
        ToolDefinition(name="browser_logs", description="List recent browser console and network log entries.", parameters={"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "default": 50}}}, handler=browser_logs),
        ToolDefinition(name="browser_extract_table", description="Extract tabular data from the current page or a selected table element.", parameters={"type": "object", "properties": {"selector": {"type": "string"}, "tab_id": {"type": "string"}, "profile_name": {"type": "string"}, "max_rows": {"type": "integer", "minimum": 1, "default": 25}, "timeout_seconds": {"type": "integer", "minimum": 1, "default": 10}}}, handler=browser_extract_table),
        ToolDefinition(name="browser_fill_form", description="Fill multiple form fields in the current page using resilient locator fallbacks.", parameters={"type": "object", "properties": {"fields": {"type": "object", "additionalProperties": {"type": "string"}}, "tab_id": {"type": "string"}, "profile_name": {"type": "string"}, "timeout_seconds": {"type": "integer", "minimum": 1, "default": 10}}, "required": ["fields"]}, handler=browser_fill_form),
    ]
    return tools, runtime
