"""ONNX browser intent predictor (CPU-only), with graceful fallback."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:  # Optional dependency
    import onnxruntime as ort  # type: ignore
except Exception:  # pragma: no cover
    ort = None


@dataclass(slots=True)
class IntentPrediction:
    intent: str
    confidence: float
    fallback_used: bool


class IntentPredictor:
    def __init__(self, model_path: Path, labels: list[str], *, min_confidence: float = 0.6) -> None:
        self.model_path = model_path
        self.labels = labels
        self.min_confidence = min_confidence
        self._session: Any = None
        self._load()

    def predict(self, text: str) -> IntentPrediction:
        if self._session is None or not text.strip():
            return IntentPrediction(intent="unknown", confidence=0.0, fallback_used=True)
        # Placeholder input contract; real notebook export should align names/shapes.
        try:
            outputs = self._session.run(None, {"text": [text]})
            probs = outputs[0][0]
            best_index = int(max(range(len(probs)), key=lambda i: probs[i]))
            confidence = float(probs[best_index])
            intent = self.labels[best_index] if best_index < len(self.labels) else "unknown"
            if confidence < self.min_confidence:
                return IntentPrediction(intent="unknown", confidence=confidence, fallback_used=True)
            return IntentPrediction(intent=intent, confidence=confidence, fallback_used=False)
        except Exception:
            return IntentPrediction(intent="unknown", confidence=0.0, fallback_used=True)

    def _load(self) -> None:
        if ort is None or not self.model_path.exists():
            return
        try:
            self._session = ort.InferenceSession(str(self.model_path), providers=["CPUExecutionProvider"])
        except Exception:
            self._session = None

