"""Claude-style visual coworker loop for bounded screen-aware tasks."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from assistant.desktop_coworker.adapters import (
    detect_surface,
    verification_hint_for_path,
    verify_surface_transition,
)
from assistant.desktop_coworker.candidate_fusion import fuse_target_candidates
from assistant.desktop_coworker.models import build_artifact
from assistant.desktop_coworker.object_candidates import extract_object_candidates, object_detection_backend_health
from assistant.desktop_coworker.ocr_boxes import extract_ocr_box_candidates, extract_ocr_box_text
from assistant.desktop_coworker.recovery import DesktopCoworkerRecovery
from assistant.desktop_coworker.state import DesktopCoworkerStateCollector
from assistant.desktop_coworker.targeting import build_click_payload, normalize_target_label
from assistant.desktop_coworker.uia_backend import DesktopCoworkerUIABackend
from assistant.desktop_coworker.visual_reasoner import DesktopCoworkerVisualReasoner
from assistant.tools.image_ocr import ocr_box_backend_health


class DesktopCoworkerVisualController:
    def __init__(self, config, tool_registry) -> None:
        self.config = config
        self.tool_registry = tool_registry
        self.state = DesktopCoworkerStateCollector(config, tool_registry)
        self.recovery = DesktopCoworkerRecovery(config)
        self.reasoner = DesktopCoworkerVisualReasoner(config)
        self.uia = DesktopCoworkerUIABackend(config)

    async def can_handle(self, request_text: str) -> bool:
        required_tools = (
            (self.tool_registry.has("desktop_window_screenshot") or self.tool_registry.has("desktop_screenshot"))
            and self.tool_registry.has("desktop_mouse_click")
        )
        return (
            bool(getattr(self.config.desktop_coworker, "visual_tasks_enabled", True))
            and required_tools
            and await self.reasoner.can_handle(request_text)
        )

    async def build_plan(self, request_text: str) -> dict[str, Any] | None:
        if not await self.can_handle(request_text):
            return None
        return await self.reasoner.build_plan(request_text)

    def backend_health(self) -> dict[str, Any]:
        ocr_boxes_health = ocr_box_backend_health()
        return {
            "targeting_backend": str(getattr(self.config.desktop_coworker, "targeting_backend", "hybrid")),
            "uia": self.uia.health(),
            "ocr_boxes": ocr_boxes_health,
            "object_detection": object_detection_backend_health(),
            "legacy_visual": {"backend": "legacy", "available": True, "detail": "Gemini screenshot reasoning"},
        }

    async def run_visual_task(
        self,
        *,
        goal: str,
        task: dict[str, Any],
        session_key: str,
        user_id: str,
        connection_id: str = "",
        channel_name: str = "",
    ) -> dict[str, Any]:
        capture_target = str(getattr(self.config.desktop_coworker, "default_visual_capture_target", "window")).strip().lower() or "window"
        max_steps = max(1, int(getattr(self.config.desktop_coworker, "max_visual_steps", 8)))
        confidence_threshold = float(getattr(self.config.desktop_coworker, "visual_target_confidence_threshold", 0.7))
        ask_on_low_confidence = bool(getattr(self.config.desktop_coworker, "ask_on_low_confidence", True))
        latest_state = dict(task.get("latest_state", {}))
        failed_targets: list[str] = []
        substeps: list[dict[str, Any]] = []
        state_before: dict[str, Any] = {}
        backend_health = self.backend_health()

        for attempt in range(max_steps):
            prior_state = dict(latest_state)
            current_state = await self.state.capture(
                include_capture=True,
                include_ocr=False,
                capture_target=capture_target,
                include_clipboard=False,
            )
            current_state["capture_target"] = capture_target
            self._populate_local_observation(current_state)
            current_state["surface"] = detect_surface(current_state)
            state_before = state_before or current_state
            current_state["artifacts"] = self._append_artifacts(
                existing=list(latest_state.get("artifacts", [])),
                state=current_state,
                label=f"before-step-{attempt + 1}",
            )
            fused_candidates = self._collect_candidates(current_state)
            current_state["target_candidates"] = list(fused_candidates)
            retry_action = latest_state.pop("retry_action", None) if isinstance(latest_state.get("retry_action"), dict) else None
            retry_hint = str(latest_state.pop("retry_hint", "")).strip() if "retry_hint" in latest_state else ""
            recovery_action = retry_action if isinstance(retry_action, dict) else None
            if recovery_action is not None:
                decision = {
                    "screen_summary": retry_hint or "Retrying the previous action with an adjusted recovery strategy.",
                    "screen_text": str(current_state.get("screen_text", "")).strip(),
                    "completion_state": "continue",
                    "message": "",
                    "goal_completed_if_verified": bool(recovery_action.get("goal_completed_if_verified", False)),
                    "candidates": list(current_state.get("target_candidates", [])),
                    "action": dict(recovery_action),
                }
            else:
                decision = await self.reasoner.decide(
                    goal=goal,
                    state=current_state,
                    latest_state=latest_state,
                    failed_targets=failed_targets,
                    fused_candidates=fused_candidates,
                    backend_health=backend_health,
                )
            if str(decision.get("screen_text", "")).strip():
                current_state["screen_text"] = str(decision.get("screen_text", "")).strip()
            current_state["target_candidates"] = list(decision.get("candidates", []))
            latest_state = self._merge_state(prior_state, current_state, decision=decision)
            completion_state = str(decision.get("completion_state", "continue")).lower()
            if completion_state == "completed":
                completion_verification = self._verify_completion_state(
                    goal=goal,
                    decision=decision,
                    current_state=current_state,
                    previous_state=prior_state,
                )
                if not completion_verification["ok"]:
                    message = (
                        str(decision.get("message", "")).strip()
                        or str(completion_verification.get("message", "")).strip()
                        or "The screen does not show enough evidence that the visual task is complete yet."
                    )
                    return {
                        "status": "failed",
                        "summary": message,
                        "verification": completion_verification,
                        "state_before": state_before,
                        "state_after": current_state,
                        "latest_state": self._merge_state(
                            latest_state,
                            {
                                "current_attempt": attempt + 1,
                                "last_backend": str(decision.get("action", {}).get("backend", "legacy")).strip().lower() or "legacy",
                                "stop_reason": str(completion_verification.get("message", message)).strip(),
                                "last_candidates": list(decision.get("candidates", [])),
                            },
                        ),
                        "tool_result": {"decision": decision},
                        "substeps": substeps,
                    }
                return {
                    "status": "completed",
                    "summary": str(decision.get("message") or decision.get("screen_summary") or "The visual task is already complete."),
                    "verification": completion_verification,
                    "state_before": state_before,
                    "state_after": current_state,
                    "latest_state": self._merge_state(
                        latest_state,
                        {
                            "current_attempt": attempt + 1,
                            "last_backend": str(decision.get("action", {}).get("backend", "legacy")).strip().lower() or "legacy",
                            "stop_reason": "",
                            "last_candidates": list(decision.get("candidates", [])),
                        },
                    ),
                    "tool_result": {"decision": decision},
                    "substeps": substeps,
                }
            if completion_state in {"ask_user", "failed"}:
                message = str(decision.get("message") or "I could not safely continue with the current screen state.")
                return {
                    "status": "failed",
                    "summary": message,
                    "verification": {"ok": False, "kind": "visual_task", "message": message},
                    "state_before": state_before,
                    "state_after": current_state,
                    "latest_state": self._merge_state(
                        latest_state,
                        {
                            "current_attempt": attempt + 1,
                            "last_backend": "reasoner",
                            "stop_reason": message,
                            "last_candidates": list(decision.get("candidates", [])),
                        },
                    ),
                    "tool_result": {"decision": decision},
                    "substeps": substeps,
                }

            action = dict(decision.get("action", {}))
            action_type = str(action.get("type", "")).strip().lower()
            if action_type == "double-click":
                action_type = "double_click"
            if not action_type or action_type in {"stop", "ask_user"}:
                message = str(decision.get("message") or "I could not determine a safe next visual action.")
                return {
                    "status": "failed",
                    "summary": message,
                    "verification": {"ok": False, "kind": "visual_task", "message": message},
                    "state_before": state_before,
                    "state_after": current_state,
                    "latest_state": self._merge_state(
                        latest_state,
                        {
                            "current_attempt": attempt + 1,
                            "last_backend": str(action.get("backend", "reasoner")).strip().lower() or "reasoner",
                            "stop_reason": message,
                            "last_candidates": list(decision.get("candidates", [])),
                        },
                    ),
                    "tool_result": {"decision": decision},
                    "substeps": substeps,
                }

            confidence = float(action.get("confidence", 0.0))
            if recovery_action is None and ask_on_low_confidence and confidence < confidence_threshold:
                message = (
                    f"I found a possible visible target, but confidence is only {confidence:.2f}. "
                    "Please be more specific or choose the item manually."
                )
                return {
                    "status": "failed",
                    "summary": message,
                    "verification": {"ok": False, "kind": "visual_task", "message": message},
                    "state_before": state_before,
                    "state_after": current_state,
                    "latest_state": self._merge_state(
                        latest_state,
                        {
                            "current_attempt": attempt + 1,
                            "last_backend": str(action.get("backend", "reasoner")).strip().lower() or "reasoner",
                            "stop_reason": message,
                            "last_candidates": list(decision.get("candidates", [])),
                        },
                    ),
                    "tool_result": {"decision": decision},
                    "substeps": substeps,
                }

            action = await self._prepare_action(
                goal=goal,
                action=action,
                state=current_state,
                session_key=session_key,
                user_id=user_id,
            )
            action_result = await self._execute_action(
                action=action,
                state=current_state,
                session_key=session_key,
                user_id=user_id,
                connection_id=connection_id,
                channel_name=channel_name,
            )
            post_state = await self.state.capture(
                include_capture=True,
                include_ocr=False,
                capture_target=capture_target,
                include_clipboard=False,
            )
            post_state["capture_target"] = capture_target
            self._populate_local_observation(post_state)
            if not str(post_state.get("screen_text", "")).strip() and str(action_result.get("screen_text_after", "")).strip():
                post_state["screen_text"] = str(action_result.get("screen_text_after", "")).strip()
            post_state["surface"] = detect_surface(post_state)
            post_state["artifacts"] = self._append_artifacts(
                existing=list(current_state.get("artifacts", [])),
                state=post_state,
                label=f"after-step-{attempt + 1}",
            )
            post_state["target_candidates"] = self._collect_candidates(post_state)
            verification = self._verify_action(action=action, before=current_state, after=post_state, tool_result=action_result)
            latest_state = self._merge_state(
                latest_state,
                post_state,
                decision=decision,
                action=action,
                tool_result=action_result,
            )
            latest_state["current_attempt"] = attempt + 1
            latest_state["last_backend"] = str(action.get("backend", action_result.get("execution_mode", "legacy"))).strip().lower() or "legacy"
            latest_state["last_candidates"] = list(decision.get("candidates", []))
            latest_state["stop_reason"] = "" if verification["ok"] else str(verification.get("message", "")).strip()
            substeps.append(
                {
                    "attempt": attempt + 1,
                    "action_type": action_type,
                    "target_label": action.get("target_label", ""),
                    "backend": action.get("backend", action_result.get("execution_mode", "legacy")),
                    "confidence": confidence,
                    "status": "completed" if verification["ok"] else "failed",
                    "summary": self._substep_summary(action=action, verification=verification),
                    "verification": verification,
                    "screen_summary": decision.get("screen_summary", ""),
                }
            )
            if verification["ok"]:
                goal_completed = bool(action.get("goal_completed_if_verified") or decision.get("goal_completed_if_verified", True))
                if goal_completed:
                    return {
                        "status": "completed",
                        "summary": self._substep_summary(action=action, verification=verification),
                        "verification": verification,
                        "state_before": state_before,
                        "state_after": post_state,
                        "latest_state": latest_state,
                        "tool_result": action_result,
                        "substeps": substeps,
                    }
                continue

            failed_label = str(action.get("target_label", "")).strip()
            if failed_label:
                failed_targets.append(failed_label)
            retry_action = self.recovery.refine_action(
                action=action,
                before=current_state,
                after=post_state,
                attempts_used=attempt,
            )
            if retry_action is not None:
                latest_state["retry_hint"] = str(retry_action.get("reason", "")).strip()
                latest_state["retry_action"] = retry_action
            if not self.recovery.should_retry_visual(attempts_used=attempt, verification_failed=True):
                message = str(verification.get("message", "")).strip() or "The visual action could not be verified."
                return {
                    "status": "failed",
                    "summary": message,
                    "verification": verification,
                    "state_before": state_before,
                    "state_after": post_state,
                    "latest_state": latest_state,
                    "tool_result": action_result,
                    "substeps": substeps,
                }

        return {
            "status": "failed",
            "summary": "I reached the maximum number of visual coworker steps before the task could be verified.",
            "verification": {
                "ok": False,
                "kind": "visual_task",
                "message": "The task hit the visual step limit before it could be verified.",
            },
            "state_before": state_before,
            "state_after": latest_state,
            "latest_state": self._merge_state(
                latest_state,
                {
                    "current_attempt": max_steps,
                    "stop_reason": "The task hit the visual step limit before it could be verified.",
                },
            ),
            "tool_result": {},
            "substeps": substeps,
        }

    async def _prepare_action(
        self,
        *,
        goal: str,
        action: dict[str, Any],
        state: dict[str, Any],
        session_key: str,
        user_id: str,
    ) -> dict[str, Any]:
        prepared = dict(action)
        action_type = str(prepared.get("type", "")).strip().lower()
        normalized_goal = normalize_target_label(goal)
        if action_type == "type_text":
            text_value = str(prepared.get("text", "")).strip()
            if text_value:
                prepared.setdefault("expected_text_after", text_value)
        if str(prepared.get("target_kind", "")).strip().lower() == "toggle":
            if "turnoffbluetooth" in normalized_goal or "disablebluetooth" in normalized_goal:
                prepared.setdefault("expected_text_after", "Off")
                prepared.setdefault("target_label", str(prepared.get("target_label", "")).strip() or "On")
            elif "turnonbluetooth" in normalized_goal or "enablebluetooth" in normalized_goal:
                prepared.setdefault("expected_text_after", "On")
                prepared.setdefault("target_label", str(prepared.get("target_label", "")).strip() or "Off")
            prepared.setdefault("expected_window_title", "Devices")
            prepared.setdefault("expected_process_name", "SystemSettings")
        if action_type not in {"click", "double_click"}:
            return prepared
        resolved_target = await self._resolve_visible_path_target(
            target_label=str(prepared.get("target_label", "")).strip(),
            target_kind=str(prepared.get("target_kind", "")).strip().lower(),
            session_key=session_key,
            user_id=user_id,
            state=state,
        )
        if not resolved_target:
            return prepared
        resolved_path = str(resolved_target.get("path", "")).strip()
        resolved_kind = str(resolved_target.get("kind", "")).strip().lower() or "unknown"
        opener_alias = str(resolved_target.get("opener_alias", "")).strip().lower()
        if not resolved_path:
            return prepared
        if resolved_kind == "file" and not opener_alias:
            return prepared
        prepared["execution_mode"] = "open_known_path"
        prepared["resolved_path"] = resolved_path
        prepared["resolved_kind"] = resolved_kind
        prepared["opener_alias"] = opener_alias or ("explorer" if resolved_kind == "folder" else "")
        prepared["target_kind"] = resolved_kind
        prepared.update({key: value for key, value in verification_hint_for_path(resolved_path, opener_alias=opener_alias).items() if value})
        prepared["goal_completed_if_verified"] = True
        human_label = Path(resolved_path).name or str(prepared.get("target_label", "")).strip()
        prepared["reason"] = str(prepared.get("reason") or f"Open the visible target '{human_label}' using a deterministic path.")
        if re.search(r"\bclick(?:\s+on)?\b", goal.lower()) and not re.search(r"\bdouble click\b|\bdouble-click\b", goal.lower()):
            prepared["type"] = "click"
        return prepared

    async def _execute_action(
        self,
        *,
        action: dict[str, Any],
        state: dict[str, Any],
        session_key: str,
        user_id: str,
        connection_id: str,
        channel_name: str,
    ) -> dict[str, Any]:
        action_type = str(action.get("type", "")).strip().lower()
        active_window = state.get("active_window", {}) if isinstance(state.get("active_window"), dict) else {}
        expected_window_title = str(active_window.get("title", "")).strip()
        expected_process_name = str(active_window.get("process_name", "")).strip()
        execution_mode = str(action.get("execution_mode", "")).strip().lower()
        uia_result = self._try_uia_action(action=action, state=state)
        if uia_result is not None and str(uia_result.get("status", "")).strip().lower() == "completed":
            return uia_result
        if execution_mode == "open_known_path":
            resolved_path = str(action.get("resolved_path", "")).strip()
            opener_alias = str(action.get("opener_alias", "")).strip().lower() or "explorer"
            if not resolved_path:
                raise RuntimeError("The visual coworker could not resolve a concrete folder path for the visible target.")
            if not self.tool_registry.has("apps_open"):
                raise RuntimeError("Explorer folder navigation requires desktop app control tools.")
            result = await self.tool_registry.dispatch("apps_open", {"target": opener_alias, "args": [resolved_path]})
            return {
                **result,
                "status": "completed",
                "execution_mode": execution_mode,
                "resolved_path": resolved_path,
                "opener_alias": opener_alias,
            }
        if action_type in {"click", "double_click"}:
            label = normalize_target_label(str(action.get("target_label", "")))
            generic_semantic_kind = str(action.get("target_kind", "")).strip().lower()
            if not getattr(self.config.desktop_coworker, "allow_semantic_clicks", False) and generic_semantic_kind not in {"", "file", "row"}:
                raise RuntimeError("Semantic screen clicks are disabled for non-file visual targets.")
            payload = build_click_payload(
                x_norm=action.get("x"),
                y_norm=action.get("y"),
                count=2 if action_type == "double_click" else 1,
                state=state,
                expected_window_title=expected_window_title,
                expected_process_name=expected_process_name,
            )
            payload.update(
                {
                    "session_key": session_key,
                    "session_id": str(session_key),
                    "user_id": user_id,
                    "connection_id": connection_id,
                    "channel_name": channel_name,
                    "visual_target_label": label,
                }
            )
            result = await self.tool_registry.dispatch("desktop_mouse_click", payload)
            return {**result, "execution_mode": "visual_click"}
        if action_type == "focus_field":
            label = normalize_target_label(str(action.get("target_label", "")))
            payload = build_click_payload(
                x_norm=action.get("x"),
                y_norm=action.get("y"),
                count=1,
                state=state,
                expected_window_title=expected_window_title,
                expected_process_name=expected_process_name,
            )
            payload.update(
                {
                    "session_key": session_key,
                    "session_id": str(session_key),
                    "user_id": user_id,
                    "connection_id": connection_id,
                    "channel_name": channel_name,
                    "visual_target_label": label,
                }
            )
            result = await self.tool_registry.dispatch("desktop_mouse_click", payload)
            return {**result, "execution_mode": "visual_focus"}
        if action_type == "press_hotkey":
            if not self.tool_registry.has("desktop_keyboard_hotkey"):
                raise RuntimeError("Visual keyboard fallback requires desktop hotkey input tools.")
            payload = {
                "hotkey": str(action.get("hotkey", "")).strip(),
                "expected_window_title": expected_window_title,
                "expected_process_name": expected_process_name,
                "session_key": session_key,
                "session_id": str(session_key),
                "user_id": user_id,
                "connection_id": connection_id,
                "channel_name": channel_name,
            }
            return await self.tool_registry.dispatch("desktop_keyboard_hotkey", payload)
        if action_type == "type_text":
            if not self.tool_registry.has("desktop_keyboard_type"):
                raise RuntimeError("Visual text entry requires desktop keyboard input tools.")
            text = str(action.get("text", ""))
            if not text:
                raise RuntimeError("The visual reasoner requested text entry without any text to type.")
            payload = {
                "text": text,
                "expected_window_title": expected_window_title,
                "expected_process_name": expected_process_name,
                "session_key": session_key,
                "session_id": str(session_key),
                "user_id": user_id,
                "connection_id": connection_id,
                "channel_name": channel_name,
            }
            return await self.tool_registry.dispatch("desktop_keyboard_type", payload)
        if action_type == "scroll":
            if not self.tool_registry.has("desktop_mouse_scroll"):
                raise RuntimeError("Visual scrolling requires desktop mouse scroll input tools.")
            payload = {
                "direction": str(action.get("direction", "down")).strip().lower() or "down",
                "amount": max(1, int(action.get("amount", 1))),
                "expected_window_title": expected_window_title,
                "expected_process_name": expected_process_name,
                "session_key": session_key,
                "session_id": str(session_key),
                "user_id": user_id,
                "connection_id": connection_id,
                "channel_name": channel_name,
            }
            return await self.tool_registry.dispatch("desktop_mouse_scroll", payload)
        if action_type == "drag":
            if not self.tool_registry.has("desktop_mouse_drag"):
                raise RuntimeError("Visual drag actions require desktop mouse drag input tools.")
            payload = {
                "x": max(0, int(action.get("x", 0))),
                "y": max(0, int(action.get("y", 0))),
                "end_x": max(0, int(action.get("x2", action.get("x", 0)))),
                "end_y": max(0, int(action.get("y2", action.get("y", 0)))),
                "coordinate_space": "active_window" if str(state.get("capture_target", "window")).lower() == "window" else "screen",
                "expected_window_title": expected_window_title,
                "expected_process_name": expected_process_name,
                "session_key": session_key,
                "session_id": str(session_key),
                "user_id": user_id,
                "connection_id": connection_id,
                "channel_name": channel_name,
            }
            return await self.tool_registry.dispatch("desktop_mouse_drag", payload)
        raise RuntimeError(f"Unsupported visual coworker action '{action_type}'.")

    def _try_uia_action(self, *, action: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | None:
        if not bool(getattr(self.config.desktop_coworker, "uia_enabled", True)):
            return None
        health = self.uia.health()
        if not bool(health.get("available", False)):
            return None
        action_type = str(action.get("type", "")).strip().lower()
        target_kind = str(action.get("target_kind", "")).strip().lower()
        if action_type not in {"click", "double_click", "focus_field", "type_text"}:
            return None
        if action_type == "type_text" and not str(action.get("text", "")).strip():
            return None
        uia_friendly_kinds = {"button", "toggle", "menu", "tab", "field", "combobox", "row", "tree", "cell", "table", "dialog", "panel", "icon"}
        backend = str(action.get("backend", "")).strip().lower()
        if target_kind not in uia_friendly_kinds and action_type != "type_text":
            return None
        if backend != "uia" and target_kind not in uia_friendly_kinds:
            return None
        result = self.uia.perform_action(action=action, state=state)
        return result if isinstance(result, dict) else None

    def _verify_action(
        self,
        *,
        action: dict[str, Any],
        before: dict[str, Any],
        after: dict[str, Any],
        tool_result: dict[str, Any],
    ) -> dict[str, Any]:
        tool_status = str(tool_result.get("status", "completed")).lower()
        if tool_status.startswith("blocked") or tool_status in {"rejected", "expired", "failed"}:
            return {
                "ok": False,
                "kind": "visual_task",
                "message": f"The visual action returned status '{tool_status}'.",
            }

        action_type = str(action.get("type", "")).strip().lower()
        target_label = normalize_target_label(str(action.get("target_label", "")))
        before_text = normalize_target_label(str(before.get("screen_text", "")))
        after_text = normalize_target_label(str(after.get("screen_text", "")))
        expected_text_after = normalize_target_label(str(action.get("expected_text_after", "")))
        before_window = before.get("active_window", {}) if isinstance(before.get("active_window"), dict) else {}
        after_window = after.get("active_window", {}) if isinstance(after.get("active_window"), dict) else {}
        before_window_text = normalize_target_label(
            f"{before_window.get('title', '')} {before_window.get('process_name', '')}"
        )
        after_window_text = normalize_target_label(
            f"{after_window.get('title', '')} {after_window.get('process_name', '')}"
        )
        expected_window_title = normalize_target_label(str(action.get("expected_window_title", "")))
        expected_process_name = normalize_target_label(str(action.get("expected_process_name", "")))
        execution_mode = str(tool_result.get("execution_mode", action.get("execution_mode", ""))).strip().lower()
        action_confidence = float(action.get("confidence", 0.0) or 0.0)
        target_kind = str(action.get("target_kind", "")).strip().lower()
        before_title = normalize_target_label(str(before_window.get("title", "")))
        after_title = normalize_target_label(str(after_window.get("title", "")))
        screen_changed = before_text != after_text
        window_changed = before_window_text != after_window_text
        new_element_appeared = self._has_new_visible_element(before, after)
        uia_before = tool_result.get("uia_state_before", {}) if isinstance(tool_result.get("uia_state_before"), dict) else {}
        uia_after = tool_result.get("uia_state_after", {}) if isinstance(tool_result.get("uia_state_after"), dict) else {}
        uia_before_value = normalize_target_label(str(uia_before.get("value", "")))
        uia_after_value = normalize_target_label(str(uia_after.get("value", "")))
        uia_after_toggle = normalize_target_label(str(uia_after.get("toggle_state", "")))
        uia_after_label = normalize_target_label(str(uia_after.get("label", "")))
        if execution_mode.startswith("uia_"):
            if action_type == "focus_field" and bool(uia_after.get("focused", False)):
                return {"ok": True, "kind": "uia_focus", "message": ""}
            if action_type == "type_text":
                expected_value = normalize_target_label(str(action.get("text", "")))
                if expected_value and (expected_value in uia_after_value or expected_value in after_text):
                    return {"ok": True, "kind": "uia_value", "message": ""}
            if target_kind == "toggle":
                if str(uia_before.get("toggle_state", "")) != str(uia_after.get("toggle_state", "")) and str(uia_after.get("toggle_state", "")):
                    return {"ok": True, "kind": "uia_toggle", "message": ""}
                if expected_text_after and (
                    expected_text_after in uia_after_value
                    or expected_text_after in uia_after_toggle
                    or expected_text_after in uia_after_label
                    or expected_text_after in after_text
                ):
                    return {"ok": True, "kind": "uia_toggle", "message": ""}
            if target_kind in {"tab", "menu", "row", "tree", "cell", "table"} and (
                bool(uia_after.get("selected", False)) or bool(uia_after.get("focused", False))
            ):
                return {"ok": True, "kind": "uia_select", "message": ""}

        adapter_verification = verify_surface_transition(
            action=action,
            before=before,
            after=after,
            tool_result=tool_result,
        )
        if adapter_verification is not None:
            return adapter_verification

        if action_type in {"click", "double_click", "focus_field"} and self._target_became_selected(
            before=before,
            after=after,
            target_label=target_label,
        ):
            return {"ok": True, "kind": "selection_state", "message": ""}
        if expected_window_title and expected_window_title in after_window_text:
            if execution_mode != "open_known_path" or expected_window_title in after_title:
                return {"ok": True, "kind": "visual_task", "message": ""}
        if expected_process_name and expected_process_name in after_window_text:
            if execution_mode != "open_known_path" or window_changed:
                return {"ok": True, "kind": "visual_task", "message": ""}
        if expected_text_after and expected_text_after in after_text and (screen_changed or window_changed):
            return {"ok": True, "kind": "visual_task", "message": ""}
        if execution_mode == "open_known_path" and expected_window_title:
            if expected_window_title in after_title and (after_title != before_title or screen_changed):
                return {"ok": True, "kind": "visual_task", "message": ""}
            return {
                "ok": False,
                "kind": "visual_task",
                "message": f"The visible application did not switch to '{action.get('target_label', '')}' after the deterministic open step.",
            }
        if action_type in {"click", "double_click"}:
            if target_label and target_label in before_text and target_label not in after_text:
                return {"ok": True, "kind": "visual_task", "message": ""}
            if window_changed:
                return {"ok": True, "kind": "visual_task", "message": ""}
            if screen_changed:
                return {"ok": True, "kind": "visual_task", "message": ""}
            if new_element_appeared:
                return {"ok": True, "kind": "visual_task", "message": ""}
        if action_type == "type_text":
            if expected_text_after and expected_text_after in after_text:
                return {"ok": True, "kind": "visual_task", "message": ""}
            if before_text != after_text:
                return {"ok": True, "kind": "visual_task", "message": ""}
            if new_element_appeared:
                return {"ok": True, "kind": "visual_task", "message": ""}
        if action_type == "scroll":
            if expected_text_after and expected_text_after in after_text:
                return {"ok": True, "kind": "visual_task", "message": ""}
            if before_text != after_text:
                return {"ok": True, "kind": "visual_task", "message": ""}
            if new_element_appeared:
                return {"ok": True, "kind": "visual_task", "message": ""}
        if action_type == "press_hotkey":
            if before_text != after_text:
                return {"ok": True, "kind": "visual_task", "message": ""}
            if new_element_appeared:
                return {"ok": True, "kind": "visual_task", "message": ""}
        if action_type == "focus_field":
            if self._target_became_selected(before=before, after=after, target_label=target_label):
                return {"ok": True, "kind": "focus_field", "message": ""}
            if before_text != after_text or new_element_appeared:
                return {"ok": True, "kind": "focus_field", "message": ""}
        if action_type == "drag":
            if window_changed or screen_changed or new_element_appeared:
                return {"ok": True, "kind": "drag", "message": ""}
        if execution_mode != "open_known_path" and target_kind not in {"file", "row"} and action_confidence > 0.85:
            return {"ok": True, "kind": "visual_task", "message": ""}
        return {
            "ok": False,
            "kind": "visual_task",
            "message": "The visible target did not change in an expected way after the action.",
        }

    def _verify_completion_state(
        self,
        *,
        goal: str,
        decision: dict[str, Any],
        current_state: dict[str, Any],
        previous_state: dict[str, Any],
    ) -> dict[str, Any]:
        action = dict(decision.get("action", {}))
        if not action and isinstance(previous_state.get("last_visual_action"), dict):
            action = dict(previous_state.get("last_visual_action", {}))
        current_window = current_state.get("active_window", {}) if isinstance(current_state.get("active_window"), dict) else {}
        previous_window = previous_state.get("active_window", {}) if isinstance(previous_state.get("active_window"), dict) else {}
        current_window_text = normalize_target_label(f"{current_window.get('title', '')} {current_window.get('process_name', '')}")
        previous_window_text = normalize_target_label(f"{previous_window.get('title', '')} {previous_window.get('process_name', '')}")
        current_text = normalize_target_label(str(current_state.get("screen_text", "")))
        previous_text = normalize_target_label(str(previous_state.get("screen_text", "")))
        target_label = normalize_target_label(str(action.get("target_label", "")))
        expected_window_title = normalize_target_label(str(action.get("expected_window_title", "")))
        expected_process_name = normalize_target_label(str(action.get("expected_process_name", "")))
        expected_text_after = normalize_target_label(str(action.get("expected_text_after", "")))
        action_confidence = float(action.get("confidence", 0.0) or 0.0)
        screen_changed = current_text != previous_text
        window_changed = current_window_text != previous_window_text
        new_element_appeared = self._has_new_visible_element(previous_state, current_state)

        if expected_window_title and expected_window_title in current_window_text:
            if not previous_window_text or window_changed or screen_changed or new_element_appeared:
                return {"ok": True, "kind": "visual_task", "message": ""}
        if expected_process_name and expected_process_name in current_window_text:
            if not previous_window_text or window_changed or screen_changed or new_element_appeared:
                return {"ok": True, "kind": "visual_task", "message": ""}
        if expected_text_after and expected_text_after in current_text:
            if not previous_text or screen_changed or window_changed or new_element_appeared:
                return {"ok": True, "kind": "visual_task", "message": ""}
        if target_label and target_label in current_window_text:
            if not previous_window_text or window_changed or screen_changed or new_element_appeared:
                return {"ok": True, "kind": "visual_task", "message": ""}
        if screen_changed or new_element_appeared:
            return {"ok": True, "kind": "visual_task", "message": ""}
        if action_confidence > 0.85:
                return {"ok": True, "kind": "visual_task", "message": ""}
        return {
            "ok": False,
            "kind": "visual_task",
            "message": (
                f"I can still see the same screen after trying to {goal.strip()}. "
                "I don't have enough evidence that the visible action really succeeded."
            ),
        }

    def _merge_state(
        self,
        existing_state: dict[str, Any],
        next_state: dict[str, Any],
        *,
        decision: dict[str, Any] | None = None,
        action: dict[str, Any] | None = None,
        tool_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        merged = dict(existing_state)
        merged.update(next_state)
        if decision is not None:
            merged["target_candidates"] = list(decision.get("candidates", []))
            merged["screen_summary"] = str(decision.get("screen_summary", "")).strip()
        if action is not None:
            merged["last_visual_action"] = dict(action)
        if tool_result is not None:
            merged["last_visual_tool_result"] = dict(tool_result)
        return merged

    def _collect_candidates(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        max_candidates = max(1, int(getattr(self.config.desktop_coworker, "max_target_candidates", 8)))
        targeting_backend = str(getattr(self.config.desktop_coworker, "targeting_backend", "hybrid")).strip().lower() or "hybrid"
        uia_candidates: list[dict[str, Any]] = []
        ocr_candidates: list[dict[str, Any]] = []
        object_candidates: list[dict[str, Any]] = []
        if targeting_backend in {"uia", "hybrid"} and bool(getattr(self.config.desktop_coworker, "uia_enabled", True)):
            uia_candidates = self.uia.collect_candidates(state, limit=max_candidates)
        if targeting_backend in {"ocr_boxes", "hybrid"} and bool(getattr(self.config.desktop_coworker, "ocr_boxes_enabled", True)):
            ocr_candidates = extract_ocr_box_candidates(state, limit=max_candidates, config=self.config)
        if targeting_backend == "hybrid" and bool(getattr(self.config.desktop_coworker, "object_detection_enabled", True)):
            object_candidates = extract_object_candidates(
                state,
                limit=max(1, int(getattr(self.config.desktop_coworker, "max_object_candidates", 6))),
                config=self.config,
            )
        return fuse_target_candidates(uia_candidates, ocr_candidates, object_candidates, limit=max_candidates)

    def _populate_local_observation(self, state: dict[str, Any]) -> None:
        if not str(state.get("screen_text", "")).strip():
            state["screen_text"] = extract_ocr_box_text(
                state,
                config=self.config,
                max_chars=int(getattr(self.config.desktop_vision, "max_ocr_characters", 12000) or 12000),
            )

    def _append_artifacts(self, *, existing: list[dict[str, Any]], state: dict[str, Any], label: str) -> list[dict[str, Any]]:
        artifacts = [dict(item) for item in existing if isinstance(item, dict)]
        capture_path = str(state.get("capture_path", "")).strip()
        if not capture_path:
            return artifacts
        if any(str(item.get("path", "")).strip() == capture_path for item in artifacts):
            return artifacts[-max(1, int(getattr(self.config.desktop_coworker, "artifact_retention_count", 20))):]
        artifacts.append(build_artifact(path=capture_path, kind="screenshot", label=label))
        retention = max(1, int(getattr(self.config.desktop_coworker, "artifact_retention_count", 20)))
        return artifacts[-retention:]

    def _is_file_explorer_window(self, state: dict[str, Any]) -> bool:
        active_window = state.get("active_window", {}) if isinstance(state.get("active_window"), dict) else {}
        title = str(active_window.get("title", "")).strip().lower()
        process_name = str(active_window.get("process_name", "")).strip().lower()
        return process_name == "explorer" or "file explorer" in title or title in {"home", "desktop", "downloads", "documents"}

    async def _resolve_visible_path_target(
        self,
        *,
        target_label: str,
        target_kind: str,
        session_key: str,
        user_id: str,
        state: dict[str, Any],
    ) -> dict[str, Any] | None:
        label = str(target_label).strip().strip("\"'")
        if not label:
            return None
        normalized = normalize_target_label(label)
        configured = getattr(self.config.system_access, "path_rules", [])
        for rule in configured:
            raw_path = getattr(rule, "path", None)
            if raw_path is None and isinstance(rule, dict):
                raw_path = rule.get("path")
            if raw_path is None:
                continue
            candidate = Path(str(raw_path)).expanduser().resolve()
            if normalize_target_label(candidate.name) == normalized:
                return {"path": str(candidate).replace("\\", "/"), "kind": "folder", "opener_alias": "explorer"}

        common_folders = {
            "desktop": "~/Desktop",
            "downloads": "~/Downloads",
            "documents": "~/Documents",
            "pictures": "~/Pictures",
            "music": "~/Music",
            "videos": "~/Videos",
        }
        if normalized in common_folders:
            return {
                "path": str(Path(common_folders[normalized]).expanduser().resolve()).replace("\\", "/"),
                "kind": "folder",
                "opener_alias": "explorer",
            }

        if not self.tool_registry.has("search_host_files"):
            return None
        try:
            result = await self.tool_registry.dispatch(
                "search_host_files",
                {
                    "root": "@allowed",
                    "name_query": label,
                    "directories_only": bool(target_kind == "folder"),
                    "files_only": bool(target_kind == "file"),
                    "limit": 5,
                    "session_id": str(session_key or "coworker-visual"),
                    "user_id": str(user_id),
                },
            )
        except Exception:
            return None
        matches = [
            item
            for item in result.get("matches", [])
            if isinstance(item, dict)
            and (
                target_kind not in {"file", "folder"}
                or (target_kind == "folder" and bool(item.get("is_dir")))
                or (target_kind == "file" and not bool(item.get("is_dir")))
            )
        ]
        exact = [
            item
            for item in matches
            if normalize_target_label(str(item.get("name", ""))) == normalized
        ]
        if len(exact) == 1:
            return self._resolved_target_for_path(str(exact[0].get("path", "")).replace("\\", "/"), state=state)
        if len(matches) == 1:
            return self._resolved_target_for_path(str(matches[0].get("path", "")).replace("\\", "/"), state=state)
        return None

    def _resolved_target_for_path(self, path: str, *, state: dict[str, Any]) -> dict[str, Any]:
        resolved = Path(path.replace("\\", "/"))
        if resolved.suffix == "":
            return {"path": str(resolved).replace("\\", "/"), "kind": "folder", "opener_alias": "explorer"}
        suffix = resolved.suffix.lower()
        active_window = state.get("active_window", {}) if isinstance(state.get("active_window"), dict) else {}
        process_name = normalize_target_label(str(active_window.get("process_name", "")))
        if process_name == "excel" or suffix in {".xlsx", ".xls", ".xlsm", ".csv"}:
            return {"path": str(resolved).replace("\\", "/"), "kind": "file", "opener_alias": "excel"}
        if process_name == "word" or suffix in {".doc", ".docx"}:
            return {"path": str(resolved).replace("\\", "/"), "kind": "file", "opener_alias": "word"}
        if suffix in {".txt", ".md", ".log"}:
            return {"path": str(resolved).replace("\\", "/"), "kind": "file", "opener_alias": "notepad"}
        return {"path": str(resolved).replace("\\", "/"), "kind": "file", "opener_alias": ""}

    def _substep_summary(self, *, action: dict[str, Any], verification: dict[str, Any]) -> str:
        label = str(action.get("target_label", "")).strip()
        action_type = str(action.get("type", "action")).strip().lower().replace("_", " ")
        target_kind = str(action.get("target_kind", "")).strip().lower()
        expected_text_after = str(action.get("expected_text_after", "")).strip()
        if verification.get("ok", False):
            if target_kind == "toggle" and expected_text_after:
                return f"I changed the visible toggle and verified that it now shows `{expected_text_after}`."
            if str(action.get("execution_mode", "")).strip().lower() == "open_known_folder" and label:
                return f"I opened the visible `{label}` folder in Explorer and verified that the window changed."
            if label:
                return f"I found `{label}` on the current screen and completed a {action_type} action successfully."
            return f"I completed the visual {action_type} action successfully."
        if label:
            return f"I targeted `{label}`, but could not verify that the {action_type} action succeeded."
        return str(verification.get("message", "The visual action could not be verified."))

    def _has_new_visible_element(self, before: dict[str, Any], after: dict[str, Any]) -> bool:
        before_labels = self._candidate_labels(before)
        after_labels = self._candidate_labels(after)
        return bool(after_labels - before_labels)

    def _candidate_labels(self, state: dict[str, Any]) -> set[str]:
        candidates = state.get("target_candidates", [])
        if not isinstance(candidates, list):
            return set()
        labels: set[str] = set()
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            normalized = normalize_target_label(str(candidate.get("normalized_label") or candidate.get("label") or ""))
            if normalized:
                labels.add(normalized)
        return labels

    def _target_became_selected(self, *, before: dict[str, Any], after: dict[str, Any], target_label: str) -> bool:
        if not target_label:
            return False
        before_selected = self._candidate_selected(before, target_label)
        after_selected = self._candidate_selected(after, target_label)
        return not before_selected and after_selected

    def _candidate_selected(self, state: dict[str, Any], target_label: str) -> bool:
        candidates = state.get("target_candidates", [])
        if not isinstance(candidates, list):
            return False
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            normalized = normalize_target_label(str(candidate.get("normalized_label") or candidate.get("label") or ""))
            if normalized != target_label:
                continue
            return bool(candidate.get("selected", False))
        return False
