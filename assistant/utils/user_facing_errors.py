"""Helpers for turning low-level runtime failures into user-facing messages."""

from __future__ import annotations

import re
from typing import Any

import httpx


def format_user_facing_exception(exc: Exception) -> str:
    """Return a concise, safe message for upstream/provider failures."""
    if isinstance(exc, httpx.HTTPStatusError):
        return _message_for_status(exc.response.status_code)

    text = str(exc).strip()
    return sanitize_error_text(text)


def format_browser_exception(exc: Exception) -> str:
    text = str(exc).strip()
    lowered = text.lower()
    if "locator.fill" in lowered or "does not have a role allowing" in lowered or "not an <input>" in lowered:
        return "I found a search or form control on the page, but it was not an editable input. I need a better site-specific selector for this page."
    if "timeout" in lowered and "exceeded" in lowered:
        return "The page took too long to load or the target element did not appear in time."
    if "err_name_not_resolved" in lowered:
        return "The website address could not be resolved from this machine."
    if "could not find an editable search input" in lowered:
        return "I couldn't find a usable search box on that page."
    if "could not find any clickable results" in lowered:
        return "I couldn't find a clear result to open on that page."
    if "playwright is not installed" in lowered:
        return text
    return sanitize_error_text(text, fallback="I couldn't finish that browser task right now. Please try again.")


def sanitize_error_text(text: str, *, fallback: str = "Something went wrong while processing your request. Please try again.") -> str:
    """Hide noisy transport/provider internals while preserving safe app errors."""
    normalized = text.strip()
    if not normalized:
        return fallback

    lower = normalized.lower()
    status_match = re.search(r"\b(4\d\d|5\d\d)\b", normalized)
    if any(
        marker in lower
        for marker in (
            "generatecontent",
            "resource_exhausted",
            "too many requests",
            "client error",
            "server error",
            "mdn web docs",
            "developer.mozilla.org",
        )
    ):
        if status_match is not None:
            try:
                return _message_for_status(int(status_match.group(1)))
            except ValueError:
                return fallback
        return fallback

    if normalized.startswith("[Model error]"):
        return fallback

    return normalized


def _message_for_status(status_code: int) -> str:
    if status_code == 400:
        return "The model request could not be completed right now. Please try again."
    if status_code in {401, 403}:
        return "The connected provider rejected the request. Please reconnect the account and try again."
    if status_code == 404:
        return "The requested model or upstream service could not be found. Please try again later."
    if status_code == 408:
        return "The upstream service timed out. Please try again."
    if status_code == 409:
        return "The request could not be completed because of a temporary conflict. Please retry."
    if status_code == 429:
        return "The model is temporarily rate-limited. Please wait a minute and try again."
    if 500 <= status_code <= 599:
        return "The upstream model service is temporarily unavailable. Please try again shortly."
    return "Something went wrong while processing your request. Please try again."
