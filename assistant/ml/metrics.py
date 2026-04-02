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


@dataclass(slots=True)
class _MemoryClassifierStats:
    decisions: int = 0
    kept: int = 0
    dropped: int = 0
    confidence_samples: deque[float] = field(default_factory=lambda: deque(maxlen=200))
    last_reason: str = ""


class MLMetricsTracker:
    """Tracks lightweight ML runtime stats and appends JSONL snapshots."""

    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._tool_router = _ToolRouterStats()
        self._memory_classifier = _MemoryClassifierStats()

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
        stats = self._combine_tool_router_stats(self._tool_router, self._load_persisted_tool_router_stats())
        memory_stats = self._combine_memory_classifier_stats(
            self._memory_classifier,
            self._load_persisted_memory_classifier_stats(),
        )
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
            },
            "memory_classifier": {
                "decisions": memory_stats.decisions,
                "kept": memory_stats.kept,
                "dropped": memory_stats.dropped,
                "keep_rate": memory_stats.kept / max(1, memory_stats.decisions),
                "avg_confidence": (
                    sum(memory_stats.confidence_samples) / len(memory_stats.confidence_samples)
                    if memory_stats.confidence_samples
                    else 0.0
                ),
                "last_reason": memory_stats.last_reason,
            },
        }

    def record_memory_classifier(
        self,
        *,
        keep: bool,
        confidence: float,
        reason: str = "",
    ) -> None:
        stats = self._memory_classifier
        stats.decisions += 1
        stats.confidence_samples.append(max(0.0, min(1.0, float(confidence))))
        if keep:
            stats.kept += 1
        else:
            stats.dropped += 1
        if reason:
            stats.last_reason = reason
        self._append_event(
            {
                "ts": _utc_now_iso(),
                "component": "memory_classifier",
                "keep": keep,
                "confidence": confidence,
                "reason": reason,
            }
        )

    def _append_event(self, payload: dict[str, Any]) -> None:
        try:
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except OSError:
            return

    def _load_persisted_tool_router_stats(self) -> _ToolRouterStats:
        stats = _ToolRouterStats()
        if not self.log_path.exists():
            return stats
        try:
            with self.log_path.open("r", encoding="utf-8") as handle:
                for raw_line in handle:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if str(payload.get("component", "")).strip() != "tool_router":
                        continue
                    stats.requests += 1
                    stats.tool_total_available += max(0, int(payload.get("tools_available", 0) or 0))
                    stats.tool_total_sent += max(0, int(payload.get("tools_selected", 0) or 0))
                    stats.confidence_samples.append(max(0.0, min(1.0, float(payload.get("confidence", 0.0) or 0.0))))
                    stats.latency_samples_ms.append(max(0.0, float(payload.get("latency_ms", 0.0) or 0.0)))
                    if bool(payload.get("fallback_used")):
                        stats.fallbacks += 1
                    reason = str(payload.get("reason", "")).strip()
                    if reason:
                        stats.last_error = reason
        except OSError:
            return _ToolRouterStats()
        return stats

    def _load_persisted_memory_classifier_stats(self) -> _MemoryClassifierStats:
        stats = _MemoryClassifierStats()
        if not self.log_path.exists():
            return stats
        try:
            with self.log_path.open("r", encoding="utf-8") as handle:
                for raw_line in handle:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if str(payload.get("component", "")).strip() != "memory_classifier":
                        continue
                    stats.decisions += 1
                    keep = bool(payload.get("keep"))
                    if keep:
                        stats.kept += 1
                    else:
                        stats.dropped += 1
                    stats.confidence_samples.append(max(0.0, min(1.0, float(payload.get("confidence", 0.0) or 0.0))))
                    reason = str(payload.get("reason", "")).strip()
                    if reason:
                        stats.last_reason = reason
        except OSError:
            return _MemoryClassifierStats()
        return stats

    def _combine_tool_router_stats(self, primary: _ToolRouterStats, secondary: _ToolRouterStats) -> _ToolRouterStats:
        if primary.requests > 0:
            return primary
        combined = _ToolRouterStats()
        combined.requests = secondary.requests
        combined.fallbacks = secondary.fallbacks
        combined.tool_total_sent = secondary.tool_total_sent
        combined.tool_total_available = secondary.tool_total_available
        combined.last_error = secondary.last_error
        combined.confidence_samples.extend(secondary.confidence_samples)
        combined.latency_samples_ms.extend(secondary.latency_samples_ms)
        return combined

    def _combine_memory_classifier_stats(
        self,
        primary: _MemoryClassifierStats,
        secondary: _MemoryClassifierStats,
    ) -> _MemoryClassifierStats:
        if primary.decisions > 0:
            return primary
        combined = _MemoryClassifierStats()
        combined.decisions = secondary.decisions
        combined.kept = secondary.kept
        combined.dropped = secondary.dropped
        combined.last_reason = secondary.last_reason
        combined.confidence_samples.extend(secondary.confidence_samples)
        return combined
