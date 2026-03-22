"""Interactive local OAuth callback flow."""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from assistant.oauth.providers import get_oauth_provider


@dataclass(slots=True)
class PendingOAuthFlow:
    provider_name: str
    state: str
    redirect_uri: str
    server: asyncio.base_events.Server
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
        result_future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        async def callback(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await self._handle_http_callback(reader, writer, provider_name)
        server = await asyncio.start_server(
            callback,
            host="127.0.0.1",
            port=0,
        )
        port = server.sockets[0].getsockname()[1]
        redirect_uri = f"http://127.0.0.1:{port}/callback"
        authorize_url = provider.build_authorize_url(redirect_uri, state)
        pending = PendingOAuthFlow(
            provider_name=provider_name,
            state=state,
            redirect_uri=redirect_uri,
            server=server,
            result_future=result_future,
        )
        self._pending_by_state[state] = pending
        return {
            "provider": provider_name,
            "authorize_url": authorize_url,
            "state": state,
            "redirect_uri": redirect_uri,
            "instructions": f"Visit this URL to connect {provider_name}: {authorize_url}",
        }

    async def handle_callback(self, code: str, state: str) -> dict[str, Any]:
        pending = self._pending_by_state.get(state)
        if pending is None:
            raise RuntimeError("Unknown or expired OAuth state.")

        provider = get_oauth_provider(pending.provider_name, self.config)
        tokens = await provider.exchange_code(code, pending.redirect_uri)
        saved = await self.token_manager.save_token(pending.provider_name, tokens)
        pending.result_future.set_result(saved)
        pending.server.close()
        await pending.server.wait_closed()
        self._pending_by_state.pop(state, None)
        return saved

    async def wait_for_completion(self, state: str, timeout: int = 300) -> dict[str, Any]:
        pending = self._pending_by_state[state]
        return await asyncio.wait_for(pending.result_future, timeout=timeout)

    async def _handle_http_callback(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, provider_name: str) -> None:
        try:
            request_line = await reader.readline()
            path = request_line.decode("utf-8", errors="replace").split(" ")[1]
            query = path.split("?", 1)[1] if "?" in path else ""
            params = {}
            for item in query.split("&"):
                if "=" not in item:
                    continue
                key, value = item.split("=", 1)
                params[key] = value
            code = params.get("code", "")
            state = params.get("state", "")
            if code and state:
                await self.handle_callback(code, state)
                body = json.dumps({"ok": True, "provider": provider_name})
                response = (
                    "HTTP/1.1 200 OK\r\n"
                    "Content-Type: application/json\r\n"
                    f"Content-Length: {len(body)}\r\n\r\n{body}"
                )
            else:
                body = json.dumps({"ok": False, "error": "Missing code or state"})
                response = (
                    "HTTP/1.1 400 Bad Request\r\n"
                    "Content-Type: application/json\r\n"
                    f"Content-Length: {len(body)}\r\n\r\n{body}"
                )
            writer.write(response.encode("utf-8"))
            await writer.drain()
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
