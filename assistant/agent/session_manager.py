"""Load, create, and persist sessions."""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote
from uuid import uuid4

from assistant.agent.session import Session, create_message, estimate_tokens, utc_now_iso
from assistant.config.schema import AppConfig


class SessionManager:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._active_sessions: dict[str, Session] = {}
        self._prune_task: asyncio.Task[None] | None = None

    async def load_or_create(self, session_key: str) -> Session:
        if session_key in self._active_sessions:
            return self._active_sessions[session_key]

        latest_path = await asyncio.to_thread(self._find_latest_session_path, session_key)
        if latest_path is None:
            session = await self.create_session(session_key)
        else:
            session = await asyncio.to_thread(self._load_session_from_path, session_key, latest_path)

        self._active_sessions[session_key] = session
        return session

    async def create_session(self, session_key: str) -> Session:
        created_at = utc_now_iso()
        session_id = f"{created_at.replace(':', '').replace('-', '').replace('+00:00', 'Z')}-{uuid4().hex[:8]}"
        session_dir = self.config.sessions_dir / self._session_dir_name(session_key)
        storage_path = session_dir / f"{session_id}.jsonl"
        snapshot_path = session_dir / f"{session_id}.snapshot.json"

        def _create() -> None:
            session_dir.mkdir(parents=True, exist_ok=True)
            storage_path.touch(exist_ok=True)

        await asyncio.to_thread(_create)
        session = Session(
            session_id=session_id,
            session_key=session_key,
            messages=[],
            token_count=0,
            created_at=created_at,
            updated_at=created_at,
            storage_path=storage_path,
            snapshot_path=snapshot_path,
            metadata={"memory_flush_ran": False},
        )
        self._active_sessions[session_key] = session
        return session

    async def reset_session(self, session_key: str) -> Session:
        session = await self.create_session(session_key)
        self._active_sessions[session_key] = session
        return session

    def active_count(self) -> int:
        return len(self._active_sessions)

    async def latest_session_path(self, session_key: str) -> Path | None:
        return await asyncio.to_thread(self._find_latest_session_path, session_key)

    async def session_history(self, session_key: str, limit: int = 50) -> list[dict[str, Any]]:
        session = await self.load_or_create(session_key)
        if limit <= 0:
            return []
        return session.messages[-limit:]

    async def session_status(self, session_key: str) -> dict[str, Any]:
        session = await self.load_or_create(session_key)
        return {
            "session_key": session.session_key,
            "session_id": session.session_id,
            "token_count": session.token_count,
            "message_count": len(session.messages),
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "storage_path": str(session.storage_path),
        }

    async def list_sessions(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_sessions_sync)

    async def get_session_by_id(self, session_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._get_session_by_id_sync, session_id)

    async def append_message(self, session: Session, message: dict[str, Any]) -> None:
        await self._append_record(session.storage_path, message)
        session.messages.append(message)
        session.token_count = estimate_tokens(session.messages)
        session.updated_at = utc_now_iso()

    async def apply_compaction(
        self,
        session: Session,
        trimmed_messages: list[dict[str, Any]],
        summary: str,
    ) -> None:
        trimmed_ids = [message["id"] for message in trimmed_messages]
        summary_message = create_message("system", f"[SUMMARY]: {summary}")
        record = {
            "record_type": "compaction",
            "id": uuid4().hex,
            "trimmed_message_ids": trimmed_ids,
            "summary_message": summary_message,
            "created_at": utc_now_iso(),
        }
        await self._append_record(session.storage_path, record)
        remaining = [message for message in session.messages if message["id"] not in set(trimmed_ids)]
        session.messages = [summary_message, *remaining]
        session.token_count = estimate_tokens(session.messages)
        session.updated_at = utc_now_iso()
        await asyncio.to_thread(session.snapshot)

    async def _append_record(self, storage_path: Path, record: dict[str, Any]) -> None:
        def _write() -> None:
            storage_path.parent.mkdir(parents=True, exist_ok=True)
            with storage_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

        await asyncio.to_thread(_write)

    def _find_latest_session_path(self, session_key: str) -> Path | None:
        session_dir = self.config.sessions_dir / self._session_dir_name(session_key)
        if not session_dir.exists():
            return None
        candidates = sorted(session_dir.glob("*.jsonl"), key=lambda item: item.stat().st_mtime, reverse=True)
        return candidates[0] if candidates else None

    def _load_session_from_path(self, session_key: str, storage_path: Path) -> Session:
        snapshot_path = storage_path.with_suffix(".snapshot.json")
        snapshot_data = self._load_snapshot(snapshot_path)
        messages_by_id: dict[str, dict[str, Any]] = {}
        ordered_ids: list[str] = []
        created_at = snapshot_data.get("created_at", utc_now_iso())
        updated_at = snapshot_data.get("updated_at", created_at)
        first_message_seen = bool(snapshot_data)
        snapshot_created_at = self._parse_iso_datetime(snapshot_data.get("snapshot_created_at"))

        if snapshot_data:
            messages = snapshot_data.get("messages", [])
            for message in messages:
                if not isinstance(message, dict) or "id" not in message:
                    continue
                messages_by_id[message["id"]] = message
                ordered_ids.append(message["id"])

        if storage_path.exists():
            with storage_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    record = json.loads(line)
                    record_created_at = self._parse_iso_datetime(record.get("created_at"))
                    if snapshot_created_at is not None and record_created_at is not None and record_created_at <= snapshot_created_at:
                        continue
                    updated_at = record.get("created_at", updated_at)
                    if record.get("record_type") == "message":
                        messages_by_id[record["id"]] = record
                        if record["id"] not in ordered_ids:
                            ordered_ids.append(record["id"])
                        if not first_message_seen:
                            created_at = record.get("created_at", created_at)
                            first_message_seen = True
                    elif record.get("record_type") == "compaction":
                        trimmed_ids = set(record.get("trimmed_message_ids", []))
                        ordered_ids = [message_id for message_id in ordered_ids if message_id not in trimmed_ids]
                        for trimmed_id in trimmed_ids:
                            messages_by_id.pop(trimmed_id, None)
                        summary_message = record["summary_message"]
                        messages_by_id[summary_message["id"]] = summary_message
                        ordered_ids.append(summary_message["id"])

        messages = [messages_by_id[message_id] for message_id in ordered_ids]
        return Session(
            session_id=storage_path.stem,
            session_key=session_key,
            messages=messages,
            token_count=estimate_tokens(messages),
            created_at=created_at,
            updated_at=updated_at,
            storage_path=storage_path,
            snapshot_path=snapshot_path,
            metadata={
                "memory_flush_ran": False,
                **({"snapshot_created_at": snapshot_data.get("snapshot_created_at")} if snapshot_data else {}),
            },
        )

    def _list_sessions_sync(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for session_dir in sorted(self.config.sessions_dir.iterdir()):
            if not session_dir.is_dir() or session_dir.name == "archive":
                continue
            decoded_session_key = self._session_key_from_dir_name(session_dir.name)
            for file_path in sorted(session_dir.glob("*.jsonl"), key=lambda item: item.stat().st_mtime, reverse=True):
                session = self._load_session_from_path(decoded_session_key, file_path)
                rows.append(
                    {
                        "session_key": session.session_key,
                        "session_id": session.session_id,
                        "token_count": session.token_count,
                        "last_active": session.updated_at,
                        "storage_path": str(session.storage_path),
                    }
                )
        return rows

    def _get_session_by_id_sync(self, session_id: str) -> dict[str, Any]:
        for session_dir in self.config.sessions_dir.iterdir():
            if not session_dir.is_dir() or session_dir.name == "archive":
                continue
            candidate = session_dir / f"{session_id}.jsonl"
            if candidate.exists():
                session = self._load_session_from_path(self._session_key_from_dir_name(session_dir.name), candidate)
                return {
                    "session_key": session.session_key,
                    "session_id": session.session_id,
                    "messages": session.messages,
                    "token_count": session.token_count,
                    "storage_path": str(session.storage_path),
                }
        raise FileNotFoundError(f"Unknown session id '{session_id}'.")

    async def prune_sessions(self) -> None:
        await asyncio.to_thread(self._prune_sessions_sync)

    async def start_pruning_task(self) -> None:
        await self.prune_sessions()
        if self._prune_task is None:
            self._prune_task = asyncio.create_task(self._pruning_loop())

    async def stop_pruning_task(self) -> None:
        if self._prune_task is None:
            return
        self._prune_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._prune_task
        self._prune_task = None

    async def _pruning_loop(self) -> None:
        while True:
            await asyncio.sleep(24 * 60 * 60)
            await self.prune_sessions()

    def _prune_sessions_sync(self) -> None:
        root = self.config.sessions_dir
        archive_root = self.config.archive_sessions_dir
        archive_root.mkdir(parents=True, exist_ok=True)
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.config.agent.session_max_age_days)

        for session_dir in root.iterdir():
            if not session_dir.is_dir() or session_dir.name == "archive":
                continue

            files = sorted(session_dir.glob("*.jsonl"), key=lambda item: item.stat().st_mtime)
            fresh_files: list[Path] = []
            for file_path in files:
                modified_at = datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc)
                if modified_at < cutoff:
                    file_path.unlink(missing_ok=True)
                    file_path.with_suffix(".snapshot.json").unlink(missing_ok=True)
                else:
                    fresh_files.append(file_path)

            overflow = max(0, len(fresh_files) - self.config.agent.max_sessions_per_key)
            for file_path in fresh_files[:overflow]:
                target_dir = archive_root / session_dir.name
                target_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(file_path), str(target_dir / file_path.name))
                snapshot_path = file_path.with_suffix(".snapshot.json")
                if snapshot_path.exists():
                    shutil.move(str(snapshot_path), str(target_dir / snapshot_path.name))

    def _session_dir_name(self, session_key: str) -> str:
        return quote(session_key, safe="")

    def _session_key_from_dir_name(self, dir_name: str) -> str:
        return unquote(dir_name)

    def _load_snapshot(self, snapshot_path: Path) -> dict[str, Any]:
        if not snapshot_path.exists():
            return {}
        try:
            payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(payload, dict):
            return {}
        return payload

    def _parse_iso_datetime(self, value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return None
