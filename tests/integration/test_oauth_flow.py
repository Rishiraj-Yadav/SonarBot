from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from assistant.oauth.flow import OAuthFlowManager
from assistant.oauth.manager import OAuthTokenManager


@pytest.mark.asyncio
async def test_oauth_flow_saves_and_refreshes_token(app_config, monkeypatch) -> None:
    app_config.oauth.google.client_id = "google-client"
    app_config.oauth.google.client_secret = "google-secret"

    token_manager = OAuthTokenManager(app_config)
    await token_manager.initialize()
    flow_manager = OAuthFlowManager(app_config, token_manager)

    async def fake_exchange_code(self, code: str, redirect_uri: str):
        assert code == "auth-code"
        assert redirect_uri.startswith("http://127.0.0.1:")
        return {
            "provider": "google",
            "user_id": "default",
            "access_token": "token-1",
            "refresh_token": "refresh-1",
            "expires_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
            "scopes": ["scope-a"],
        }

    async def fake_refresh_tokens(self, refresh_token: str):
        assert refresh_token == "refresh-1"
        return {
            "provider": "google",
            "user_id": "default",
            "access_token": "token-2",
            "refresh_token": "refresh-1",
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            "scopes": ["scope-a"],
        }

    monkeypatch.setattr("assistant.oauth.providers.google.GoogleOAuthProvider.exchange_code", fake_exchange_code)
    monkeypatch.setattr("assistant.oauth.providers.google.GoogleOAuthProvider.refresh_tokens", fake_refresh_tokens)

    flow = await flow_manager.start_oauth_flow("google")
    await flow_manager.handle_callback("auth-code", flow["state"])

    refreshed = await token_manager.get_token("google", "default")

    assert refreshed is not None
    assert refreshed["access_token"] == "token-2"
    assert refreshed["refresh_token"] == "refresh-1"
