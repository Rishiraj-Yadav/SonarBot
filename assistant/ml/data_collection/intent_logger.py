"""Intent logging utility for building browser intent training datasets."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def _redact(text: str) -> str:
    cleaned = text
    cleaned = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[REDACTED_EMAIL]", cleaned)
    cleaned = re.sub(r"https?://\S+", "[REDACTED_URL]", cleaned)
    cleaned = re.sub(r"\b[A-Fa-f0-9]{24,}\b", "[REDACTED_TOKEN]", cleaned)
    cleaned = re.sub(r"[A-Za-z]:\\[^\s]+", "[REDACTED_PATH]", cleaned)
    return cleaned


@dataclass(slots=True)
class IntentLogRow:
    ts: str
    session_key: str
    message: str
    predicted_intent: str
    confidence: float
    fallback_used: bool
    label: str = ""


class IntentLogger:
    def __init__(self, csv_path: Path) -> None:
        self.csv_path = csv_path
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.csv_path.exists():
            with self.csv_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["ts", "session_key", "message", "predicted_intent", "confidence", "fallback_used", "label"])

    def append(
        self,
        *,
        session_key: str,
        message: str,
        predicted_intent: str,
        confidence: float,
        fallback_used: bool,
        label: str = "",
    ) -> None:
        row = IntentLogRow(
            ts=datetime.now(timezone.utc).isoformat(),
            session_key=session_key,
            message=_redact(message),
            predicted_intent=predicted_intent,
            confidence=max(0.0, min(1.0, float(confidence))),
            fallback_used=bool(fallback_used),
            label=label,
        )
        with self.csv_path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow([row.ts, row.session_key, row.message, row.predicted_intent, row.confidence, row.fallback_used, row.label])

