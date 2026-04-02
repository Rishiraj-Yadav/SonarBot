from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from assistant.gateway.server import create_app
from assistant.oauth.flow import OAuthFlowManager
from assistant.oauth.manager import OAuthTokenManager
from assistant.oauth.providers.google import GoogleOAuthProvider


@pytest.mark.asyncio
async def test_oauth_flow_saves_and_refreshes_token(app_config, monkeypatch) -> None:
    app_config.oauth.google.client_id = "google-client"
    app_config.oauth.google.client_secret = "google-secret"

    token_manager = OAuthTokenManager(app_config)
    await token_manager.initialize()
    flow_manager = OAuthFlowManager(app_config, token_manager)

    async def fake_exchange_code(self, code: str, redirect_uri: str):
        assert code == "auth-code"
        assert redirect_uri.endswith("/oauth/callback/google")
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
    await flow_manager.handle_callback("google", "auth-code", flow["state"])

    refreshed = await token_manager.get_token("google", "default")

    assert refreshed is not None
    assert refreshed["access_token"] == "token-2"
    assert refreshed["refresh_token"] == "refresh-1"

    repeated = await flow_manager.handle_callback("google", "auth-code", flow["state"])
    assert repeated["access_token"] == "token-1"


@pytest.mark.asyncio
async def test_get_token_falls_back_to_most_recent_provider_token(app_config) -> None:
    token_manager = OAuthTokenManager(app_config)
    await token_manager.initialize()
    await token_manager.save_token(
        "google",
        {
            "provider": "google",
            "user_id": "google-user-123",
            "access_token": "google-token",
            "refresh_token": "refresh-token",
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            "scopes": ["gmail.readonly"],
        },
        user_id="google-user-123",
    )

    resolved = await token_manager.get_token("google")

    assert resolved is not None
    assert resolved["access_token"] == "google-token"
    assert resolved["user_id"] == "google-user-123"


def test_gateway_oauth_callback_endpoint_completes_flow(app_config, monkeypatch) -> None:
    async def fake_handle_callback(provider_name: str, code: str, state: str):
        assert provider_name == "github"
        assert code == "auth-code"
        assert state == "state-123"
        return {"provider": "github", "user_id": "default"}

    app = create_app(config=app_config)
    with TestClient(app) as client:
        services = client.app.state.services
        monkeypatch.setattr(services.oauth_flow_manager, "handle_callback", fake_handle_callback)
        response = client.get("/oauth/callback/github?code=auth-code&state=state-123")
        assert response.status_code == 200
        assert "GitHub connected" in response.text


def test_google_oauth_provider_normalizes_missing_expires_in(app_config) -> None:
    provider = GoogleOAuthProvider(app_config)

    tokens = provider._normalize_tokens(
        {
            "access_token": "token-1",
            "refresh_token": "refresh-1",
            "expires_in": None,
        }
    )

    assert tokens["access_token"] == "token-1"
    assert tokens["refresh_token"] == "refresh-1"
    assert tokens["expires_at"]
