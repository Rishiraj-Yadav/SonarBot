"""Pending approval management for host actions."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from assistant.system_access.models import HostApprovalRequest


class HostApprovalManager:
    def __init__(self, config, store, *, on_created=None, on_updated=None) -> None:
        self.config = config
        self.store = store
        self.on_created = on_created
        self.on_updated = on_updated
        self._pending: dict[str, asyncio.Future[str]] = {}
        self._session_cache: set[tuple[str, str, str]] = set()

    async def request(
        self,
        *,
        user_id: str,
        session_id: str,
        session_key: str,
        connection_id: str,
        channel_name: str,
        action_kind: str,
        target_summary: str,
        category: str,
        payload: dict[str, Any],
    ) -> tuple[str, str, dict[str, Any]]:
        cache_key = (user_id, session_id, category)
        if category == "ask_once" and self.config.system_access.ask_once_session_cache and cache_key in self._session_cache:
            return "approved", "session_cache", {}

        approval = HostApprovalRequest(
            user_id=user_id,
            session_id=session_id,
            session_key=session_key,
            connection_id=connection_id,
            channel_name=channel_name,
            action_kind=action_kind,
            target_summary=target_summary,
            category=category,
            payload=payload,
        )
        await self.store.create_approval(approval)
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._pending[approval.approval_id] = future
        if self.on_created is not None:
            await self.on_created(approval.to_dict())
        try:
            decision = await asyncio.wait_for(future, timeout=self.config.system_access.approval_timeout_seconds)
        except asyncio.TimeoutError:
            decision = "expired"
            await self.store.update_approval_status(approval.approval_id, decision)
        finally:
            self._pending.pop(approval.approval_id, None)

        if decision == "approved" and category == "ask_once" and self.config.system_access.ask_once_session_cache:
            self._session_cache.add(cache_key)
        updated = await self.store.get_approval(approval.approval_id) or approval.to_dict()
        if self.on_updated is not None:
            await self.on_updated(updated)
        return decision, "approval", updated

    async def decide(self, approval_id: str, decision: str) -> dict[str, Any]:
        normalized = "approved" if decision == "approved" else "rejected"
        await self.store.update_approval_status(approval_id, normalized)
        future = self._pending.get(approval_id)
        if future is not None and not future.done():
            future.set_result(normalized)
        approval = await self.store.get_approval(approval_id)
        if approval is None:
            raise KeyError(f"Unknown host approval '{approval_id}'.")
        if self.on_updated is not None:
            await self.on_updated(approval)
        return approval

    async def list_approvals(self, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        approvals = await self.store.list_approvals(user_id, limit=limit)
        now = datetime.now(timezone.utc)
        for approval in approvals:
            try:
                expires_at = datetime.fromisoformat(str(approval.get("expires_at")))
                approval["expired"] = expires_at <= now
            except ValueError:
                approval["expired"] = False
        return approvals
