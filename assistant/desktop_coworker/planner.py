"""Bounded planner for verified desktop coworker tasks."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


class DesktopCoworkerPlanner:
    def __init__(self, config, tool_registry) -> None:
        self.config = config
        self.tool_registry = tool_registry

    def can_handle(self, request_text: str) -> bool:
        return self.plan(request_text) is not None

    def plan(self, request_text: str) -> dict[str, Any] | None:
        original = request_text.strip()
        if not original:
            return None
        normalized = re.sub(r"\s+", " ", original).strip()
        lowered = normalized.lower()
        lowered = re.sub(r"^(?:please |can you |could you |would you )", "", lowered)
        stripped_original = re.sub(r"^(?:please |can you |could you |would you )", "", normalized, flags=re.IGNORECASE)
        if lowered.startswith("help me "):
            lowered = lowered.removeprefix("help me ").strip()
            stripped_original = re.sub(r"^help me\s+", "", stripped_original, flags=re.IGNORECASE).strip()

        for builder in (
            self._plan_copy_and_summarize,
            self._plan_task_manager_summary,
            self._plan_bluetooth_toggle,
            self._plan_bluetooth_check,
            self._plan_preset_run,
            self._plan_vscode_open,
            self._plan_document_replace,
        ):
            plan = builder(stripped_original, lowered)
            if plan is not None:
                steps = list(plan.get("steps", []))
                max_steps = max(1, int(getattr(self.config.desktop_coworker, "max_steps_per_task", 6)))
                if len(steps) > max_steps:
                    raise ValueError(
                        f"This coworker task would need {len(steps)} steps, which exceeds desktop_coworker.max_steps_per_task={max_steps}."
                    )
                return plan
        return None

    def _plan_task_manager_summary(self, original: str, lowered: str) -> dict[str, Any] | None:
        if "task manager" not in lowered:
            return None
        if not self.tool_registry.has("task_manager_open") or not self.tool_registry.has("task_manager_summary"):
            raise ValueError("Task Manager coworker tasks require the Phase 5 task manager skill pack to be enabled.")
        return {
            "summary": "Open Task Manager and summarize system usage.",
            "steps": [
                {
                    "type": "task_manager_open",
                    "title": "Open Task Manager",
                    "verification": {"kind": "tool_status"},
                },
                {
                    "type": "task_manager_summary",
                    "title": "Summarize CPU, memory, and disk usage",
                    "verification": {"kind": "summary_has_keys", "keys": ["cpu_percent", "memory", "disk"]},
                },
            ],
        }

    def _plan_bluetooth_check(self, original: str, lowered: str) -> dict[str, Any] | None:
        if "bluetooth" not in lowered:
            return None
        if self._bluetooth_toggle_target(lowered) is not None:
            return None
        if "settings" not in lowered and "whether bluetooth" not in lowered and "check bluetooth" not in lowered:
            return None
        if not self.tool_registry.has("system_open_settings") or not self.tool_registry.has("system_bluetooth_status"):
            raise ValueError("Bluetooth coworker tasks require the Phase 5 system skill pack to be enabled.")
        return {
            "summary": "Open Bluetooth settings and report Bluetooth availability.",
            "steps": [
                {
                    "type": "system_open_settings",
                    "title": "Open Bluetooth settings",
                    "payload": {"page": "bluetooth"},
                    "verification": {"kind": "tool_status"},
                },
                {
                    "type": "system_bluetooth_status",
                    "title": "Check Bluetooth availability",
                    "verification": {"kind": "summary_has_keys", "keys": ["available", "service_status"]},
                },
            ],
        }

    def _plan_bluetooth_toggle(self, original: str, lowered: str) -> dict[str, Any] | None:
        target_state = self._bluetooth_toggle_target(lowered)
        if target_state is None:
            return None
        if not self.tool_registry.has("system_open_settings"):
            raise ValueError("Bluetooth coworker tasks require the Phase 5 system skill pack to be enabled.")
        if not self.tool_registry.has("desktop_read_screen") or not self.tool_registry.has("desktop_mouse_click"):
            raise ValueError("Bluetooth toggle coworker tasks require desktop vision and desktop input to be enabled.")
        target_label = "off" if target_state == "off" else "on"
        verb = "turn off" if target_state == "off" else "turn on"
        return {
            "summary": f"Open Bluetooth settings and {verb} Bluetooth.",
            "steps": [
                {
                    "type": "system_open_settings",
                    "title": "Open Bluetooth settings",
                    "payload": {"page": "bluetooth"},
                    "verification": {"kind": "tool_status"},
                },
                {
                    "type": "visual_task",
                    "title": f"{verb.title()} the visible Bluetooth toggle",
                    "payload": {
                        "goal": (
                            f"In Windows Bluetooth settings, {verb} Bluetooth. "
                            f"Only complete once the visible Bluetooth toggle clearly shows {target_label.title()}."
                        )
                    },
                    "verification": {"kind": "visual_task"},
                    "retryable": False,
                    "risky": True,
                },
            ],
        }

    def _bluetooth_toggle_target(self, lowered: str) -> str | None:
        if "bluetooth" not in lowered:
            return None
        off_patterns = (
            r"\bturn\s+off\s+(?:the\s+)?bluetooth\b",
            r"\bturn\s+(?:the\s+)?bluetooth\s+off\b",
            r"\bswitch\s+off\s+(?:the\s+)?bluetooth\b",
            r"\bswitch\s+(?:the\s+)?bluetooth\s+off\b",
            r"\bdisable\s+(?:the\s+)?bluetooth\b",
        )
        on_patterns = (
            r"\bturn\s+on\s+(?:the\s+)?bluetooth\b",
            r"\bturn\s+(?:the\s+)?bluetooth\s+on\b",
            r"\bswitch\s+on\s+(?:the\s+)?bluetooth\b",
            r"\bswitch\s+(?:the\s+)?bluetooth\s+on\b",
            r"\benable\s+(?:the\s+)?bluetooth\b",
        )
        if any(re.search(pattern, lowered) for pattern in off_patterns):
            return "off"
        if any(re.search(pattern, lowered) for pattern in on_patterns):
            return "on"
        return None

    def _plan_vscode_open(self, original: str, lowered: str) -> dict[str, Any] | None:
        match = re.match(
            r"^open\s+(.+?)\s+in\s+v(?:s(?:ual)?)?\s*code(?:\s+and\s+confirm.*)?$",
            original,
            flags=re.IGNORECASE,
        )
        if match is None:
            return None
        if not self.tool_registry.has("vscode_open_target"):
            raise ValueError("VS Code coworker tasks require the VS Code skill pack to be enabled.")
        target = str(match.group(1)).strip().strip("\"'")
        prefer = "file" if Path(target).suffix else "directory"
        return {
            "summary": f"Open {target} in VS Code and verify focus.",
            "steps": [
                {
                    "type": "vscode_open_target",
                    "title": f"Open {target} in VS Code",
                    "payload": {"target": target, "prefer": prefer},
                    "verification": {
                        "kind": "active_window_contains",
                        "matches": ["visual studio code", "code"],
                    },
                }
            ],
        }

    def _plan_document_replace(self, original: str, lowered: str) -> dict[str, Any] | None:
        match = re.match(
            r"^open\s+(.+?)(?:,|\s+and)\s*replace\s+(.+?)\s+with\s+(.+?)(?:,|\s+and)\s*(?:save it\s*(?:and\s*)?)?(?:verify|confirm).*$",
            original,
            flags=re.IGNORECASE,
        )
        if match is None:
            return None
        if not self.tool_registry.has("document_read") or not self.tool_registry.has("document_replace_text"):
            raise ValueError("Document coworker tasks require the document skill pack to be enabled.")
        path_hint = str(match.group(1)).strip().strip("\"'")
        find_text = str(match.group(2)).strip().strip("\"'")
        replace_text = str(match.group(3)).strip().strip("\"'")
        verification: dict[str, Any] = {
            "kind": "document_contains",
            "contains": replace_text,
        }
        if find_text and find_text not in replace_text:
            verification["not_contains"] = find_text
        return {
            "summary": f"Update {path_hint} and verify the text change.",
            "steps": [
                {
                    "type": "document_read",
                    "title": "Read the current document",
                    "payload": {"path": path_hint},
                    "verification": {"kind": "tool_status"},
                },
                {
                    "type": "document_replace_text",
                    "title": "Replace the requested text",
                    "payload": {"path": path_hint, "find_text": find_text, "replace_text": replace_text},
                    "verification": {"kind": "tool_status"},
                    "risky": True,
                },
                {
                    "type": "document_read",
                    "title": "Verify the updated document",
                    "payload": {"path": path_hint},
                    "verification": verification,
                },
            ],
        }

    def _plan_preset_run(self, original: str, lowered: str) -> dict[str, Any] | None:
        match = re.match(r"^(?:prepare|start|run)\s+(study mode|work mode|meeting mode)(?:\s+and\s+confirm.*)?$", lowered)
        if match is None:
            return None
        if not self.tool_registry.has("preset_run"):
            raise ValueError("Preset coworker tasks require the preset skill pack to be enabled.")
        preset_name = str(match.group(1)).replace(" ", "-")
        readable_name = str(match.group(1))
        return {
            "summary": f"Run {readable_name} and confirm the setup steps completed.",
            "steps": [
                {
                    "type": "preset_run",
                    "title": f"Run {readable_name}",
                    "payload": {"name": preset_name},
                    "verification": {"kind": "tool_status"},
                }
            ],
        }

    def _plan_copy_and_summarize(self, _original: str, lowered: str) -> dict[str, Any] | None:
        if lowered not in {"copy selected text and summarize it", "copy the selected text and summarize it"}:
            return None
        if not self.tool_registry.has("desktop_keyboard_hotkey") or not self.tool_registry.has("desktop_clipboard_read"):
            raise ValueError("Copy-and-summarize coworker tasks require desktop input to be enabled.")
        if not self.tool_registry.has("llm_task"):
            raise ValueError("Copy-and-summarize coworker tasks require llm_task to be available.")
        return {
            "summary": "Copy the selected text and summarize it.",
            "steps": [
                {
                    "type": "desktop_keyboard_hotkey",
                    "title": "Copy the selected text",
                    "payload": {"hotkey": "ctrl+c"},
                    "verification": {"kind": "tool_status"},
                },
                {
                    "type": "desktop_clipboard_read",
                    "title": "Read the clipboard",
                    "verification": {"kind": "clipboard_nonempty"},
                },
                {
                    "type": "llm_summarize_text",
                    "title": "Summarize the copied text",
                    "payload": {"instruction": "Summarize the copied text in 3 concise bullet points."},
                    "verification": {"kind": "tool_status"},
                },
            ],
        }
