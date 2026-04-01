"""Simple in-process metrics tracker for ML components."""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class _ToolRouterStats:
    requests: int = 0
    fallbacks: int = 0
    tool_total_sent: int = 0
    tool_total_available: int = 0
    confidence_samples: deque[float] = field(default_factory=lambda: deque(maxlen=200))
    latency_samples_ms: deque[float] = field(default_factory=lambda: deque(maxlen=200))
    last_error: str = ""


class MLMetricsTracker:
    """Tracks lightweight ML runtime stats and appends JSONL snapshots."""

    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._tool_router = _ToolRouterStats()

    def record_tool_router(
        self,
        *,
        tools_available: int,
        tools_selected: int,
        confidence: float,
        fallback_used: bool,
        latency_ms: float,
        reason: str = "",
    ) -> None:
        stats = self._tool_router
        stats.requests += 1
        stats.tool_total_available += max(0, int(tools_available))
        stats.tool_total_sent += max(0, int(tools_selected))
        stats.confidence_samples.append(max(0.0, min(1.0, float(confidence))))
        stats.latency_samples_ms.append(max(0.0, float(latency_ms)))
        if fallback_used:
            stats.fallbacks += 1
        if reason:
            stats.last_error = reason
        self._append_event(
            {
                "ts": _utc_now_iso(),
                "component": "tool_router",
                "tools_available": tools_available,
                "tools_selected": tools_selected,
                "confidence": confidence,
                "fallback_used": fallback_used,
                "latency_ms": latency_ms,
                "reason": reason,
            }
        )

    def snapshot(self) -> dict[str, Any]:
        stats = self._tool_router
        requests = max(1, stats.requests)
        avg_confidence = sum(stats.confidence_samples) / len(stats.confidence_samples) if stats.confidence_samples else 0.0
        avg_latency = sum(stats.latency_samples_ms) / len(stats.latency_samples_ms) if stats.latency_samples_ms else 0.0
        total_saved = max(0, stats.tool_total_available - stats.tool_total_sent)
        return {
            "tool_router": {
                "requests": stats.requests,
                "fallbacks": stats.fallbacks,
                "fallback_rate": stats.fallbacks / requests,
                "avg_confidence": avg_confidence,
                "avg_latency_ms": avg_latency,
                "tools_available_total": stats.tool_total_available,
                "tools_selected_total": stats.tool_total_sent,
                "tools_saved_total": total_saved,
                "last_error": stats.last_error,
            }
        }

    def _append_event(self, payload: dict[str, Any]) -> None:
        try:
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except OSError:
            return

