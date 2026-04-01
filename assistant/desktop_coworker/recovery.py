"""Bounded retry policy for Phase 6 coworker execution."""

from __future__ import annotations


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
