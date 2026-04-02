"""Phase 6 coworker service for bounded verified desktop tasks."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any

from assistant.desktop_coworker.executor import DesktopCoworkerExecutor
from assistant.desktop_coworker.models import (
    DesktopCoworkerTask,
    DesktopInteractionContext,
    DesktopRequestAnalysis,
    utc_now_iso,
)
from assistant.desktop_coworker.planner import DesktopCoworkerPlanner
from assistant.desktop_coworker.state import DesktopCoworkerStateCollector
from assistant.desktop_coworker.store import DesktopCoworkerStore
from assistant.desktop_coworker.targeting import normalize_target_label
from assistant.desktop_coworker.visual_controller import DesktopCoworkerVisualController


class DesktopCoworkerService:
    def __init__(self, config, tool_registry) -> None:
        self.config = config
        self.tool_registry = tool_registry
        self.store = DesktopCoworkerStore(config)
        self.planner = DesktopCoworkerPlanner(config, tool_registry)
        self.visual = DesktopCoworkerVisualController(config, tool_registry)
        self.executor = DesktopCoworkerExecutor(config, tool_registry)
        self.state = DesktopCoworkerStateCollector(config, tool_registry)
        self._desktop_contexts: dict[str, DesktopInteractionContext] = {}

    async def initialize(self) -> None:
        await self.store.initialize()

    def ensure_enabled(self) -> None:
        if not bool(getattr(self.config.desktop_coworker, "enabled", False)):
            raise RuntimeError("Desktop coworker is not enabled.")

    async def can_handle_request(self, request_text: str) -> bool:
        if not bool(getattr(self.config.desktop_coworker, "enabled", False)):
            return False
        try:
            analysis = await self.analyze_request(session_key="main", request_text=request_text)
            return bool(analysis.get("desktop_ui_task", False))
        except Exception:
            return False

    async def analyze_request(self, *, session_key: str, request_text: str) -> dict[str, Any]:
        if not bool(getattr(self.config.desktop_coworker, "enabled", False)):
            return DesktopRequestAnalysis(desktop_ui_task=False).to_dict()
        normalized_request = request_text.strip()
        if not normalized_request:
            return DesktopRequestAnalysis(desktop_ui_task=False).to_dict()

        builtin_plan = self.planner.plan(normalized_request)
        if builtin_plan is not None:
            return DesktopRequestAnalysis(
                desktop_ui_task=True,
                task_kind="structured",
                summary=str(builtin_plan.get("summary", "")).strip(),
                normalized_request=normalized_request,
                requires_visual_context=False,
                route_kind="structured",
            ).to_dict()

        active_context = await self.get_interaction_context(session_key=session_key)
        contextual = self._resolve_contextual_followup(normalized_request, active_context)
        if contextual is not None:
            contextual_request = str(contextual.get("normalized_request", "")).strip() or normalized_request
            contextual_plan = self.planner.plan(contextual_request)
            return DesktopRequestAnalysis(
                desktop_ui_task=True,
                task_kind="structured" if contextual_plan is not None else "visual",
                summary=str(contextual.get("summary", "")).strip() or contextual_request,
                normalized_request=contextual_request,
                requires_visual_context=True,
                route_kind="followup_visual",
            ).to_dict()

        analysis = await self.visual.reasoner.analyze_request(normalized_request)
        payload = DesktopRequestAnalysis(
            desktop_ui_task=bool(analysis.get("desktop_ui_task", False)),
            task_kind=str(analysis.get("task_kind", "non_desktop")).strip().lower() or "non_desktop",
            summary=str(analysis.get("summary", "")).strip(),
            normalized_request=str(analysis.get("normalized_request", "")).strip() or normalized_request,
            requires_visual_context=bool(analysis.get("requires_visual_context", False)),
            route_kind="structured" if str(analysis.get("task_kind", "")).strip().lower() == "structured" else ("visual" if bool(analysis.get("desktop_ui_task", False)) else "none"),
        )
        return payload.to_dict()

    async def get_interaction_context(self, *, session_key: str) -> dict[str, Any]:
        context = self._desktop_contexts.get(session_key)
        if context is None:
            return {}
        if self._context_expired(context):
            self._desktop_contexts.pop(session_key, None)
            return {}
        current_window = await self.state._active_window()
        if context.active_window and current_window and not self._same_window_family(context.active_window, current_window):
            self._desktop_contexts.pop(session_key, None)
            return {}
        return context.to_dict()

    async def plan_task(
        self,
        *,
        user_id: str,
        session_key: str,
        request_text: str,
        request_analysis: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.ensure_enabled()
        analysis = request_analysis or await self.analyze_request(session_key=session_key, request_text=request_text)
        normalized_request = str(analysis.get("normalized_request", "")).strip() or request_text.strip()
        desktop_context = await self.get_interaction_context(session_key=session_key)
        plan = await self.planner.plan_request(
            normalized_request,
            request_analysis=analysis,
            desktop_context=desktop_context,
        )
        if plan is None and (str(analysis.get("task_kind", "")).strip().lower() == "visual" or bool(analysis.get("requires_visual_context", False))):
            plan = await self.visual.build_plan(normalized_request)
        if plan is None:
            raise ValueError("I couldn't turn that into a bounded coworker task yet. Try /coworker help for supported requests.")
        task = DesktopCoworkerTask.new(
            user_id=user_id,
            session_key=session_key,
            request_text=normalized_request,
            summary=str(plan["summary"]),
            steps=list(plan["steps"]),
            status="planned",
        )
        await self.store.create_task(task)
        initial_state = {
            "route_kind": str(analysis.get("route_kind", "")).strip() or ("visual" if any(str(step.get("type", "")) == "visual_task" for step in plan.get("steps", [])) else "structured"),
            "request_analysis": dict(analysis),
        }
        await self.store.update_task(task.task_id, latest_state=initial_state)
        stored = await self.store.get_task(task.task_id) or {}
        return self._decorate_task(stored)

    async def run_task_request(
        self,
        *,
        user_id: str,
        session_key: str,
        request_text: str,
        request_analysis: dict[str, Any] | None = None,
        connection_id: str = "",
        channel_name: str = "",
    ) -> dict[str, Any]:
        task = await self.plan_task(
            user_id=user_id,
            session_key=session_key,
            request_text=request_text,
            request_analysis=request_analysis,
        )
        result = await self.run_task(
            user_id=user_id,
            task_id=str(task["task_id"]),
            connection_id=connection_id,
            channel_name=channel_name,
        )
        self._remember_context(result)
        return result

    async def run_task(
        self,
        *,
        user_id: str,
        task_id: str,
        connection_id: str = "",
        channel_name: str = "",
    ) -> dict[str, Any]:
        self.ensure_enabled()
        task = await self._require_task(user_id, task_id)
        if str(task.get("status")) in {"completed", "failed", "stopped"}:
            return self._decorate_task(task)
        while int(task.get("current_step_index", 0)) < int(task.get("total_steps", 0)):
            task = await self.step_task(
                user_id=user_id,
                task_id=task_id,
                connection_id=connection_id,
                channel_name=channel_name,
            )
            if str(task.get("status")) in {"failed", "stopped"}:
                break
        decorated = self._decorate_task(task)
        self._remember_context(decorated)
        return decorated

    async def step_task(
        self,
        *,
        user_id: str,
        task_id: str,
        connection_id: str = "",
        channel_name: str = "",
    ) -> dict[str, Any]:
        self.ensure_enabled()
        task = await self._require_task(user_id, task_id)
        status = str(task.get("status", "planned"))
        if status in {"completed", "failed", "stopped"}:
            return self._decorate_task(task)
        current_index = int(task.get("current_step_index", 0))
        if current_index >= int(task.get("total_steps", 0)):
            updated = await self.store.update_task(task_id, status="completed", completed_at=utc_now_iso())
            return updated or task

        step_result = await self.executor.execute_next_step(
            task=task,
            session_key=str(task.get("session_key", "main")),
            user_id=user_id,
            connection_id=connection_id,
            channel_name=channel_name,
        )
        next_index = current_index + (1 if step_result["status"] == "completed" or bool(task["steps"][current_index].get("continue_on_error", False)) else 0)
        next_status = "in_progress"
        completed_at = ""
        error = ""
        if step_result["status"] == "failed" and not bool(task["steps"][current_index].get("continue_on_error", False)):
            next_status = "failed"
            error = str(step_result.get("verification", {}).get("message", "")).strip() or "Step verification failed."
        elif next_index >= int(task.get("total_steps", 0)):
            next_status = "completed"
            completed_at = utc_now_iso()

        transcript = list(task.get("transcript", []))
        transcript_entry = dict(step_result)
        if not bool(getattr(self.config.desktop_coworker, "store_transcripts", True)):
            transcript_entry = {
                "step_index": step_result.get("step_index"),
                "step_type": step_result.get("step_type"),
                "status": step_result.get("status"),
                "summary": step_result.get("summary"),
            }
        transcript.append(transcript_entry)
        updated = await self.store.update_task(
            task_id,
            status=next_status,
            current_step_index=next_index,
            latest_state=dict(step_result.get("latest_state", {})),
            transcript=transcript,
            error=error,
            completed_at=completed_at,
        )
        decorated = self._decorate_task(updated or task)
        self._remember_context(decorated)
        return decorated

    async def retry_task(
        self,
        *,
        user_id: str,
        task_id: str,
        connection_id: str = "",
        channel_name: str = "",
    ) -> dict[str, Any]:
        self.ensure_enabled()
        task = await self._require_task(user_id, task_id)
        status = str(task.get("status", "planned"))
        if status == "completed":
            return self._decorate_task(task)
        if status in {"failed", "stopped"}:
            retried = await self.store.update_task(task_id, status="in_progress", error="", completed_at="")
            task = retried or task
        result = await self.step_task(
            user_id=user_id,
            task_id=task_id,
            connection_id=connection_id,
            channel_name=channel_name,
        )
        self._remember_context(result)
        return result

    async def stop_task(self, *, user_id: str, task_id: str) -> dict[str, Any]:
        self.ensure_enabled()
        task = await self._require_task(user_id, task_id)
        if str(task.get("status")) == "completed":
            return self._decorate_task(task)
        stopped = await self.store.stop_task(task_id)
        decorated = self._decorate_task(stopped or task)
        self._remember_context(decorated)
        return decorated

    async def get_task(self, *, user_id: str, task_id: str) -> dict[str, Any]:
        self.ensure_enabled()
        task = await self._require_task(user_id, task_id)
        return self._decorate_task(task)

    async def list_tasks(self, *, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        self.ensure_enabled()
        return [self._decorate_task(task) for task in await self.store.list_tasks(user_id, limit=limit)]

    def backend_health(self) -> dict[str, Any]:
        return self.visual.backend_health()

    async def _require_task(self, user_id: str, task_id: str) -> dict[str, Any]:
        task = await self.store.get_task(task_id)
        if task is None or str(task.get("user_id", "")) != user_id:
            raise KeyError(f"Unknown coworker task '{task_id}'.")
        return task

    def _decorate_task(self, task: dict[str, Any]) -> dict[str, Any]:
        latest_state = dict(task.get("latest_state", {}))
        decorated = dict(task)
        decorated.setdefault("total_steps", len(task.get("steps", [])))
        decorated["current_attempt"] = int(latest_state.get("current_attempt", 0) or 0)
        decorated["last_backend"] = str(latest_state.get("last_backend", "")).strip()
        decorated["stop_reason"] = str(latest_state.get("stop_reason", "") or task.get("error", "")).strip()
        decorated["artifacts"] = [dict(item) for item in latest_state.get("artifacts", []) if isinstance(item, dict)]
        decorated["pending_approval"] = dict(latest_state.get("pending_approval", {})) if isinstance(latest_state.get("pending_approval"), dict) else {}
        decorated["last_candidates"] = [dict(item) for item in latest_state.get("last_candidates", []) if isinstance(item, dict)]
        return decorated

    def _remember_context(self, task: dict[str, Any]) -> None:
        session_key = str(task.get("session_key", "")).strip()
        if not session_key:
            return
        latest_state = dict(task.get("latest_state", {}))
        artifacts = [dict(item) for item in latest_state.get("artifacts", []) if isinstance(item, dict)]
        last_screenshot = ""
        if artifacts:
            last_screenshot = str(artifacts[-1].get("path", "")).strip()
        elif latest_state.get("capture_path"):
            last_screenshot = str(latest_state.get("capture_path", "")).strip()
        context = DesktopInteractionContext(
            session_key=session_key,
            task_id=str(task.get("task_id", "")).strip(),
            request_text=str(task.get("request_text", "")).strip(),
            summary=str(task.get("summary", "")).strip(),
            route_kind=str(latest_state.get("route_kind", "")).strip() or ("visual" if latest_state.get("last_visual_action") else "structured"),
            active_window=dict(latest_state.get("active_window", {})) if isinstance(latest_state.get("active_window"), dict) else {},
            last_candidates=[dict(item) for item in latest_state.get("last_candidates", []) if isinstance(item, dict)],
            last_screenshot=last_screenshot,
            last_action=dict(latest_state.get("last_visual_action", {})) if isinstance(latest_state.get("last_visual_action"), dict) else {},
            latest_state=latest_state,
            status=str(task.get("status", "")).strip(),
            updated_at=str(task.get("updated_at", "")).strip() or utc_now_iso(),
        )
        self._desktop_contexts[session_key] = context

    def _context_expired(self, context: DesktopInteractionContext) -> bool:
        retention_seconds = max(30, int(getattr(self.config.desktop_coworker, "context_retention_seconds", 300)))
        try:
            updated_at = datetime.fromisoformat(context.updated_at.replace("Z", "+00:00"))
        except ValueError:
            return True
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        return updated_at + timedelta(seconds=retention_seconds) < datetime.now(timezone.utc)

    def _same_window_family(self, left: dict[str, Any], right: dict[str, Any]) -> bool:
        left_process = normalize_target_label(str(left.get("process_name", "")))
        right_process = normalize_target_label(str(right.get("process_name", "")))
        if left_process and right_process:
            return left_process == right_process
        left_title = normalize_target_label(str(left.get("title", "")))
        right_title = normalize_target_label(str(right.get("title", "")))
        if not left_title or not right_title:
            return True
        return left_title == right_title or left_title in right_title or right_title in left_title

    def _resolve_contextual_followup(self, request_text: str, context: dict[str, Any]) -> dict[str, Any] | None:
        if not bool(getattr(self.config.desktop_coworker, "followup_visual_affinity_enabled", True)):
            return None
        if not context:
            return None
        normalized = re.sub(r"\s+", " ", request_text.strip().lower())
        if not normalized:
            return None
        if self._context_mentions_bluetooth(context):
            if normalized in {"turn it off", "switch it off", "disable it"}:
                return {"normalized_request": "turn off bluetooth", "summary": "Turn off Bluetooth using the active settings context."}
            if normalized in {"turn it on", "switch it on", "enable it"}:
                return {"normalized_request": "turn on bluetooth", "summary": "Turn on Bluetooth using the active settings context."}

        candidates = [dict(item) for item in context.get("last_candidates", []) if isinstance(item, dict)]
        candidate = self._resolve_candidate_reference(normalized, candidates)
        if candidate is not None:
            verb = "open"
            if "double click" in normalized or "double-click" in normalized:
                verb = "double click"
            elif normalized.startswith("click") or normalized.startswith("select"):
                verb = "click"
            kind = str(candidate.get("kind", "")).strip().lower()
            suffix = f" {kind}" if kind in {"file", "folder", "button", "tab", "item", "row"} else ""
            return {
                "normalized_request": f"{verb} the visible {candidate.get('label', '').strip()}{suffix}".strip(),
                "summary": f"{verb.title()} the visible target '{candidate.get('label', '').strip()}'.",
            }

        if any(marker in normalized for marker in {"that one", "this one", "click this", "click that", "open this", "open that", "the visible file", "the visible item"}):
            return {
                "normalized_request": request_text.strip(),
                "summary": request_text.strip(),
            }
        return None

    def _resolve_candidate_reference(self, request_text: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not candidates:
            return None
        ordinal_map = {"first": 0, "second": 1, "third": 2}
        for ordinal, index in ordinal_map.items():
            if f"{ordinal} one" in request_text or f"{ordinal} file" in request_text or f"{ordinal} item" in request_text:
                if index < len(candidates):
                    return dict(candidates[index])
        query = re.sub(r"^(?:please\s+)?(?:open|click|select|double click|double-click)\s+", "", request_text)
        query = re.sub(r"\b(?:the|visible|file|folder|button|tab|item|row|one|this|that|it)\b", " ", query)
        compact_query = normalize_target_label(query)
        if not compact_query:
            selected = [item for item in candidates if bool(item.get("selected", False))]
            if len(selected) == 1:
                return dict(selected[0])
            if len(candidates) == 1:
                return dict(candidates[0])
            return None
        matches = []
        for candidate in candidates:
            candidate_label = normalize_target_label(str(candidate.get("label", "")))
            if not candidate_label:
                continue
            if compact_query in candidate_label or candidate_label in compact_query:
                matches.append(candidate)
        if len(matches) == 1:
            return dict(matches[0])
        selected_matches = [item for item in matches if bool(item.get("selected", False))]
        if len(selected_matches) == 1:
            return dict(selected_matches[0])
        return None

    def _context_mentions_bluetooth(self, context: dict[str, Any]) -> bool:
        latest_state = dict(context.get("latest_state", {}))
        haystack = " ".join(
            [
                str(context.get("summary", "")),
                str(context.get("request_text", "")),
                str(latest_state.get("screen_text", "")),
                str((context.get("active_window", {}) or {}).get("title", "")),
            ]
        )
        return "bluetooth" in haystack.lower()
