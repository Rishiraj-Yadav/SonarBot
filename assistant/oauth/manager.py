"""Encrypted OAuth token storage."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite

from assistant.oauth.providers import get_oauth_provider
from assistant.utils.crypto import derive_fernet


class OAuthTokenManager:
    def __init__(self, config) -> None:
        self.config = config
        self.db_path = config.data_db_path
        self._fernet = derive_fernet(config.gateway.token)

    async def initialize(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS oauth_tokens (
                    provider TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    encrypted_payload BLOB NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (provider, user_id)
                )
                """
            )
            await db.commit()

    async def save_token(self, provider: str, tokens: dict[str, Any], user_id: str | None = None) -> dict[str, Any]:
        await self.initialize()
        resolved_user_id = user_id or str(tokens.get("user_id") or "default")
        payload = dict(tokens)
        payload["user_id"] = resolved_user_id
        encrypted = self._fernet.encrypt(json.dumps(payload).encode("utf-8"))
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO oauth_tokens (provider, user_id, encrypted_payload, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(provider, user_id)
                DO UPDATE SET encrypted_payload = excluded.encrypted_payload, updated_at = excluded.updated_at
                """,
                (provider, resolved_user_id, encrypted, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()
        return payload

    async def get_token(self, provider: str, user_id: str = "default") -> dict[str, Any] | None:
        await self.initialize()
        record = await self._read_token(provider, user_id)
        if record is None:
            return None
        return await self.refresh_if_needed(provider, record)

    async def refresh_if_needed(self, provider: str, tokens: dict[str, Any]) -> dict[str, Any]:
        expires_at_raw = tokens.get("expires_at")
        if not expires_at_raw:
            return tokens
        expires_at = datetime.fromisoformat(str(expires_at_raw))
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at > (datetime.now(timezone.utc) + timedelta(minutes=5)):
            return tokens

        refresh_token = str(tokens.get("refresh_token", "")).strip()
        if not refresh_token:
            return tokens

        provider_impl = get_oauth_provider(provider, self.config)
        refreshed = await provider_impl.refresh_tokens(refresh_token)
        refreshed.setdefault("user_id", tokens.get("user_id", "default"))
        await self.save_token(provider, refreshed, user_id=str(refreshed["user_id"]))
        return refreshed

    async def list_connected(self) -> list[dict[str, Any]]:
        await self.initialize()
        output: list[dict[str, Any]] = []
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT provider, user_id, encrypted_payload FROM oauth_tokens ORDER BY provider, user_id") as cursor:
                async for provider, user_id, encrypted_payload in cursor:
                    payload = self._decrypt_payload(encrypted_payload)
                    output.append(
                        {
                            "provider": provider,
                            "user_id": user_id,
                            "expires_at": payload.get("expires_at"),
                            "scopes": payload.get("scopes", []),
                        }
                    )
        return output

    async def _read_token(self, provider: str, user_id: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT encrypted_payload FROM oauth_tokens WHERE provider = ? AND user_id = ?",
                (provider, user_id),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        return self._decrypt_payload(row[0])

    def _decrypt_payload(self, encrypted_payload: bytes | str) -> dict[str, Any]:
        blob = encrypted_payload if isinstance(encrypted_payload, bytes) else encrypted_payload.encode("utf-8")
        return json.loads(self._fernet.decrypt(blob).decode("utf-8"))
