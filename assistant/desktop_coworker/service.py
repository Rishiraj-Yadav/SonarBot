"""Phase 6 coworker service for bounded verified desktop tasks."""

from __future__ import annotations

from typing import Any

from assistant.desktop_coworker.executor import DesktopCoworkerExecutor
from assistant.desktop_coworker.models import DesktopCoworkerTask, utc_now_iso
from assistant.desktop_coworker.planner import DesktopCoworkerPlanner
from assistant.desktop_coworker.store import DesktopCoworkerStore
from assistant.desktop_coworker.visual_controller import DesktopCoworkerVisualController


class DesktopCoworkerService:
    def __init__(self, config, tool_registry) -> None:
        self.config = config
        self.tool_registry = tool_registry
        self.store = DesktopCoworkerStore(config)
        self.planner = DesktopCoworkerPlanner(config, tool_registry)
        self.visual = DesktopCoworkerVisualController(config, tool_registry)
        self.executor = DesktopCoworkerExecutor(config, tool_registry)

    async def initialize(self) -> None:
        await self.store.initialize()

    def ensure_enabled(self) -> None:
        if not bool(getattr(self.config.desktop_coworker, "enabled", False)):
            raise RuntimeError("Desktop coworker is not enabled.")

    async def can_handle_request(self, request_text: str) -> bool:
        if not bool(getattr(self.config.desktop_coworker, "enabled", False)):
            return False
        try:
            return self.planner.can_handle(request_text) or await self.visual.can_handle(request_text)
        except Exception:
            return False

    async def plan_task(self, *, user_id: str, session_key: str, request_text: str) -> dict[str, Any]:
        self.ensure_enabled()
        plan = self.planner.plan(request_text)
        if plan is None:
            plan = await self.visual.build_plan(request_text)
        if plan is None:
            raise ValueError("I couldn't turn that into a bounded coworker task yet. Try /coworker help for supported requests.")
        task = DesktopCoworkerTask.new(
            user_id=user_id,
            session_key=session_key,
            request_text=request_text,
            summary=str(plan["summary"]),
            steps=list(plan["steps"]),
            status="planned",
        )
        await self.store.create_task(task)
        stored = await self.store.get_task(task.task_id) or {}
        return self._decorate_task(stored)

    async def run_task_request(
        self,
        *,
        user_id: str,
        session_key: str,
        request_text: str,
        connection_id: str = "",
        channel_name: str = "",
    ) -> dict[str, Any]:
        task = await self.plan_task(user_id=user_id, session_key=session_key, request_text=request_text)
        return await self.run_task(
            user_id=user_id,
            task_id=str(task["task_id"]),
            connection_id=connection_id,
            channel_name=channel_name,
        )

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
        return self._decorate_task(task)

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
        return self._decorate_task(updated or task)

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
        return await self.step_task(
            user_id=user_id,
            task_id=task_id,
            connection_id=connection_id,
            channel_name=channel_name,
        )

    async def stop_task(self, *, user_id: str, task_id: str) -> dict[str, Any]:
        self.ensure_enabled()
        task = await self._require_task(user_id, task_id)
        if str(task.get("status")) == "completed":
            return self._decorate_task(task)
        stopped = await self.store.stop_task(task_id)
        return self._decorate_task(stopped or task)

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
