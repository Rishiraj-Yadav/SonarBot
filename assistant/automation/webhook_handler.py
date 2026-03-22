"""Webhook verification and message rendering."""

from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace
from typing import Any


def verify_webhook_signature(secret: str, body: bytes, signature: str | None) -> bool:
    if not signature:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    normalized = signature.removeprefix("sha256=")
    return hmac.compare_digest(expected, normalized)


def render_webhook_message(message_template: str, payload: dict[str, Any]) -> str:
    context = {key: _to_namespace(value) for key, value in payload.items()}
    return message_template.format(**context)


def parse_webhook_body(body: bytes) -> dict[str, Any]:
    return json.loads(body.decode("utf-8"))


def _to_namespace(value: Any) -> Any:
    if isinstance(value, dict):
        return SimpleNamespace(**{key: _to_namespace(item) for key, item in value.items()})
    if isinstance(value, list):
        return [_to_namespace(item) for item in value]
    return value
