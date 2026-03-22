"""Helpers for browser-based webchat connections."""

from __future__ import annotations

from uuid import uuid4

from fastapi import WebSocket


def get_webchat_device_id(websocket: WebSocket) -> str:
    cookie_value = websocket.cookies.get("sonarbot_webchat")
    if cookie_value:
        return cookie_value
    query_value = websocket.query_params.get("device_id")
    if query_value:
        return query_value
    return f"webchat-{uuid4().hex}"
