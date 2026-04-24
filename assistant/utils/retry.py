"""Retry and circuit-breaker helpers."""

from __future__ import annotations

import asyncio
import functools
import time
from typing import Any, Awaitable, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])

# When Gemini returns 429 (RESOURCE_EXHAUSTED) the quota window is typically
# 60 seconds.  A short base_delay (0.5 s) just fires the retries in a burst
# and wastes the attempts.  Use a much longer base for 429 responses.
_RATE_LIMIT_BASE_DELAY = 8.0


def async_retry(max_attempts: int = 3, base_delay: float = 0.5) -> Callable[[F], F]:
    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            attempt = 0
            last_exc: Exception | None = None
            while attempt < max_attempts:
                attempt += 1
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:  # pragma: no cover - defensive wrapper
                    last_exc = exc
                    if attempt >= max_attempts:
                        raise
                    # Use a longer delay for rate-limit errors so that we
                    # don't burn all retries in a fraction of a second.
                    is_rate_limit = _is_rate_limit_error(exc)
                    delay = (
                        _RATE_LIMIT_BASE_DELAY * (2 ** (attempt - 1))
                        if is_rate_limit
                        else base_delay * (2 ** (attempt - 1))
                    )
                    await asyncio.sleep(delay)
            if last_exc is not None:
                raise last_exc

        return wrapper  # type: ignore[return-value]

    return decorator


def _is_rate_limit_error(exc: Exception) -> bool:
    """Return True if exc looks like an HTTP 429 / RESOURCE_EXHAUSTED error."""
    try:
        import httpx  # local import to avoid coupling

        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code == 429
    except ImportError:
        pass
    msg = str(exc).lower()
    return "429" in msg or "resource_exhausted" in msg or "too many requests" in msg


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, reset_timeout_seconds: int = 60) -> None:
        self.failure_threshold = failure_threshold
        self.reset_timeout_seconds = reset_timeout_seconds
        self._failures = 0
        self._opened_at: float | None = None

    def allow_request(self) -> bool:
        if self._opened_at is None:
            return True
        if (time.monotonic() - self._opened_at) >= self.reset_timeout_seconds:
            self._opened_at = None
            self._failures = 0
            return True
        return False

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._opened_at = time.monotonic()
