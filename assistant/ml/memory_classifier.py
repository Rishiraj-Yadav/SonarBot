"""Lightweight message importance classifier with safe fallback."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:  # Optional dependency
    import joblib  # type: ignore
except Exception:  # pragma: no cover
    joblib = None


@dataclass(slots=True)
class MemoryDecision:
    keep: bool
    confidence: float
    reason: str = ""


class MemoryClassifier:
    def __init__(self, *, enabled: bool = False, min_confidence: float = 0.55, model_path: Path | None = None) -> None:
        self.enabled = enabled
        self.min_confidence = max(0.0, min(1.0, min_confidence))
        self.model_path = model_path
        self._model: Any = None
        self._model_error = ""
        self._load_model_if_possible()

    def status(self) -> dict[str, Any]:
        self._reload_if_available()
        return {
            "enabled": self.enabled,
            "min_confidence": self.min_confidence,
            "model_path": str(self.model_path) if self.model_path else "",
            "model_loaded": self._model is not None,
            "model_error": self._model_error,
        }

    def decide(self, text: str) -> MemoryDecision:
        self._reload_if_available()
        normalized = re.sub(r"\s+", " ", text).strip()
        if not normalized:
            return MemoryDecision(keep=False, confidence=0.0, reason="empty")
        if not self.enabled:
            return MemoryDecision(keep=self._heuristic_keep(normalized), confidence=0.6, reason="disabled_heuristic")

        if self._model is not None:
            try:
                pred = self._model.predict([normalized])[0]  # type: ignore[index]
                keep = bool(pred)
                confidence = 0.85
                if confidence >= self.min_confidence:
                    return MemoryDecision(keep=keep, confidence=confidence, reason="model")
            except Exception:
                pass
        return MemoryDecision(keep=self._heuristic_keep(normalized), confidence=0.55, reason="fallback_heuristic")

    def _heuristic_keep(self, text: str) -> bool:
        lowered = text.lower()
        signals = (
            "remember",
            "preference",
            "my name is",
            "i prefer",
            "always",
            "never",
            "important",
            "deadline",
            "appointment",
        )
        return any(token in lowered for token in signals)

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
            self._model = joblib.load(self.model_path)
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
