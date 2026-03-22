"""Google OAuth provider."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx


class GoogleOAuthProvider:
    name = "google"
    authorize_url = "https://accounts.google.com/o/oauth2/v2/auth"
    token_url = "https://oauth2.googleapis.com/token"

    def __init__(self, config) -> None:
        self.config = config

    @property
    def client_id(self) -> str:
        return self.config.oauth.google.client_id

    @property
    def client_secret(self) -> str:
        return self.config.oauth.google.client_secret

    @property
    def scopes(self) -> list[str]:
        return self.config.oauth.google.scopes

    def build_authorize_url(self, redirect_uri: str, state: str) -> str:
        scope_string = " ".join(self.scopes)
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "access_type": "offline",
            "prompt": "consent",
            "scope": scope_string,
            "state": state,
        }
        return str(httpx.URL(self.authorize_url, params=params))

    async def exchange_code(self, code: str, redirect_uri: str) -> dict[str, Any]:
        payload = {
            "code": code,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(self.token_url, data=payload)
            response.raise_for_status()
        data = response.json()
        return self._normalize_tokens(data)

    async def refresh_tokens(self, refresh_token: str) -> dict[str, Any]:
        payload = {
            "refresh_token": refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "refresh_token",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(self.token_url, data=payload)
            response.raise_for_status()
        data = response.json()
        data["refresh_token"] = data.get("refresh_token") or refresh_token
        return self._normalize_tokens(data)

    def _normalize_tokens(self, data: dict[str, Any]) -> dict[str, Any]:
        expires_in = int(data.get("expires_in", 3600))
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        return {
            "provider": self.name,
            "user_id": str(data.get("id_token", "default")),
            "access_token": data.get("access_token", ""),
            "refresh_token": data.get("refresh_token", ""),
            "expires_at": expires_at.isoformat(),
            "scopes": self.scopes,
            "token_type": data.get("token_type", "Bearer"),
        }
