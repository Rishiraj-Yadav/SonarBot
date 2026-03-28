from __future__ import annotations

import httpx

from assistant.utils.user_facing_errors import format_user_facing_exception, sanitize_error_text


def test_format_user_facing_exception_maps_rate_limit() -> None:
    request = httpx.Request("POST", "https://example.com")
    response = httpx.Response(429, request=request)
    exc = httpx.HTTPStatusError("rate limited", request=request, response=response)

    assert format_user_facing_exception(exc) == "The model is temporarily rate-limited. Please wait a minute and try again."


def test_sanitize_error_text_hides_raw_provider_error() -> None:
    raw = (
        "Client error '400 Bad Request' for url "
        "'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent' "
        "For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/400"
    )

    assert sanitize_error_text(raw) == "The model request could not be completed right now. Please try again."


def test_sanitize_error_text_maps_leaked_gemini_key_to_actionable_message() -> None:
    raw = (
        "Gemini rejected the configured API key because it was reported as leaked. "
        "Replace llm.gemini_api_key in C:\\Users\\Ritesh\\.assistant\\config.toml and restart SonarBot."
    )

    assert sanitize_error_text(raw) == "The configured Gemini API key was revoked as leaked. Update llm.gemini_api_key and restart SonarBot."
