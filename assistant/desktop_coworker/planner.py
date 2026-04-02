"""Bounded planner for verified desktop coworker tasks."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


class DesktopCoworkerPlanner:
    def __init__(self, config, tool_registry) -> None:
        self.config = config
        self.tool_registry = tool_registry

    def can_handle(self, request_text: str) -> bool:
        return self.plan(request_text) is not None

    async def plan_request(
        self,
        request_text: str,
        *,
        request_analysis: dict[str, Any] | None = None,
        desktop_context: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        builtin = self.plan(request_text)
        if builtin is not None:
            return builtin
        if not bool(getattr(self.config.desktop_coworker, "structured_planner_enabled", True)):
            return None
        return await self._plan_structured_with_llm(
            request_text,
            request_analysis=request_analysis or {},
            desktop_context=desktop_context or {},
        )

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
            self._plan_volume_control,
            self._plan_brightness_control,
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

    async def _plan_structured_with_llm(
        self,
        request_text: str,
        *,
        request_analysis: dict[str, Any],
        desktop_context: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not self.tool_registry.has("llm_task"):
            return None
        task_kind = str(request_analysis.get("task_kind", "")).strip().lower()
        if task_kind not in {"structured", "visual"}:
            return None
        normalized_request = str(request_analysis.get("normalized_request", "")).strip() or request_text.strip()
        if not normalized_request:
            return None
        max_steps = max(1, int(getattr(self.config.desktop_coworker, "max_steps_per_task", 6)))
        app_aliases = sorted(
            {
                str(alias).strip().lower()
                for alias in getattr(self.config.desktop_apps, "known_apps", {}).keys()
                if str(alias).strip()
            }
        )
        prompt = (
            "Convert the user's Windows desktop request into a bounded JSON plan.\n"
            "Use deterministic tools first. Only use visual_task when the task depends on what is visible on screen.\n"
            "Return JSON only.\n"
            f"Max steps: {max_steps}.\n"
            f"Known desktop app aliases: {', '.join(app_aliases) or 'none'}.\n"
            "Allowed step types:\n"
            "- apps_open payload={target:string,args?:string[]}\n"
            "- apps_focus payload={target:string}\n"
            "- system_open_settings payload={page:string}\n"
            "- system_bluetooth_set payload={mode:'on'|'off'|'toggle',fallback_visual?:bool,open_settings_on_fallback?:bool,open_settings_page?:'bluetooth'}\n"
            "- system_volume_set payload={percent:int}\n"
            "- system_brightness_set payload={percent:int}\n"
            "- task_manager_open payload={}\n"
            "- task_manager_summary payload={}\n"
            "- vscode_open_target payload={target:string,prefer?:'file'|'directory'|'either'}\n"
            "- preset_run payload={name:string}\n"
            "- visual_task payload={goal:string}\n"
            "Each step must include: type, title, payload, verification, retryable, risky.\n"
            "Verification kinds you may use: tool_status, active_window_contains, bluetooth_state, brightness_state, volume_state, visual_task.\n"
            "Examples:\n"
            "- 'please launch chrome and go to google.com' => apps_open chrome with args ['https://google.com']\n"
            "- 'open excel and then open the visible file' => apps_open excel, then visual_task\n"
            "- 'open settings and turn bluetooth off' => system_open_settings bluetooth, then system_bluetooth_set with fallback_visual=true\n"
            "- 'open explorer and go to the desktop folder' => apps_open explorer, then visual_task\n"
            f"Current desktop context (may be empty): {json.dumps(desktop_context, ensure_ascii=False)}\n"
            f"Request analysis: {json.dumps(request_analysis, ensure_ascii=False)}\n"
            f"User request: {normalized_request}\n\n"
            "Return JSON with this shape:\n"
            "{\n"
            '  "summary": "short summary",\n'
            '  "steps": [\n'
            "    {\n"
            '      "type": "apps_open",\n'
            '      "title": "Open Chrome",\n'
            '      "payload": {"target": "chrome", "args": ["https://google.com"]},\n'
            '      "verification": {"kind": "active_window_contains", "matches": ["chrome"]},\n'
            '      "retryable": true,\n'
            '      "risky": false\n'
            "    }\n"
            "  ]\n"
            "}"
        )
        result = await self.tool_registry.dispatch("llm_task", {"prompt": prompt, "model": "cheap"})
        raw_content = str(result.get("content", "")).strip()
        if not raw_content:
            return None
        plan_payload = self._parse_plan_payload(raw_content)
        if plan_payload is None:
            return None
        steps = self._sanitize_structured_steps(plan_payload.get("steps", []), max_steps=max_steps)
        if not steps:
            return None
        summary = str(plan_payload.get("summary", "")).strip() or normalized_request.rstrip(".")
        if summary:
            summary = summary[0].upper() + summary[1:]
        return {"summary": summary, "steps": steps}

    def _plan_task_manager_summary(self, original: str, lowered: str) -> dict[str, Any] | None:
        if "task manager" not in lowered:
            return None
        if not any(marker in lowered for marker in {"summarize", "summary", "system usage", "cpu", "memory", "disk"}):
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

    def _plan_volume_control(self, original: str, lowered: str) -> dict[str, Any] | None:
        match = re.match(r"^(?:set|change)\s+volume\s+to\s+(\d{1,3})%?$", lowered)
        if match is None:
            return None
        if not self.tool_registry.has("system_volume_set"):
            raise ValueError("Volume coworker tasks require the system skill pack to be enabled.")
        percent = max(0, min(100, int(match.group(1))))
        return {
            "summary": f"Set the system volume to {percent}%.",
            "steps": [
                {
                    "type": "system_volume_set",
                    "title": f"Set volume to {percent}%",
                    "payload": {"percent": percent},
                    "verification": {"kind": "volume_state", "percent": percent},
                    "risky": True,
                }
            ],
        }

    def _plan_brightness_control(self, original: str, lowered: str) -> dict[str, Any] | None:
        match = re.match(r"^(?:set|change|increase|raise)\s+brightness(?:\s+to)?\s+(\d{1,3})%?$", lowered)
        if match is None:
            return None
        if not self.tool_registry.has("system_brightness_set"):
            raise ValueError("Brightness coworker tasks require the system skill pack to be enabled.")
        percent = max(0, min(100, int(match.group(1))))
        return {
            "summary": f"Set the display brightness to {percent}%.",
            "steps": [
                {
                    "type": "system_brightness_set",
                    "title": f"Set brightness to {percent}%",
                    "payload": {"percent": percent},
                    "verification": {"kind": "brightness_state", "percent": percent},
                    "risky": True,
                }
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
        if not self.tool_registry.has("system_open_settings") and not self.tool_registry.has("system_bluetooth_set"):
            raise ValueError("Bluetooth coworker tasks require the Phase 5 system skill pack to be enabled.")
        target_label = "off" if target_state == "off" else "on"
        verb = "turn off" if target_state == "off" else "turn on"
        explicit_settings = "settings" in lowered
        steps: list[dict[str, Any]] = []
        if explicit_settings:
            steps.append(
                {
                    "type": "system_open_settings",
                    "title": "Open Bluetooth settings",
                    "payload": {"page": "bluetooth"},
                    "verification": {"kind": "tool_status"},
                }
            )
        if self.tool_registry.has("system_bluetooth_set"):
            steps.append(
                {
                    "type": "system_bluetooth_set",
                    "title": f"{verb.title()} Bluetooth",
                    "payload": {
                        "mode": target_state,
                        "fallback_visual": True,
                        "open_settings_on_fallback": not explicit_settings,
                        "open_settings_page": "bluetooth",
                        "goal": original,
                    },
                    "verification": {"kind": "bluetooth_state", "state": target_state},
                    "retryable": False,
                    "risky": True,
                }
            )
        else:
            if not self.tool_registry.has("desktop_mouse_click"):
                raise ValueError("Bluetooth toggle coworker tasks require desktop input to be enabled.")
            if not steps:
                steps.append(
                    {
                        "type": "system_open_settings",
                        "title": "Open Bluetooth settings",
                        "payload": {"page": "bluetooth"},
                        "verification": {"kind": "tool_status"},
                    }
                )
            steps.append(
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
                }
            )
        return {
            "summary": f"Open Bluetooth settings and {verb} Bluetooth.",
            "steps": steps,
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

    def _parse_plan_payload(self, raw_content: str) -> dict[str, Any] | None:
        candidate = raw_content.strip()
        fenced = re.search(r"\{[\s\S]*\}", candidate)
        if fenced is not None:
            candidate = fenced.group(0)
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _sanitize_structured_steps(self, raw_steps: Any, *, max_steps: int) -> list[dict[str, Any]]:
        if not isinstance(raw_steps, list):
            return []
        allowed_types = {
            "apps_open",
            "apps_focus",
            "system_open_settings",
            "system_bluetooth_set",
            "system_volume_set",
            "system_brightness_set",
            "task_manager_open",
            "task_manager_summary",
            "vscode_open_target",
            "preset_run",
            "visual_task",
        }
        steps: list[dict[str, Any]] = []
        for raw_step in raw_steps[:max_steps]:
            if not isinstance(raw_step, dict):
                continue
            step_type = str(raw_step.get("type", "")).strip().lower()
            if step_type not in allowed_types:
                continue
            payload = dict(raw_step.get("payload", {})) if isinstance(raw_step.get("payload"), dict) else {}
            verification = dict(raw_step.get("verification", {})) if isinstance(raw_step.get("verification"), dict) else self._default_verification(step_type, payload)
            step = {
                "type": step_type,
                "title": str(raw_step.get("title", step_type.replace("_", " ").title())).strip() or step_type.replace("_", " ").title(),
                "payload": payload,
                "verification": verification,
                "retryable": bool(raw_step.get("retryable", True)),
                "risky": bool(raw_step.get("risky", False)),
            }
            if step_type == "apps_open":
                target = str(payload.get("target", "")).strip().lower()
                if not target or not self.tool_registry.has("apps_open"):
                    continue
                step["payload"]["args"] = [str(item) for item in payload.get("args", [])] if isinstance(payload.get("args"), list) else []
            elif step_type == "apps_focus":
                if not str(payload.get("target", "")).strip() or not self.tool_registry.has("apps_focus"):
                    continue
            elif step_type == "system_open_settings":
                page = str(payload.get("page", "")).strip().lower()
                if not page or not self.tool_registry.has("system_open_settings"):
                    continue
                step["payload"]["page"] = page
            elif step_type == "system_bluetooth_set":
                mode = str(payload.get("mode", "")).strip().lower()
                if mode not in {"on", "off", "toggle"} or not self.tool_registry.has("system_bluetooth_set"):
                    continue
                step["payload"]["mode"] = mode
                step["payload"]["fallback_visual"] = bool(payload.get("fallback_visual", True))
                step["payload"]["open_settings_on_fallback"] = bool(payload.get("open_settings_on_fallback", True))
                step["payload"]["open_settings_page"] = "bluetooth"
                step["risky"] = True
            elif step_type == "system_volume_set":
                if not self.tool_registry.has("system_volume_set"):
                    continue
                step["payload"]["percent"] = max(0, min(100, int(payload.get("percent", 0) or 0)))
                step["risky"] = True
            elif step_type == "system_brightness_set":
                if not self.tool_registry.has("system_brightness_set"):
                    continue
                step["payload"]["percent"] = max(0, min(100, int(payload.get("percent", 0) or 0)))
                step["risky"] = True
            elif step_type == "task_manager_open":
                if not self.tool_registry.has("task_manager_open"):
                    continue
            elif step_type == "task_manager_summary":
                if not self.tool_registry.has("task_manager_summary"):
                    continue
            elif step_type == "vscode_open_target":
                if not self.tool_registry.has("vscode_open_target") or not str(payload.get("target", "")).strip():
                    continue
            elif step_type == "preset_run":
                if not self.tool_registry.has("preset_run") or not str(payload.get("name", "")).strip():
                    continue
            elif step_type == "visual_task":
                goal = str(payload.get("goal", "")).strip()
                if not goal:
                    continue
                step["retryable"] = False
            steps.append(step)
        return steps

    def _default_verification(self, step_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        if step_type == "apps_open":
            target = str(payload.get("target", "")).strip().lower()
            return {"kind": "active_window_contains", "matches": [target]}
        if step_type == "apps_focus":
            target = str(payload.get("target", "")).strip().lower()
            return {"kind": "active_window_contains", "matches": [target]}
        if step_type == "system_bluetooth_set":
            return {"kind": "bluetooth_state", "state": str(payload.get("mode", "")).strip().lower()}
        if step_type == "system_volume_set":
            return {"kind": "volume_state", "percent": int(payload.get("percent", 0) or 0)}
        if step_type == "system_brightness_set":
            return {"kind": "brightness_state", "percent": int(payload.get("percent", 0) or 0)}
        if step_type == "visual_task":
            return {"kind": "visual_task"}
        return {"kind": "tool_status"}
