"""Bounded retry policy for Phase 6 coworker execution."""

from __future__ import annotations

from typing import Any

from assistant.desktop_coworker.adapters import keyboard_fallback_recipe


class DesktopCoworkerRecovery:
    def __init__(self, config) -> None:
        self.config = config

    def should_retry(self, step: dict[str, object], *, attempts_used: int, verification_failed: bool) -> bool:
        if not verification_failed:
            return False
        if not bool(step.get("retryable", True)):
            return False
        max_retries = max(0, int(getattr(self.config.desktop_coworker, "max_retries_per_step", 2)))
        return attempts_used < max_retries

    def should_retry_visual(self, *, attempts_used: int, verification_failed: bool) -> bool:
        if not verification_failed:
            return False
        max_retries = max(0, int(getattr(self.config.desktop_coworker, "max_recovery_attempts", 3)))
        return attempts_used < max_retries

    def refine_action(
        self,
        *,
        action: dict[str, Any],
        before: dict[str, Any],
        after: dict[str, Any],
        attempts_used: int,
    ) -> dict[str, Any] | None:
        if bool(getattr(self.config.desktop_coworker, "keyboard_fallback_enabled", True)):
            keyboard_recipe = keyboard_fallback_recipe(
                action=action,
                before=before,
                after=after,
                attempts_used=attempts_used,
            )
            if keyboard_recipe is not None:
                return keyboard_recipe

        action_type = str(action.get("type", "")).strip().lower()
        if action_type not in {"click", "double_click"}:
            return None
        if attempts_used >= max(0, int(getattr(self.config.desktop_coworker, "max_visual_replans", 2))):
            return None
        offsets = ((0, 0), (16, 0), (-16, 0), (0, 16), (0, -16))
        offset_x, offset_y = offsets[min(attempts_used + 1, len(offsets) - 1)]
        refined = dict(action)
        refined["x"] = max(0, min(1000, int(refined.get("x", 500)) + offset_x))
        refined["y"] = max(0, min(1000, int(refined.get("y", 500)) + offset_y))
        refined["reason"] = (
            str(refined.get("reason", "")).strip()
            or "Retry the same target with a refined click position."
        )
        refined["recovery_strategy"] = "refined_click"
        return refined
