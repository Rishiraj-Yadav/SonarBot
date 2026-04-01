"""Verified executor for bounded desktop coworker tasks."""

from __future__ import annotations

from typing import Any

from assistant.desktop_coworker.recovery import DesktopCoworkerRecovery
from assistant.desktop_coworker.state import DesktopCoworkerStateCollector


class DesktopCoworkerExecutor:
    def __init__(self, config, tool_registry) -> None:
        self.config = config
        self.tool_registry = tool_registry
        self.state = DesktopCoworkerStateCollector(config, tool_registry)
        self.recovery = DesktopCoworkerRecovery(config)

    async def execute_next_step(
        self,
        *,
        task: dict[str, Any],
        session_key: str,
        user_id: str,
        connection_id: str = "",
        channel_name: str = "",
    ) -> dict[str, Any]:
        steps = list(task.get("steps", []))
        current_index = int(task.get("current_step_index", 0))
        if current_index >= len(steps):
            raise RuntimeError("This coworker task has no remaining steps to run.")
        step = dict(steps[current_index])
        attempts = 0
        last_result: dict[str, Any] | None = None
        verification_failed = False
        latest_state = dict(task.get("latest_state", {}))

        while True:
            pre_state = await self.state.capture(
                include_capture=False,
                include_ocr=False,
                include_clipboard=bool(step.get("type") == "desktop_clipboard_read"),
            )
            tool_result = await self._dispatch_step(
                step=step,
                task=task,
                session_key=session_key,
                user_id=user_id,
                connection_id=connection_id,
                channel_name=channel_name,
            )
            include_ocr = bool(getattr(self.config.desktop_coworker, "ocr_after_each_step", False))
            include_capture = bool(getattr(self.config.desktop_coworker, "screenshot_after_each_step", True))
            post_state = await self.state.capture(
                include_capture=include_capture,
                include_ocr=include_ocr,
                include_clipboard=step.get("type") in {"desktop_clipboard_read", "desktop_keyboard_hotkey"},
            )
            verification = self._verify_step(step=step, tool_result=tool_result, state_before=pre_state, state_after=post_state)
            verification_failed = not bool(verification.get("ok", False))
            latest_state = self._merge_latest_state(task.get("latest_state", {}), post_state, tool_result)
            last_result = {
                "step_index": current_index,
                "step_type": step.get("type", ""),
                "title": step.get("title", ""),
                "status": "completed" if not verification_failed else "failed",
                "attempts": attempts + 1,
                "verification": verification,
                "tool_result": tool_result,
                "state_before": pre_state,
                "state_after": post_state,
                "summary": self._summarize_step(step, tool_result, verification),
            }
            if not verification_failed:
                break
            if not self.recovery.should_retry(step, attempts_used=attempts, verification_failed=True):
                break
            attempts += 1

        assert last_result is not None
        return {**last_result, "latest_state": latest_state}

    async def _dispatch_step(
        self,
        *,
        step: dict[str, Any],
        task: dict[str, Any],
        session_key: str,
        user_id: str,
        connection_id: str,
        channel_name: str,
    ) -> dict[str, Any]:
        step_type = str(step.get("type", "")).strip().lower()
        payload = dict(step.get("payload", {}))
        context = {
            "session_key": session_key,
            "session_id": str(task.get("task_id", session_key)),
            "user_id": user_id,
            "connection_id": connection_id,
            "channel_name": channel_name,
        }

        if step_type == "task_manager_open":
            return await self.tool_registry.dispatch("task_manager_open", {})
        if step_type == "task_manager_summary":
            return await self.tool_registry.dispatch("task_manager_summary", {})
        if step_type == "system_open_settings":
            return await self.tool_registry.dispatch("system_open_settings", {**payload, **context})
        if step_type == "system_bluetooth_status":
            return await self.tool_registry.dispatch("system_bluetooth_status", {})
        if step_type == "vscode_open_target":
            return await self.tool_registry.dispatch("vscode_open_target", {**payload, **context})
        if step_type == "document_read":
            return await self.tool_registry.dispatch("document_read", {**payload, **context})
        if step_type == "document_replace_text":
            return await self.tool_registry.dispatch("document_replace_text", {**payload, **context})
        if step_type == "preset_run":
            return await self.tool_registry.dispatch("preset_run", {**payload, "user_id": user_id})
        if step_type == "desktop_keyboard_hotkey":
            return await self.tool_registry.dispatch("desktop_keyboard_hotkey", {**payload, **context})
        if step_type == "desktop_clipboard_read":
            return await self.tool_registry.dispatch("desktop_clipboard_read", {})
        if step_type == "llm_summarize_text":
            clipboard_text = str(task.get("latest_state", {}).get("clipboard_text", "")).strip()
            if not clipboard_text:
                raise RuntimeError("There is no clipboard text available to summarize yet.")
            instruction = str(payload.get("instruction", "Summarize this text briefly.")).strip()
            result = await self.tool_registry.dispatch(
                "llm_task",
                {
                    "prompt": f"{instruction}\n\nText:\n{clipboard_text}",
                    "model": "cheap",
                },
            )
            return {"status": "completed", "content": str(result.get("content", "")).strip()}
        raise RuntimeError(f"Unsupported coworker step '{step_type}'.")

    def _verify_step(
        self,
        *,
        step: dict[str, Any],
        tool_result: dict[str, Any],
        state_before: dict[str, Any],
        state_after: dict[str, Any],
    ) -> dict[str, Any]:
        verification = dict(step.get("verification", {}))
        kind = str(verification.get("kind", "tool_status")).strip().lower()
        if kind == "tool_status":
            status = str(tool_result.get("status", "completed")).lower()
            ok = status not in {"failed", "rejected", "expired", "blocked", "blocked_by_window_guard", "no_change"}
            return {"ok": ok, "kind": kind, "message": "" if ok else f"Tool returned status '{status}'."}
        if kind == "active_window_contains":
            active_window = state_after.get("active_window", {})
            haystack = " ".join(
                [
                    str(active_window.get("title", "")),
                    str(active_window.get("process_name", "")),
                ]
            ).lower()
            matches = [str(item).lower() for item in verification.get("matches", [])]
            ok = any(item in haystack for item in matches) if matches else bool(haystack)
            return {
                "ok": ok,
                "kind": kind,
                "message": "" if ok else f"Active window '{haystack or 'unknown'}' did not match {matches}.",
            }
        if kind == "document_contains":
            content = str(tool_result.get("content", ""))
            contains = str(verification.get("contains", ""))
            not_contains = str(verification.get("not_contains", ""))
            ok = True
            if contains:
                ok = contains in content
            if ok and not_contains:
                ok = not_contains not in content
            return {
                "ok": ok,
                "kind": kind,
                "message": "" if ok else "Document verification did not match the expected text conditions.",
            }
        if kind == "clipboard_nonempty":
            content = str(tool_result.get("content", "") or state_after.get("clipboard_text", ""))
            ok = bool(content.strip())
            return {"ok": ok, "kind": kind, "message": "" if ok else "Clipboard is empty after the copy step."}
        if kind == "summary_has_keys":
            source = tool_result.get("summary", tool_result)
            keys = [str(item) for item in verification.get("keys", [])]
            ok = isinstance(source, dict) and all(key in source for key in keys)
            return {
                "ok": ok,
                "kind": kind,
                "message": "" if ok else f"Expected summary keys {keys} were not present.",
            }
        return {"ok": True, "kind": kind, "message": ""}

    def _merge_latest_state(
        self,
        existing_state: dict[str, Any] | None,
        state_after: dict[str, Any],
        tool_result: dict[str, Any],
    ) -> dict[str, Any]:
        merged = dict(existing_state or {})
        merged.update(state_after)
        if "content" in tool_result and isinstance(tool_result.get("content"), str):
            if tool_result.get("path"):
                merged["last_document_content"] = str(tool_result.get("content", ""))
            else:
                merged["last_summary_text"] = str(tool_result.get("content", ""))
        if "summary" in tool_result:
            merged["last_summary"] = tool_result.get("summary")
        if state_after.get("clipboard_text"):
            merged["clipboard_text"] = str(state_after["clipboard_text"])
        return merged

    def _summarize_step(self, step: dict[str, Any], tool_result: dict[str, Any], verification: dict[str, Any]) -> str:
        title = str(step.get("title", step.get("type", "step")))
        if not verification.get("ok", False):
            return f"{title}: {verification.get('message', 'verification failed')}"
        step_type = str(step.get("type", "")).strip().lower()
        if step_type == "task_manager_summary":
            summary = tool_result
            return f"{title}: CPU {summary.get('cpu_percent', 0)}%, memory and disk summary captured."
        if step_type == "system_bluetooth_status":
            return f"{title}: Bluetooth availability checked."
        if step_type == "document_read":
            return f"{title}: Read {tool_result.get('path', 'the document')}."
        if step_type == "document_replace_text":
            return f"{title}: Applied {tool_result.get('replacements', 0)} replacement(s)."
        if step_type == "llm_summarize_text":
            return f"{title}: Summary generated."
        return f"{title}: completed."
