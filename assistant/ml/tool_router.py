"""CPU-friendly tool schema router with safe fallback behavior."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:  # Optional dependency
    import joblib  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    joblib = None


@dataclass(slots=True)
class ToolRouterDecision:
    selected_tool_names: list[str]
    confidence: float
    fallback_used: bool
    reason: str = ""
    latency_ms: float = 0.0


class ToolRouter:
    """Selects a subset of tools for a turn, while preserving critical tools."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        shadow_mode: bool = True,
        min_confidence: float = 0.45,
        model_path: Path | None = None,
        safety_tools: list[str] | None = None,
    ) -> None:
        self.enabled = enabled
        self.shadow_mode = shadow_mode
        self.min_confidence = max(0.0, min(1.0, min_confidence))
        self.model_path = model_path
        self.safety_tools = set(
            safety_tools
            or [
                "llm_task",
                "read_file",
                "write_file",
                "list_files",
                "browser_navigate",
                "browser_click",
                "browser_type",
            ]
        )
        self._model: Any = None
        self._labels: list[str] = []
        self._model_error = ""
        self._load_model_if_possible()

    def status(self) -> dict[str, Any]:
        self._reload_if_available()
        metadata = self._artifact_metadata()
        return {
            "enabled": self.enabled,
            "shadow_mode": self.shadow_mode,
            "min_confidence": self.min_confidence,
            "model_path": str(self.model_path) if self.model_path else "",
            "model_loaded": self._model is not None,
            "model_error": self._model_error,
            "label_count": len(self._labels),
            "safety_tool_count": len(self.safety_tools),
            **metadata,
        }

    def select_tools(self, message: str, tool_schemas: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], ToolRouterDecision]:
        self._reload_if_available()
        start = time.perf_counter()
        if not self.enabled:
            decision = ToolRouterDecision(
                selected_tool_names=[str(item.get("name", "")) for item in tool_schemas],
                confidence=1.0,
                fallback_used=True,
                reason="disabled",
            )
            decision.latency_ms = (time.perf_counter() - start) * 1000.0
            return tool_schemas, decision

        all_names = [str(item.get("name", "")).strip() for item in tool_schemas if str(item.get("name", "")).strip()]
        predicted, confidence, reason = self._predict_names(message, all_names)
        selected_names = self._merge_with_safety(predicted, all_names)
        fallback_used = confidence < self.min_confidence or not selected_names
        if fallback_used:
            selected_names = all_names

        selected_schemas = [item for item in tool_schemas if str(item.get("name", "")).strip() in set(selected_names)]
        if self.shadow_mode:
            selected_schemas = tool_schemas

        decision = ToolRouterDecision(
            selected_tool_names=selected_names,
            confidence=confidence,
            fallback_used=fallback_used,
            reason=reason,
        )
        decision.latency_ms = (time.perf_counter() - start) * 1000.0
        return selected_schemas, decision

    def _predict_names(self, message: str, all_names: list[str]) -> tuple[list[str], float, str]:
        normalized = re.sub(r"\s+", " ", message).strip().lower()
        if not normalized:
            return [], 0.0, "empty_message"

        if self._model is not None:
            try:
                model_out = self._model.predict([normalized])  # type: ignore[call-arg]
                names = self._normalize_model_output(model_out, all_names, self._labels)
                confidence = 0.85 if names else 0.2
                return names, confidence, "model"
            except Exception as exc:
                self._model_error = str(exc)

        # Heuristic fallback (CPU-only, no dependencies).
        hints: dict[str, tuple[str, ...]] = {
            "read_file": ("read ", "open file", "show file", ".txt", ".md", ".py"),
            "write_file": ("write ", "create file", "save file", "append"),
            "list_files": ("list files", "show files", "directory", "folder"),
            "search_web": ("search ", "web", "google", "lookup"),
            "gmail_latest_email": ("latest email", "last email", "check email"),
            "github_list_repos": ("repo count", "repositories", "github repos"),
            "browser_navigate": ("open website", "open site", "go to ", "browser"),
            "browser_click": ("click", "open first result"),
            "browser_type": ("type ", "fill "),
        }
        matched: list[str] = []
        for tool_name, tokens in hints.items():
            if tool_name not in all_names:
                continue
            if any(token in normalized for token in tokens):
                matched.append(tool_name)
        confidence = 0.75 if matched else 0.2
        return matched, confidence, "heuristic"

    def _merge_with_safety(self, predicted: list[str], all_names: list[str]) -> list[str]:
        names = {item for item in predicted if item in all_names}
        names.update(item for item in self.safety_tools if item in all_names)
        return sorted(names)

    def _normalize_model_output(self, model_out: Any, all_names: list[str], labels: list[str]) -> list[str]:
        if model_out is None:
            return []
        if labels:
            try:
                row = list(model_out[0])  # type: ignore[index]
                names = [labels[index] for index, flag in enumerate(row) if int(flag) == 1 and index < len(labels)]
                return [name for name in names if name in all_names]
            except Exception:
                pass
        if isinstance(model_out, list):
            flat = model_out[0] if model_out and isinstance(model_out[0], (list, tuple, set)) else model_out
            return [str(item).strip() for item in flat if str(item).strip() in all_names]
        try:
            values = list(model_out[0])  # type: ignore[index]
            return [str(item).strip() for item in values if str(item).strip() in all_names]
        except Exception:
            return []

    def _load_model_if_possible(self) -> None:
        if self.model_path is None:
            return
        if joblib is None:
            self._model_error = "joblib_not_installed"
            return
        if not self.model_path.exists():
            self._model_error = "model_not_found"
            return
        try:
            artifact = joblib.load(self.model_path)
            if isinstance(artifact, dict) and "pipeline" in artifact:
                self._model = artifact.get("pipeline")
                raw_labels = artifact.get("labels", [])
                self._labels = [str(item).strip() for item in raw_labels if str(item).strip()]
            else:
                self._model = artifact
            self._model_error = ""
        except Exception as exc:
            self._model_error = str(exc)
            self._model = None

    def _reload_if_available(self) -> None:
        if self._model is not None:
            return
        if self.model_path is None:
            return
        if not self.model_path.exists():
            return
        self._load_model_if_possible()

    def _artifact_metadata(self) -> dict[str, Any]:
        if self.model_path is None or not self.model_path.exists():
            return {"model_bytes": 0, "model_updated_at": "", "feature_count": 0, "model_type": ""}
        try:
            stat = self.model_path.stat()
        except OSError:
            return {"model_bytes": 0, "model_updated_at": "", "feature_count": 0, "model_type": ""}
        return {
            "model_bytes": int(stat.st_size),
            "model_updated_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            "feature_count": self._feature_count(),
            "model_type": self._model.__class__.__name__ if self._model is not None else "",
        }

    def _feature_count(self) -> int:
        if self._model is None:
            return 0
        named_steps = getattr(self._model, "named_steps", {})
        tfidf = named_steps.get("tfidf") if isinstance(named_steps, dict) else None
        vocabulary = getattr(tfidf, "vocabulary_", None)
        return len(vocabulary) if isinstance(vocabulary, dict) else 0
