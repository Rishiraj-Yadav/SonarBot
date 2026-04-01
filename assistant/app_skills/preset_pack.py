"""Study/work preset skills built on existing app and browser primitives."""

from __future__ import annotations

from typing import Any


class PresetSkillPack:
    def __init__(self, config, tool_registry, *, resolve_target_path, browser_pack) -> None:
        self.config = config
        self.tool_registry = tool_registry
        self.resolve_target_path = resolve_target_path
        self.browser_pack = browser_pack

    def _ensure_enabled(self) -> None:
        if not bool(getattr(self.config.app_skills, "enabled", False)):
            raise RuntimeError("App skills are not enabled.")
        if not bool(getattr(self.config.app_skills, "presets_enabled", True)):
            raise RuntimeError("Preset skills are disabled.")

    def list_presets(self) -> list[dict[str, str]]:
        self._ensure_enabled()
        return [
            {"name": "study-mode", "description": "Open study apps, your semester folder when found, and study URLs."},
            {"name": "work-mode", "description": "Open work apps, your workspace folder when found, and work URLs."},
            {"name": "meeting-mode", "description": "Open meeting apps and meeting browser URLs."},
        ]

    async def run_preset(self, preset_name: str, *, user_id: str) -> dict[str, Any]:
        self._ensure_enabled()
        normalized = preset_name.strip().lower().replace("_", "-")
        if normalized not in {"study-mode", "work-mode", "meeting-mode"}:
            raise RuntimeError(f"Unknown preset '{preset_name}'.")
        preset_config = getattr(self.config.app_skills, "presets", None)
        if preset_config is None:
            raise RuntimeError("Preset configuration is unavailable.")

        if normalized == "study-mode":
            apps = list(getattr(preset_config, "study_apps", []))
            folder_hints = list(getattr(preset_config, "study_folder_hints", []))
            browser_workspace = "study"
        elif normalized == "work-mode":
            apps = list(getattr(preset_config, "work_apps", []))
            folder_hints = list(getattr(preset_config, "work_folder_hints", []))
            browser_workspace = "work"
        else:
            apps = list(getattr(preset_config, "meeting_apps", []))
            folder_hints = []
            browser_workspace = "meeting"

        actions: list[str] = []
        opened_folder = ""
        for hint in folder_hints:
            try:
                opened_folder = await self.resolve_target_path(hint, prefer="directory")
            except Exception:
                continue
            break

        if opened_folder:
            await self.tool_registry.dispatch("apps_open", {"target": "explorer", "args": [opened_folder]})
            actions.append(f"Opened {opened_folder} in Explorer")

        for app_name in apps:
            normalized_app = str(app_name).strip().lower()
            if not normalized_app or normalized_app == "explorer":
                continue
            if not self.tool_registry.has("apps_open"):
                break
            try:
                await self.tool_registry.dispatch("apps_open", {"target": normalized_app})
                actions.append(f"Opened {normalized_app}")
            except Exception:
                continue

        browser_result = None
        try:
            browser_result = await self.browser_pack.open_workspace(browser_workspace, user_id=user_id)
        except Exception:
            browser_result = None
        if browser_result is not None and browser_result.get("count", 0):
            actions.append(f"Opened {browser_result['count']} browser tab(s)")

        return {
            "preset": normalized,
            "actions": actions,
            "opened_folder": opened_folder,
            "browser": browser_result,
            "status": "completed",
        }
