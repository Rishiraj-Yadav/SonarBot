"""Lightweight local browser intent classifier for starter analytics."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:  # Optional dependency
    import joblib  # type: ignore
except Exception:  # pragma: no cover
    joblib = None


class BrowserIntentClassifier:
    def __init__(self, *, model_path: Path | None = None) -> None:
        self.model_path = model_path
        self._model: Any = None
        self._labels: list[str] = []
        self._model_error = ""
        self._load_model_if_possible()

    def status(self) -> dict[str, Any]:
        self._reload_if_available()
        metadata = self._artifact_metadata()
        return {
            "enabled": bool(self.model_path and self.model_path.exists()),
            "model_path": str(self.model_path) if self.model_path else "",
            "model_loaded": self._model is not None,
            "model_error": self._model_error,
            "labels": self._labels,
            "label_count": len(self._labels),
            **metadata,
        }

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
            if isinstance(artifact, dict):
                self._model = artifact.get("pipeline")
                raw_labels = artifact.get("labels", [])
                self._labels = [str(item).strip() for item in raw_labels if str(item).strip()]
            else:
                self._model = artifact
            self._model_error = ""
        except Exception as exc:
            self._model = None
            self._model_error = str(exc)

    def _reload_if_available(self) -> None:
        if self._model is not None:
            return
        if self.model_path is None or not self.model_path.exists():
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
