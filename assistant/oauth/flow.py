"""Interactive OAuth flow management using the main gateway callback."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from assistant.oauth.providers import get_oauth_provider


@dataclass(slots=True)
class PendingOAuthFlow:
    provider_name: str
    state: str
    redirect_uri: str
    result_future: asyncio.Future[dict[str, Any]]


class OAuthFlowManager:
    def __init__(self, config, token_manager) -> None:
        self.config = config
        self.token_manager = token_manager
        self._pending_by_state: dict[str, PendingOAuthFlow] = {}

    async def start_oauth_flow(self, provider_name: str) -> dict[str, Any]:
        provider = get_oauth_provider(provider_name, self.config)
        if not getattr(provider, "client_id", "") or not getattr(provider, "client_secret", ""):
            raise RuntimeError(f"OAuth provider '{provider_name}' is not configured.")

        state = uuid4().hex
        redirect_uri = f"{self._gateway_base_url()}/oauth/callback/{provider_name}"
        authorize_url = provider.build_authorize_url(redirect_uri, state)
        result_future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending_by_state[state] = PendingOAuthFlow(
            provider_name=provider_name,
            state=state,
            redirect_uri=redirect_uri,
            result_future=result_future,
        )
        return {
            "provider": provider_name,
            "authorize_url": authorize_url,
            "state": state,
            "redirect_uri": redirect_uri,
            "instructions": f"Open this URL in your browser to connect {provider_name}: {authorize_url}",
        }

    async def handle_callback(self, provider_name: str, code: str, state: str) -> dict[str, Any]:
        pending = self._pending_by_state.get(state)
        if pending is None:
            raise RuntimeError("Unknown or expired OAuth state.")
        if pending.provider_name != provider_name:
            raise RuntimeError("OAuth provider mismatch for this state.")

        provider = get_oauth_provider(pending.provider_name, self.config)
        tokens = await provider.exchange_code(code, pending.redirect_uri)
        saved = await self.token_manager.save_token(pending.provider_name, tokens)
        if not pending.result_future.done():
            pending.result_future.set_result(saved)
        self._pending_by_state.pop(state, None)
        return saved

    async def wait_for_completion(self, state: str, timeout: int = 300) -> dict[str, Any]:
        pending = self._pending_by_state[state]
        return await asyncio.wait_for(pending.result_future, timeout=timeout)

    def _gateway_base_url(self) -> str:
        host = self.config.gateway.host.strip()
        if host in {"0.0.0.0", "::", ""}:
            host = "127.0.0.1"
        return f"http://{host}:{self.config.gateway.port}"
