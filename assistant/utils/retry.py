"""Retry and circuit-breaker helpers."""

from __future__ import annotations

import asyncio
import functools
import time
from typing import Any, Awaitable, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def async_retry(
    max_attempts: int = 3,
    base_delay: float = 0.5,
    *,
    non_retry_exceptions: tuple[type[BaseException], ...] = (),
) -> Callable[[F], F]:
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
                    if non_retry_exceptions and isinstance(exc, non_retry_exceptions):
                        raise
                    last_exc = exc
                    if attempt >= max_attempts:
                        raise
                    await asyncio.sleep(base_delay * (2 ** (attempt - 1)))
            if last_exc is not None:
                raise last_exc

        return wrapper  # type: ignore[return-value]

    return decorator


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
