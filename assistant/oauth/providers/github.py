"""GitHub OAuth provider."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx


class GitHubOAuthProvider:
    name = "github"
    authorize_url = "https://github.com/login/oauth/authorize"
    token_url = "https://github.com/login/oauth/access_token"

    def __init__(self, config) -> None:
        self.config = config

    @property
    def client_id(self) -> str:
        return self.config.oauth.github.client_id

    @property
    def client_secret(self) -> str:
        return self.config.oauth.github.client_secret

    @property
    def scopes(self) -> list[str]:
        return self.config.oauth.github.scopes

    def build_authorize_url(self, redirect_uri: str, state: str) -> str:
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(self.scopes),
            "state": state,
        }
        return str(httpx.URL(self.authorize_url, params=params))

    async def exchange_code(self, code: str, redirect_uri: str) -> dict[str, Any]:
        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        }
        headers = {"Accept": "application/json"}
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(self.token_url, data=payload, headers=headers)
            response.raise_for_status()
        data = response.json()
        return self._normalize_tokens(data)

    async def refresh_tokens(self, refresh_token: str) -> dict[str, Any]:
        # GitHub's classic OAuth flow usually doesn't expose refresh tokens.
        expires_at = datetime.now(timezone.utc) + timedelta(days=3650)
        return {
            "provider": self.name,
            "user_id": "default",
            "access_token": "",
            "refresh_token": refresh_token,
            "expires_at": expires_at.isoformat(),
            "scopes": self.scopes,
            "token_type": "Bearer",
        }

    def _normalize_tokens(self, data: dict[str, Any]) -> dict[str, Any]:
        expires_at = datetime.now(timezone.utc) + timedelta(days=3650)
        return {
            "provider": self.name,
            "user_id": "default",
            "access_token": data.get("access_token", ""),
            "refresh_token": data.get("refresh_token", ""),
            "expires_at": expires_at.isoformat(),
            "scopes": self.scopes,
            "token_type": data.get("token_type", "bearer"),
        }
