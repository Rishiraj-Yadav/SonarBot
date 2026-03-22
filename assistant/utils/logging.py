"""Structured logging configuration."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

try:  # pragma: no cover - optional dependency fallback
    import structlog
except Exception:  # pragma: no cover - optional dependency fallback
    structlog = None


def configure_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8"), logging.StreamHandler()],
        force=True,
    )
    if structlog is None:
        return
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.stdlib.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def get_logger(name: str, **fields: Any):
    if structlog is None:  # pragma: no cover - fallback logger
        return logging.getLogger(name)
    return structlog.get_logger(name).bind(**fields)
