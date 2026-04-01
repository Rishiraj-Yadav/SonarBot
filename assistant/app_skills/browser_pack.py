"""Separate browser skill pack built on stable browser tool contracts."""

from __future__ import annotations

from typing import Any


class BrowserSkillPack:
    def __init__(self, config, tool_registry) -> None:
        self.config = config
        self.tool_registry = tool_registry

    def _urls_for_workspace(self, workspace_name: str) -> list[str]:
        presets = getattr(self.config.app_skills, "presets", None)
        if presets is None:
            return []
        normalized = workspace_name.strip().lower().replace("_", "-")
        if normalized == "study":
            return list(getattr(presets, "study_browser_urls", []))
        if normalized == "work":
            return list(getattr(presets, "work_browser_urls", []))
        if normalized == "meeting":
            return list(getattr(presets, "meeting_browser_urls", []))
        return []

    async def open_workspace(self, workspace_name: str, *, user_id: str) -> dict[str, Any]:
        if not bool(getattr(self.config.app_skills, "browser_enabled", True)):
            raise RuntimeError("Browser skill pack is disabled.")
        urls = [str(url).strip() for url in self._urls_for_workspace(workspace_name) if str(url).strip()]
        if not urls:
            raise RuntimeError(f"No browser workspace URLs are configured for '{workspace_name}'.")
        if not self.tool_registry.has("browser_tab_open"):
            raise RuntimeError("Browser automation is not configured.")
        opened: list[dict[str, Any]] = []
        for url in urls:
            result = await self.tool_registry.dispatch(
                "browser_tab_open",
                {
                    "url": url,
                    "user_id": user_id,
                    "headless": not bool(getattr(self.config.app_skills, "browser_headed_for_workspaces", True)),
                },
            )
            opened.append(
                {
                    "url": str(result.get("url", url)),
                    "title": str(result.get("title", "")),
                    "tab_id": str(result.get("tab_id", "")),
                }
            )
        return {"workspace": workspace_name, "opened": opened, "count": len(opened)}
