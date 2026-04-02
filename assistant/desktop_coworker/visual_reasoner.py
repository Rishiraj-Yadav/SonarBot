"""Screenshot-aware LLM reasoner for visual coworker tasks."""

from __future__ import annotations

import asyncio
import base64
import json
import re
from pathlib import Path
from typing import Any

import httpx

from assistant.desktop_coworker.targeting import clamp_confidence, clamp_normalized_coordinate, sanitize_candidates


class DesktopCoworkerVisualReasoner:
    def __init__(self, config) -> None:
        self.config = config
        self._analysis_cache: dict[str, dict[str, Any]] = {}

    async def can_handle(self, request_text: str) -> bool:
        analysis = await self.analyze_request(request_text)
        return bool(analysis.get("desktop_ui_task", False))

    async def analyze_request(self, request_text: str) -> dict[str, Any]:
        return await self._analyze_request(request_text)

    async def _analyze_request(self, request_text: str) -> dict[str, Any]:
        lowered = re.sub(r"\s+", " ", request_text.strip().lower())
        if not lowered:
            return {
                "desktop_ui_task": False,
                "task_kind": "non_desktop",
                "summary": "",
                "normalized_request": "",
                "requires_visual_context": False,
            }
        if lowered in self._analysis_cache:
            return dict(self._analysis_cache[lowered])
        if not self.config.llm.gemini_api_key:
            result = self._regex_analysis(lowered, original=request_text.strip())
            self._remember_analysis(lowered, result)
            return result
        try:
            result = await self._analyze_request_with_llm(lowered)
        except Exception:
            result = self._regex_analysis(lowered, original=request_text.strip())
        self._remember_analysis(lowered, result)
        return result

    def _regex_analysis(self, lowered: str, *, original: str) -> dict[str, Any]:
        if self._regex_can_handle(lowered):
            cleaned = original.strip()
            summary = cleaned.rstrip(".")
            if summary:
                summary = summary[0].upper() + summary[1:]
            return {
                "desktop_ui_task": True,
                "task_kind": "visual",
                "summary": summary,
                "normalized_request": cleaned,
                "requires_visual_context": True,
            }
        return {
            "desktop_ui_task": False,
            "task_kind": "non_desktop",
            "summary": "",
            "normalized_request": original.strip(),
            "requires_visual_context": False,
        }

    def _regex_can_handle(self, lowered: str) -> bool:
        if not lowered:
            return False
        bluetooth_toggle_patterns = (
            r"\bturn\s+off\s+(?:the\s+)?bluetooth\b",
            r"\bturn\s+(?:the\s+)?bluetooth\s+off\b",
            r"\bswitch\s+off\s+(?:the\s+)?bluetooth\b",
            r"\bswitch\s+(?:the\s+)?bluetooth\s+off\b",
            r"\bdisable\s+(?:the\s+)?bluetooth\b",
            r"\bturn\s+on\s+(?:the\s+)?bluetooth\b",
            r"\bturn\s+(?:the\s+)?bluetooth\s+on\b",
            r"\bswitch\s+on\s+(?:the\s+)?bluetooth\b",
            r"\bswitch\s+(?:the\s+)?bluetooth\s+on\b",
            r"\benable\s+(?:the\s+)?bluetooth\b",
            r"\bturn\s+(?:the\s+)?visible\s+bluetooth\s+toggle\s+(?:off|on)\b",
        )
        if any(re.search(pattern, lowered) for pattern in bluetooth_toggle_patterns):
            return True
        if re.match(r"^(?:click(?:\s+on)?|select|double click|double-click)\s+(?!at\b)(?:the\s+)?[a-z0-9][\w\s._()&-]*$", lowered):
            return True
        if re.search(r"\b(?:open|click|select|double click|double-click)\b.+\bvisible\b", lowered):
            return True
        visual_markers = (
            "see on screen",
            "see on the screen",
            "visible on screen",
            "visible on the screen",
            "on screen now",
            "on the screen now",
            "you are seeing on the screen",
            "shown on screen",
            "highlighted",
            "visible file",
            "visible item",
            "visible button",
            "visible tab",
        )
        visual_verbs = ("open ", "click ", "select ", "double click", "double-click")
        return any(marker in lowered for marker in visual_markers) and any(verb in lowered for verb in visual_verbs)

    async def build_plan(self, request_text: str) -> dict[str, Any] | None:
        analysis = await self._analyze_request(request_text)
        if not bool(analysis.get("desktop_ui_task", False)):
            return None
        normalized_request = str(analysis.get("normalized_request", "")).strip() or request_text.strip()
        summary = str(analysis.get("summary", "")).strip()
        if not summary:
            summary = normalized_request.rstrip(".")
        if summary:
            summary = summary[0].upper() + summary[1:]
        else:
            summary = "Run the visual coworker task."
        return {
            "summary": summary,
            "steps": [
                {
                    "type": "visual_task",
                    "title": "Inspect the active window, choose the visible target, and verify the action.",
                    "payload": {"goal": normalized_request},
                    "verification": {"kind": "visual_task"},
                    "retryable": False,
                }
            ],
        }

    async def _analyze_request_with_llm(self, request_text: str) -> dict[str, Any]:
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": (
                                "Analyze the user's request for a Windows desktop coworker.\n"
                                "Return JSON only.\n"
                                "Desktop UI tasks include opening apps, clicking visible UI elements, interacting with dialogs, "
                                "typing into focused fields, scrolling visible windows, and screen-aware actions.\n"
                                "Requests such as 'turn off bluetooth', 'set volume to 40', and 'increase brightness to 100' are desktop UI tasks and are usually structured.\n"
                                "Do not classify pure file-read/write tasks, browser-only web automation, or general questions as desktop UI tasks.\n"
                                "Use task_kind=\"visual\" for tasks that should go through the screenshot-aware coworker loop.\n"
                                "Use task_kind=\"structured\" for desktop UI tasks that are better handled by a deterministic desktop coworker task.\n"
                                "Use task_kind=\"non_desktop\" otherwise.\n\n"
                                f"Request: {request_text}\n\n"
                                "Return JSON with this shape:\n"
                                "{\n"
                                '  "desktop_ui_task": true,\n'
                                '  "task_kind": "visual|structured|non_desktop",\n'
                                '  "summary": "short action-oriented summary",\n'
                                '  "normalized_request": "cleaned request to execute",\n'
                                '  "requires_visual_context": false\n'
                                "}"
                            )
                        }
                    ],
                }
            ]
        }
        raw_text = await self._request_text_completion(payload=payload, model_names=self._classification_models())
        parsed = self._parse_json_payload(raw_text)
        normalized_request = str(parsed.get("normalized_request", "")).strip() or request_text
        task_kind = str(parsed.get("task_kind", "")).strip().lower()
        if task_kind not in {"visual", "structured", "non_desktop"}:
            task_kind = "visual" if bool(parsed.get("desktop_ui_task", False)) else "non_desktop"
        summary = str(parsed.get("summary", "")).strip()
        return {
            "desktop_ui_task": bool(parsed.get("desktop_ui_task", False)),
            "task_kind": task_kind,
            "summary": summary,
            "normalized_request": normalized_request,
            "requires_visual_context": bool(parsed.get("requires_visual_context", task_kind == "visual")),
        }

    async def decide(
        self,
        *,
        goal: str,
        state: dict[str, Any],
        latest_state: dict[str, Any] | None,
        failed_targets: list[str],
        fused_candidates: list[dict[str, Any]] | None = None,
        backend_health: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        capture_path = str(state.get("capture_path", "")).strip()
        if not capture_path:
            return {
                "completion_state": "failed",
                "message": "There is no active screenshot available for the visual coworker step.",
                "candidates": [],
                "action": {},
            }
        image_path = self._resolve_capture_path(capture_path)
        if not image_path.exists():
            return {
                "completion_state": "failed",
                "message": f"The coworker screenshot '{image_path}' is missing.",
                "candidates": [],
                "action": {},
            }
        raw_text = await self._request_decision(
            image_path=image_path,
            prompt=self._build_prompt(
                goal=goal,
                state=state,
                latest_state=latest_state or {},
                failed_targets=failed_targets,
                fused_candidates=fused_candidates or [],
                backend_health=backend_health or {},
            ),
        )
        parsed = self._parse_json_payload(raw_text)
        max_candidates = max(1, int(getattr(self.config.desktop_coworker, "max_target_candidates", 8)))
        model_candidates = sanitize_candidates(parsed.get("candidates", []), limit=max_candidates)
        candidates = list(fused_candidates or [])
        if model_candidates:
            existing_labels = {str(item.get("normalized_label", "")) for item in candidates}
            for candidate in model_candidates:
                if str(candidate.get("normalized_label", "")) in existing_labels:
                    continue
                candidates.append(candidate)
                if len(candidates) >= max_candidates:
                    break
        action = self._normalize_action(dict(parsed.get("action", {})), candidates=candidates)
        completion_state = str(parsed.get("completion_state", "continue")).strip().lower() or "continue"
        if completion_state not in {"continue", "completed", "ask_user", "failed"}:
            completion_state = "continue"
        return {
            "screen_summary": str(parsed.get("screen_summary", "")).strip(),
            "screen_text": str(parsed.get("screen_text", "")).strip(),
            "completion_state": completion_state,
            "message": str(parsed.get("message", "")).strip(),
            "goal_completed_if_verified": bool(parsed.get("goal_completed_if_verified", True)),
            "candidates": candidates,
            "action": action,
        }

    def _normalize_action(self, action: dict[str, Any], *, candidates: list[dict[str, Any]]) -> dict[str, Any]:
        normalized_type = str(action.get("type", "")).strip().lower()
        if normalized_type == "double-click":
            normalized_type = "double_click"
        if normalized_type in {"type", "type text", "type-text"}:
            normalized_type = "type_text"
        if normalized_type in {"focus", "focus field", "focus-field"}:
            normalized_type = "focus_field"
        if normalized_type in {"hotkey", "keyboard_shortcut"}:
            normalized_type = "press_hotkey"
        if normalized_type in {"dragdrop", "drag_drop"}:
            normalized_type = "drag"
        if normalized_type not in {"click", "double_click", "press_hotkey", "focus_field", "type_text", "scroll", "drag", "complete", "ask_user", "stop"}:
            normalized_type = "stop"
        target_label = str(action.get("target_label") or action.get("label") or "").strip()
        chosen_candidate = None
        compact_label = re.sub(r"[^a-z0-9]+", "", target_label.lower())
        for candidate in candidates:
            if candidate.get("normalized_label") == compact_label and compact_label:
                chosen_candidate = candidate
                break
        x = action.get("x")
        y = action.get("y")
        if chosen_candidate is not None:
            x = chosen_candidate.get("x", x)
            y = chosen_candidate.get("y", y)
            target_label = target_label or str(chosen_candidate.get("label", ""))
            if normalized_type in {"click", "double_click"} and not action.get("click_action"):
                normalized_type = str(chosen_candidate.get("click_action", normalized_type))
        direction = str(action.get("direction", "down")).strip().lower()
        if direction not in {"up", "down"}:
            direction = "down"
        try:
            amount = int(action.get("amount", 1))
        except (TypeError, ValueError):
            amount = 1
        amount = max(1, min(amount, 20))
        normalized_x = clamp_normalized_coordinate(x)
        normalized_y = clamp_normalized_coordinate(y)
        default_goal_completed = normalized_type in {"click", "double_click"}
        return {
            "type": normalized_type,
            "target_label": target_label,
            "target_kind": str(action.get("target_kind") or (chosen_candidate or {}).get("kind", "")).strip().lower(),
            "x": normalized_x,
            "y": normalized_y,
            "x2": clamp_normalized_coordinate(action.get("x2"), default=normalized_x),
            "y2": clamp_normalized_coordinate(action.get("y2"), default=normalized_y),
            "confidence": clamp_confidence(action.get("confidence"), default=0.0),
            "reason": str(action.get("reason", "")).strip(),
            "expected_text_after": str(action.get("expected_text_after", "")).strip(),
            "expected_window_title": str(action.get("expected_window_title", "")).strip(),
            "expected_process_name": str(action.get("expected_process_name", "")).strip(),
            "hotkey": str(action.get("hotkey", "")).strip(),
            "text": str(action.get("text", "")).strip(),
            "direction": direction,
            "amount": amount,
            "goal_completed_if_verified": bool(action.get("goal_completed_if_verified", default_goal_completed)),
            "backend": str((chosen_candidate or {}).get("backend", action.get("backend", "unknown"))).strip().lower() or "unknown",
            "selected": bool((chosen_candidate or {}).get("selected", False)),
            "bbox": dict((chosen_candidate or {}).get("bbox", {})) if isinstance((chosen_candidate or {}).get("bbox"), dict) else {},
        }

    def _build_prompt(
        self,
        *,
        goal: str,
        state: dict[str, Any],
        latest_state: dict[str, Any],
        failed_targets: list[str],
        fused_candidates: list[dict[str, Any]],
        backend_health: dict[str, Any],
    ) -> str:
        active_window = state.get("active_window", {}) if isinstance(state.get("active_window"), dict) else {}
        failed_hint = ", ".join(failed_targets[:6]) if failed_targets else "none"
        latest_window = latest_state.get("active_window", {}) if isinstance(latest_state, dict) else {}
        latest_window_text = (
            f"{latest_window.get('title', '')} ({latest_window.get('process_name', '')})"
            if isinstance(latest_window, dict)
            else ""
        )
        latest_action = latest_state.get("last_visual_action", {}) if isinstance(latest_state, dict) else {}
        latest_action_summary = (
            f"{latest_action.get('type', '')} -> {latest_action.get('target_label', '')}"
            if isinstance(latest_action, dict) and latest_action
            else "none"
        )
        latest_screen_text = str(latest_state.get("screen_text", "")) if isinstance(latest_state, dict) else ""
        latest_screen_excerpt = latest_screen_text[:800]
        max_candidates = max(1, int(getattr(self.config.desktop_coworker, "max_target_candidates", 8)))
        retry_hint = str(latest_state.get("retry_hint", "")) if isinstance(latest_state, dict) else ""
        candidate_lines = []
        for candidate in fused_candidates[:max_candidates]:
            candidate_lines.append(
                "- "
                + json.dumps(
                    {
                        "label": candidate.get("label", ""),
                        "kind": candidate.get("kind", ""),
                        "backend": candidate.get("backend", "unknown"),
                        "confidence": candidate.get("confidence", 0.0),
                        "selected": bool(candidate.get("selected", False)),
                        "x": candidate.get("x", 500),
                        "y": candidate.get("y", 500),
                        "click_action": candidate.get("click_action", "click"),
                    },
                    ensure_ascii=False,
                )
            )
        candidate_text = "\n".join(candidate_lines) if candidate_lines else "- none"
        return (
            "You are helping a Windows desktop coworker choose exactly one next UI action.\n"
            "Use the screenshot as the primary source of truth. Extract OCR text from the screenshot and return it in screen_text.\n"
            "Use any provided OCR text only as supporting context.\n"
            "Assume coordinates are relative to the screenshot, normalized from 0 to 1000.\n"
            "Prefer the provided target candidates. They already merge stronger backends such as UI Automation and OCR boxes.\n"
            "Focus on OCR-visible or UIA-visible text targets first, but you may also use generic object candidates when the screenshot clearly shows a non-text control.\n"
            "Prefer opening visible files or clicking visible list items, rows, tree items, tabs, buttons, toggles, switches, menus, dialogs, and form fields.\n"
            "If the goal already appears complete, return completion_state=\"completed\" and action.type=\"complete\".\n"
            "If multiple plausible targets exist or confidence is low, return completion_state=\"ask_user\" with a short message.\n"
            "If the previous attempt failed and the screen still looks unchanged, do not say the task is complete.\n"
            "Mere visibility of a label is not completion. For open or click goals, completion requires a visible state change.\n"
            "When File Explorer is active and the target is a visible folder item such as Desktop, Downloads, or Documents, choose the correct visible label but do not claim success until the folder actually opens.\n"
            "When the goal is to turn Bluetooth on or off, only mark completion after the visible toggle text or switch state clearly changes to the requested state.\n"
            "If the best next step is keyboard fallback, return action.type=\"press_hotkey\".\n"
            "If text entry is needed but the field is not focused yet, return action.type=\"focus_field\" first.\n"
            "If text entry is needed after the correct field is already focused, return action.type=\"type_text\" with the exact text to enter.\n"
            "If the needed target is off-screen or more content must be revealed, return action.type=\"scroll\" with direction and amount.\n"
            "If a drag and drop interaction is clearly required, return action.type=\"drag\" with x, y, x2, and y2.\n"
            "For chat and messaging tasks, do not mark the task complete immediately after typing. Prefer a follow-up send action and verify the message text is visible in the conversation.\n"
            "Do not guess. Return JSON only.\n\n"
            f"Goal: {goal}\n"
            f"Active window: {active_window.get('title', '')} ({active_window.get('process_name', '')})\n"
            f"Previous latest window: {latest_window_text}\n"
            f"Previous last action: {latest_action_summary}\n"
            f"Backend health: {json.dumps(backend_health, ensure_ascii=False)}\n"
            f"Retry hint: {retry_hint or 'none'}\n"
            f"Previous OCR excerpt:\n{latest_screen_excerpt}\n\n"
            f"Screenshot size: {state.get('capture_width', 0)} x {state.get('capture_height', 0)}\n"
            f"Failed targets to avoid repeating: {failed_hint}\n"
            f"Candidate targets:\n{candidate_text}\n\n"
            f"OCR text:\n{state.get('screen_text', '')}\n\n"
            "Return JSON with this shape:\n"
            "{\n"
            '  "screen_text": "visible OCR text from the screenshot",\n'
            '  "screen_summary": "short summary",\n'
            '  "completion_state": "continue|completed|ask_user|failed",\n'
            '  "message": "short explanation for user when needed",\n'
            '  "goal_completed_if_verified": true,\n'
            '  "candidates": [\n'
            '    {"label": "visible text", "kind": "file|folder|row|tree|table|cell|button|menu|tab|toggle|dialog|field|combobox|icon|object|unknown", "confidence": 0.0, "x": 0, "y": 0, "click_action": "click|double_click"}\n'
            "  ],\n"
            '  "action": {\n'
            '    "type": "click|double_click|press_hotkey|focus_field|type_text|scroll|drag|complete|ask_user|stop",\n'
            '    "target_label": "chosen candidate text",\n'
            '    "x": 0,\n'
            '    "y": 0,\n'
            '    "x2": 0,\n'
            '    "y2": 0,\n'
            '    "confidence": 0.0,\n'
            '    "reason": "why this is the right next step",\n'
            '    "expected_text_after": "text that should appear after success",\n'
            '    "expected_window_title": "window title fragment expected after success",\n'
            '    "expected_process_name": "process name fragment expected after success",\n'
            '    "hotkey": "only when action.type is press_hotkey",\n'
            '    "text": "only when action.type is type_text",\n'
            '    "direction": "up|down when action.type is scroll",\n'
            '    "amount": 1,\n'
            '    "goal_completed_if_verified": true\n'
            "  }\n"
            "}\n"
            f"Limit candidates to at most {max_candidates}."
        )

    async def _request_decision(self, *, image_path: Path, prompt: str) -> str:
        if not self.config.llm.gemini_api_key:
            raise RuntimeError("Visual coworker requires GEMINI_API_KEY for screenshot-aware reasoning.")

        image_bytes = await asyncio.to_thread(image_path.read_bytes)
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": prompt},
                        {
                            "inline_data": {
                                "mime_type": self._mime_type_for_image(image_path),
                                "data": base64.b64encode(image_bytes).decode("ascii"),
                            }
                        },
                    ],
                }
            ]
        }
        return await self._request_text_completion(payload=payload, model_names=self._candidate_models())

    def _parse_json_payload(self, raw_text: str) -> dict[str, Any]:
        candidate = raw_text.strip()
        fenced = re.search(r"\{[\s\S]*\}", candidate)
        if fenced is not None:
            candidate = fenced.group(0)
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            return {
                "completion_state": "ask_user",
            "screen_text": "",
            "message": "I could not confidently understand the current screen well enough to continue safely.",
            "candidates": [],
            "action": {"type": "ask_user", "confidence": 0.0},
        }
        return parsed if isinstance(parsed, dict) else {
            "completion_state": "ask_user",
            "screen_text": "",
            "message": "I could not convert the screen analysis into a safe next step.",
            "candidates": [],
            "action": {"type": "ask_user", "confidence": 0.0},
        }

    async def _request_text_completion(self, *, payload: dict[str, Any], model_names: list[str]) -> str:
        async with httpx.AsyncClient(timeout=60.0) as client:
            data: dict[str, Any] | None = None
            last_error: Exception | None = None
            for index, model_name in enumerate(model_names):
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
                response = await client.post(url, params={"key": self.config.llm.gemini_api_key}, json=payload)
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    last_error = exc
                    if exc.response.status_code not in {400, 404} or index == len(model_names) - 1:
                        raise
                    continue
                data = response.json()
                break
        if data is None:
            if last_error is not None:
                raise last_error
            raise RuntimeError("Visual coworker could not reach a compatible Gemini model.")

        chunks: list[str] = []
        for candidate in data.get("candidates", []):
            parts = candidate.get("content", {}).get("parts", [])
            for part in parts:
                if "text" in part:
                    chunks.append(str(part["text"]))
        return "\n".join(chunks).strip()

    def _candidate_models(self) -> list[str]:
        candidates = [str(self.config.agent.model), "gemini-2.0-flash"]
        unique: list[str] = []
        seen: set[str] = set()
        for item in candidates:
            normalized = str(item).strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            unique.append(normalized)
        return unique

    def _classification_models(self) -> list[str]:
        candidates = ["gemini-2.0-flash", str(self.config.agent.model)]
        unique: list[str] = []
        seen: set[str] = set()
        for item in candidates:
            normalized = str(item).strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            unique.append(normalized)
        return unique

    def _remember_analysis(self, request_text: str, result: dict[str, Any]) -> None:
        self._analysis_cache[request_text] = dict(result)
        if len(self._analysis_cache) > 128:
            oldest_key = next(iter(self._analysis_cache))
            self._analysis_cache.pop(oldest_key, None)

    @staticmethod
    def _mime_type_for_image(path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix in {".jpg", ".jpeg"}:
            return "image/jpeg"
        if suffix == ".webp":
            return "image/webp"
        if suffix == ".bmp":
            return "image/bmp"
        return "image/png"

    def _resolve_capture_path(self, capture_path: str) -> Path:
        raw_path = Path(capture_path).expanduser()
        candidates = []
        if raw_path.is_absolute():
            candidates.append(raw_path)
        else:
            workspace_dir = Path(self.config.agent.workspace_dir).expanduser()
            candidates.extend(
                [
                    raw_path,
                    workspace_dir / raw_path,
                    workspace_dir.parent / raw_path,
                ]
            )
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved.exists():
                return resolved
        return candidates[0].resolve() if candidates else raw_path.resolve()
