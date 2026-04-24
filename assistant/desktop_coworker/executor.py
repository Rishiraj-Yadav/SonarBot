"""Verified executor for bounded desktop coworker tasks."""

from __future__ import annotations

from typing import Any

from assistant.desktop_coworker.recovery import DesktopCoworkerRecovery
from assistant.desktop_coworker.state import DesktopCoworkerStateCollector
from assistant.desktop_coworker.visual_controller import DesktopCoworkerVisualController


class DesktopCoworkerExecutor:
    def __init__(self, config, tool_registry) -> None:
        self.config = config
        self.tool_registry = tool_registry
        self.state = DesktopCoworkerStateCollector(config, tool_registry)
        self.recovery = DesktopCoworkerRecovery(config)
        self.visual = DesktopCoworkerVisualController(config, tool_registry)

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
        if str(step.get("type", "")).strip().lower() == "visual_task":
            result = await self.visual.run_visual_task(
                goal=str(step.get("payload", {}).get("goal") or task.get("request_text", "")),
                task=task,
                session_key=session_key,
                user_id=user_id,
                connection_id=connection_id,
                channel_name=channel_name,
            )
            return {
                "step_index": current_index,
                "step_type": step.get("type", ""),
                "title": step.get("title", ""),
                "status": str(result.get("status", "failed")),
                "attempts": len(result.get("substeps", [])) or 1,
                "verification": dict(result.get("verification", {})),
                "tool_result": dict(result.get("tool_result", {})),
                "state_before": dict(result.get("state_before", {})),
                "state_after": dict(result.get("state_after", {})),
                "summary": str(result.get("summary", "")).strip() or "Completed the visual coworker task.",
                "visual_substeps": list(result.get("substeps", [])),
                "latest_state": dict(result.get("latest_state", {})),
                "last_backend": str(result.get("latest_state", {}).get("last_backend", "")),
                "current_attempt": int(result.get("latest_state", {}).get("current_attempt", 0) or 0),
                "stop_reason": str(result.get("latest_state", {}).get("stop_reason", "")),
                "artifacts": list(result.get("latest_state", {}).get("artifacts", [])),
                "last_candidates": list(result.get("latest_state", {}).get("last_candidates", [])),
            }
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
            include_ocr = self._should_collect_post_ocr(step)
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

    def _should_collect_post_ocr(self, step: dict[str, Any]) -> bool:
        if not bool(getattr(self.config.desktop_coworker, "ocr_after_each_step", False)):
            return False
        payload = dict(step.get("payload", {}))
        verification = dict(step.get("verification", {}))
        if bool(payload.get("force_post_ocr")) or bool(verification.get("requires_screen_text")):
            return True
        kind = str(verification.get("kind", "tool_status")).strip().lower()
        if kind in {
            "tool_status",
            "active_window_contains",
            "document_contains",
            "clipboard_nonempty",
            "summary_has_keys",
            "bluetooth_state",
            "volume_state",
            "brightness_state",
        }:
            return False
        return True

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
        if step_type == "system_bluetooth_set":
            result = await self.tool_registry.dispatch("system_bluetooth_set", {**payload, **context})
            requested_state = str(payload.get("mode", "")).strip().lower()
            succeeded = (
                str(result.get("status", "")).strip().lower() == "completed"
                and str(result.get("radio_state_after", "")).strip().lower() == requested_state
            )
            should_fallback = bool(payload.get("fallback_visual", False)) and not succeeded
            if should_fallback:
                if bool(payload.get("open_settings_on_fallback", False)):
                    await self.tool_registry.dispatch("system_open_settings", {"page": str(payload.get("open_settings_page", "bluetooth")), **context})
                visual_result = await self.visual.run_visual_task(
                    goal=str(payload.get("goal") or f"Turn Bluetooth {requested_state}."),
                    task=task,
                    session_key=session_key,
                    user_id=user_id,
                    connection_id=connection_id,
                    channel_name=channel_name,
                )
                return {
                    "status": "completed" if str(visual_result.get("status", "failed")) == "completed" else "failed",
                    "requested_state": requested_state,
                    "fallback_visual_used": True,
                    "direct_result": result,
                    "visual_result": visual_result,
                    "radio_state_after": str(visual_result.get("state_after", {}).get("screen_text", "")),
                    "message": str(visual_result.get("summary", "")).strip(),
                }
            return result
        if step_type == "system_volume_set":
            return await self.tool_registry.dispatch("system_volume_set", {**payload, **context})
        if step_type == "system_brightness_set":
            return await self.tool_registry.dispatch("system_brightness_set", {**payload, **context})
        if step_type == "apps_open":
            return await self.tool_registry.dispatch("apps_open", {**payload})
        if step_type == "apps_focus":
            return await self.tool_registry.dispatch("apps_focus", {**payload})
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
        if step_type == "host_file_move":
            return await self._dispatch_host_file_move(step=step, session_key=session_key, user_id=user_id, connection_id=connection_id)
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
        if kind == "bluetooth_state":
            expected_state = str(verification.get("state", "")).strip().lower()
            visual_result = tool_result.get("visual_result", {})
            if isinstance(visual_result, dict) and str(visual_result.get("status", "")).strip().lower() == "completed":
                return {"ok": True, "kind": kind, "message": ""}
            after_state = str(tool_result.get("radio_state_after", "")).strip().lower()
            ok = bool(expected_state and after_state == expected_state)
            return {
                "ok": ok,
                "kind": kind,
                "message": "" if ok else f"Bluetooth did not reach the requested '{expected_state}' state.",
            }
        if kind == "volume_state":
            expected_percent = int(verification.get("percent", 0) or 0)
            actual_percent = int(tool_result.get("volume_percent", -1) or -1)
            ok = actual_percent == expected_percent
            return {
                "ok": ok,
                "kind": kind,
                "message": "" if ok else f"Volume remained at {actual_percent}% instead of {expected_percent}%.",
            }
        if kind == "brightness_state":
            if not bool(tool_result.get("supported", True)):
                return {"ok": False, "kind": kind, "message": str(tool_result.get("message", "Direct brightness control is unavailable."))}
            expected_percent = int(verification.get("percent", 0) or 0)
            actual_percent = int(tool_result.get("brightness_percent", -1) or -1)
            ok = actual_percent == expected_percent
            return {
                "ok": ok,
                "kind": kind,
                "message": "" if ok else f"Brightness remained at {actual_percent}% instead of {expected_percent}%.",
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
        visual_result = tool_result.get("visual_result")
        if isinstance(visual_result, dict):
            visual_latest_state = visual_result.get("latest_state", {})
            visual_state_after = visual_result.get("state_after", {})
            if isinstance(visual_state_after, dict):
                merged.update(visual_state_after)
            if isinstance(visual_latest_state, dict):
                merged.update(visual_latest_state)
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
        if step_type == "system_bluetooth_set":
            if bool(tool_result.get("fallback_visual_used")):
                return f"{title}: completed through the Settings fallback."
            return f"{title}: Bluetooth is now {tool_result.get('radio_state_after', 'unknown')}."
        if step_type == "system_volume_set":
            return f"{title}: Volume is now {tool_result.get('volume_percent', 0)}%."
        if step_type == "system_brightness_set":
            if not bool(tool_result.get("supported", True)):
                return f"{title}: {tool_result.get('message', 'brightness control is unavailable')}"
            return f"{title}: Brightness is now {tool_result.get('brightness_percent', 0)}%."
        if step_type == "apps_open":
            return f"{title}: opened {tool_result.get('alias', tool_result.get('target', 'the app'))}."
        if step_type == "apps_focus":
            return f"{title}: focused {tool_result.get('target', step.get('payload', {}).get('target', 'the app'))}."
        if step_type == "document_read":
            return f"{title}: Read {tool_result.get('path', 'the document')}."
        if step_type == "document_replace_text":
            return f"{title}: Applied {tool_result.get('replacements', 0)} replacement(s)."
        if step_type == "llm_summarize_text":
            return f"{title}: Summary generated."
        if step_type == "host_file_move":
            moved = tool_result.get("moved_count", tool_result.get("count", 0))
            return f"{title}: {moved} file(s) processed successfully."
        return f"{title}: completed."

    async def _dispatch_host_file_move(
        self,
        *,
        step: dict[str, Any],
        session_key: str,
        user_id: str,
        connection_id: str,
    ) -> dict[str, Any]:
        """Move or copy files from src to dst using host file tools directly.

        Actual registered tools:
          - list_host_dir  (params: path, limit)
          - move_host_file (params: source, destination)
          - copy_host_file (params: source, destination)

        This avoids opening File Explorer or any GUI, which prevents
        window-matching verification failures when another window is in focus.
        """
        payload = dict(step.get("payload", {}))
        src = str(payload.get("src", "")).strip()
        dst = str(payload.get("dst", "")).strip()
        operation = str(payload.get("operation", "move")).strip().lower()
        tool_name = "copy_host_file" if operation == "copy" else "move_host_file"

        if not src or not dst:
            return {"status": "failed", "error": "Source or destination path is missing.", "moved_count": 0}

        # Common context fields expected by host tools
        context = {
            "session_key": session_key,
            "session_id": session_key,
            "user_id": user_id,
            "connection_id": connection_id,
            "channel_name": "",
        }

        # Step 1: list the source directory
        try:
            list_result = await self.tool_registry.dispatch(
                "list_host_dir", {"path": src, "limit": 200, **context}
            )
        except Exception as exc:
            return {"status": "failed", "error": f"Could not list source directory '{src}': {exc}", "moved_count": 0}

        entries = list_result.get("entries", []) if isinstance(list_result, dict) else []
        # Filter to files only (entries whose type is not a directory)
        files = [
            entry for entry in entries
            if isinstance(entry, dict)
            and str(entry.get("type", "file")).lower() not in {"directory", "dir", "d"}
        ]

        if not files:
            return {"status": "completed", "message": f"No files found in '{src}'.", "moved_count": 0}

        # Sort by modification time desc so that the most-recent file comes first
        def _sort_key(e: dict[str, Any]) -> str:
            return str(e.get("modified", e.get("mtime", e.get("name", ""))))

        files_sorted = sorted(files, key=_sort_key, reverse=True)

        # If the user asked to move "folder contents" / "files" move all; otherwise just the latest
        src_label = str(payload.get("src_label", "")).lower()
        move_all = any(kw in src_label for kw in ("contents", "files", "all"))
        targets = files_sorted if move_all else files_sorted[:1]

        import os
        moved_count = 0
        errors: list[str] = []
        for entry in targets:
            filename = str(entry.get("name", "")).strip()
            if not filename:
                continue
            src_path = os.path.join(src, filename)
            dst_path = os.path.join(dst, filename)
            try:
                await self.tool_registry.dispatch(
                    tool_name,
                    {"source": src_path, "destination": dst_path, **context},
                )
                moved_count += 1
            except Exception as exc:
                errors.append(f"{filename}: {exc}")

        if moved_count == 0 and errors:
            return {"status": "failed", "error": "; ".join(errors), "moved_count": 0}
        return {
            "status": "completed",
            "moved_count": moved_count,
            "errors": errors,
            "src": src,
            "dst": dst,
            "operation": operation,
        }
