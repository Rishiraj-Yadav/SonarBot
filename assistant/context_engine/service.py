"""Background life-context synthesis and proactive insight delivery."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from assistant.automation.models import Notification, utc_now_iso
from assistant.utils import get_logger


class ContextEngine:
    def __init__(
        self,
        config,
        *,
        model_provider,
        memory_manager,
        session_manager,
        oauth_token_manager,
        automation_store,
        notification_dispatcher,
        user_profiles,
    ) -> None:
        self.config = config
        self.model_provider = model_provider
        self.memory_manager = memory_manager
        self.session_manager = session_manager
        self.oauth_token_manager = oauth_token_manager
        self.automation_store = automation_store
        self.notification_dispatcher = notification_dispatcher
        self.user_profiles = user_profiles
        self.logger = get_logger("context_engine")
        self._task: asyncio.Task[None] | None = None
        self._last_run_at: str = ""
        self._last_error: str = ""
        self.snapshot_dir = self.config.agent.workspace_dir / self.config.context_engine.snapshot_subdir
        self.insights_dir = self.config.agent.workspace_dir / self.config.context_engine.insights_subdir

    async def start(self) -> None:
        if not self.config.context_engine.enabled or self._task is not None:
            return
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self.insights_dir.mkdir(parents=True, exist_ok=True)
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    def status(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.config.context_engine.enabled),
            "running": self._task is not None and not self._task.done(),
            "last_run_at": self._last_run_at,
            "last_error": self._last_error,
            "snapshot_dir": str(self.snapshot_dir),
        }

    async def run_once(self) -> dict[str, Any]:
        if not self.config.context_engine.enabled:
            return {"status": "disabled", "processed_users": 0}

        processed = 0
        notified = 0
        errors: list[str] = []
        user_ids = await self.user_profiles.list_user_ids()
        for user_id in user_ids:
            try:
                result = await self._process_user(user_id)
                processed += 1
                notified += int(result.get("notifications_sent", 0))
            except Exception as exc:  # pragma: no cover - defensive runtime path
                self.logger.exception("context_engine_user_failed", user_id=user_id)
                errors.append(f"{user_id}: {exc}")
        self._last_run_at = utc_now_iso()
        self._last_error = "; ".join(errors)
        return {
            "status": "completed" if not errors else "completed_with_errors",
            "processed_users": processed,
            "notifications_sent": notified,
            "errors": errors,
        }

    async def latest_snapshot(self, user_id: str) -> dict[str, Any]:
        path = self._snapshot_path(user_id)
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    async def _run_loop(self) -> None:
        interval_seconds = max(300, int(self.config.context_engine.interval_minutes * 60))
        try:
            while True:
                try:
                    await self.run_once()
                except Exception as exc:  # pragma: no cover - defensive runtime path
                    self._last_error = str(exc)
                    self.logger.exception("context_engine_run_failed")
                await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            raise

    async def _process_user(self, user_id: str) -> dict[str, Any]:
        profile = await self.user_profiles.get_profile(user_id)
        if not profile.get("automation_enabled", True):
            return {"status": "skipped", "reason": "automation_disabled", "notifications_sent": 0}

        snapshot = await self._build_life_state(user_id)
        await self._write_json(self._snapshot_path(user_id), snapshot)

        insights = await self._generate_insights(snapshot)
        if not insights:
            return {"status": "completed", "notifications_sent": 0}

        history = await self._load_insight_history(user_id)
        sent_notifications = 0
        if self._in_quiet_hours(profile):
            return {"status": "quiet_hours", "notifications_sent": 0, "insight_count": len(insights)}

        for insight in insights[: self.config.context_engine.max_notifications_per_run]:
            fingerprint = self._insight_fingerprint(insight)
            if self._already_notified(history, fingerprint):
                continue
            notification = Notification(
                notification_id=uuid4().hex,
                user_id=user_id,
                title=str(insight.get("title", "Life context update")).strip()[:80] or "Life context update",
                body=str(insight.get("body", "")).strip()[:1000],
                source="context-engine",
                severity=self._severity_for_urgency(float(insight.get("urgency", 0))),
                delivery_mode="primary",
                status="queued",
                target_channels=[],
                metadata={
                    "rule_name": "context-engine",
                    "delivery_policy": "primary",
                    "confidence": insight.get("confidence", 0),
                    "urgency": insight.get("urgency", 0),
                    "fingerprint": fingerprint,
                },
            )
            if not notification.body:
                continue
            await self.notification_dispatcher.dispatch(notification)
            history.append(
                {
                    "fingerprint": fingerprint,
                    "title": notification.title,
                    "sent_at": utc_now_iso(),
                    "confidence": insight.get("confidence", 0),
                    "urgency": insight.get("urgency", 0),
                }
            )
            sent_notifications += 1

        await self._write_json(self._insight_history_path(user_id), self._trim_history(history))
        return {"status": "completed", "notifications_sent": sent_notifications, "insight_count": len(insights)}

    async def _build_life_state(self, user_id: str) -> dict[str, Any]:
        long_term = await self.memory_manager.read_long_term()
        daily = await self.memory_manager.read_today_and_yesterday()
        recent_sessions = await self._recent_sessions(user_id)
        recent_notifications = await self.automation_store.list_notifications(user_id, limit=8)
        recent_runs = await self.automation_store.list_runs(user_id, limit=8)
        google_context = await self._google_context(user_id)

        return {
            "generated_at": utc_now_iso(),
            "user_id": user_id,
            "memory": {
                "long_term": self._truncate(long_term, 2500),
                "recent_daily": self._truncate(daily, 2200),
            },
            "recent_sessions": recent_sessions,
            "automation": {
                "notifications": [
                    {
                        "title": self._truncate(str(item.get("title", "")), 120),
                        "body": self._truncate(str(item.get("body", "")), 220),
                        "source": item.get("source", ""),
                        "created_at": item.get("created_at", ""),
                    }
                    for item in recent_notifications
                ],
                "runs": [
                    {
                        "rule_name": item.get("rule_name", ""),
                        "status": item.get("status", ""),
                        "result_text": self._truncate(str(item.get("result_text", "")), 220),
                        "created_at": item.get("created_at", ""),
                    }
                    for item in recent_runs
                ],
            },
            "google": google_context,
            "signals": {
                "connected_providers": [item["provider"] for item in await self.oauth_token_manager.list_connected() if item.get("user_id") == user_id or user_id == self.config.users.default_user_id],
                "session_count": len(recent_sessions),
                "notification_count": len(recent_notifications),
                "automation_run_count": len(recent_runs),
            },
        }

    async def _recent_sessions(self, user_id: str) -> list[dict[str, Any]]:
        identities = await self.user_profiles.list_identities(user_id)
        candidate_session_keys = ["main", "webchat_main"]
        for identity in identities:
            identity_type = str(identity.get("identity_type", ""))
            identity_value = str(identity.get("identity_value", ""))
            if identity_type == "telegram":
                candidate_session_keys.append(f"telegram:{identity_value}")
            elif identity_type == "cli":
                candidate_session_keys.append("main")

        sessions: list[dict[str, Any]] = []
        seen: set[str] = set()
        for session_key in candidate_session_keys:
            if session_key in seen:
                continue
            seen.add(session_key)
            latest_path = await self.session_manager.latest_session_path(session_key)
            if latest_path is None:
                continue
            history = await self.session_manager.session_history(session_key, limit=max(1, self.config.context_engine.recent_session_message_limit))
            if not history:
                continue
            compact_messages: list[dict[str, str]] = []
            for message in history[-self.config.context_engine.recent_session_message_limit :]:
                role = str(message.get("role", "")).strip().lower()
                if role not in {"user", "assistant"}:
                    continue
                content = str(message.get("content", "")).strip()
                if not content:
                    continue
                compact_messages.append({"role": role, "content": self._truncate(content, 220)})
            if compact_messages:
                sessions.append({"session_key": session_key, "messages": compact_messages})
        return sessions[: self.config.context_engine.session_count_limit]

    async def _google_context(self, user_id: str) -> dict[str, Any]:
        token = await self.oauth_token_manager.get_token("google", user_id=user_id)
        if token is None or not str(token.get("access_token", "")).strip():
            return {"connected": False, "gmail_threads": [], "calendar_events": []}

        access_token = str(token["access_token"])
        try:
            gmail_threads = await self._fetch_gmail_threads(access_token)
        except Exception as exc:  # pragma: no cover - defensive runtime path
            self.logger.warning("context_engine_gmail_fetch_failed", error=str(exc))
            gmail_threads = []
        try:
            calendar_events = await self._fetch_calendar_events(access_token)
        except Exception as exc:  # pragma: no cover - defensive runtime path
            self.logger.warning("context_engine_calendar_fetch_failed", error=str(exc))
            calendar_events = []
        return {
            "connected": True,
            "gmail_threads": gmail_threads,
            "calendar_events": calendar_events,
        }

    async def _fetch_gmail_threads(self, access_token: str) -> list[dict[str, Any]]:
        max_results = max(1, self.config.context_engine.gmail_thread_limit)
        thread_refs = await self._google_request(
            access_token,
            "https://gmail.googleapis.com/gmail/v1/users/me/threads",
            params={"q": "in:inbox newer_than:14d", "maxResults": max_results},
        )
        threads: list[dict[str, Any]] = []
        for ref in (thread_refs.get("threads", []) or [])[:max_results]:
            thread_id = str(ref.get("id", "")).strip()
            if not thread_id:
                continue
            detail = await self._google_request(
                access_token,
                f"https://gmail.googleapis.com/gmail/v1/users/me/threads/{thread_id}",
                params={"format": "metadata", "metadataHeaders": ["Subject", "From", "Date", "To"]},
            )
            latest_message = (detail.get("messages", []) or [{}])[-1]
            headers = {
                str(item.get("name", "")): str(item.get("value", ""))
                for item in (latest_message.get("payload", {}) or {}).get("headers", [])
            }
            threads.append(
                {
                    "thread_id": thread_id,
                    "subject": headers.get("Subject", ""),
                    "from": headers.get("From", ""),
                    "date": headers.get("Date", ""),
                    "snippet": self._truncate(str(detail.get("snippet", "")), 180),
                }
            )
        return threads

    async def _fetch_calendar_events(self, access_token: str) -> list[dict[str, Any]]:
        max_results = max(1, self.config.context_engine.calendar_event_limit)
        response = await self._google_request(
            access_token,
            "https://www.googleapis.com/calendar/v3/calendars/primary/events",
            params={
                "singleEvents": "true",
                "orderBy": "startTime",
                "timeMin": datetime.now(timezone.utc).isoformat(),
                "maxResults": max_results,
            },
        )
        events: list[dict[str, Any]] = []
        for item in (response.get("items", []) or [])[:max_results]:
            start = item.get("start", {}) or {}
            end = item.get("end", {}) or {}
            events.append(
                {
                    "summary": str(item.get("summary", "")),
                    "start": str(start.get("dateTime") or start.get("date") or ""),
                    "end": str(end.get("dateTime") or end.get("date") or ""),
                    "location": self._truncate(str(item.get("location", "")), 120),
                }
            )
        return events

    async def _google_request(self, access_token: str, url: str, *, params: dict[str, Any]) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {access_token}"}
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers, params=params)
            response.raise_for_status()
            return response.json()

    async def _generate_insights(self, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        prompt = (
            "You are SonarBot's proactive life context engine. Review the structured life-state JSON and produce only "
            "high-signal insights worth proactively notifying the user about. Look for cross-source reasoning such as "
            "calendar + memory, recent promises + missed deadlines, repeated reminders, looming expirations, or inbox "
            "signals tied to planned work. Avoid generic summaries and avoid repeating low-value observations.\n\n"
            "Return strict JSON in the form {\"insights\": [{\"title\": str, \"body\": str, \"confidence\": float, "
            "\"urgency\": float, \"category\": str, \"fingerprint\": str}]}. If there is nothing worth surfacing, "
            "return {\"insights\": []}."
        )
        message = {
            "role": "user",
            "content": json.dumps(snapshot, ensure_ascii=False, indent=2),
        }
        parts: list[str] = []
        try:
            async for response in self.model_provider.complete(messages=[message], system=prompt, tools=[], stream=False):
                if response.text:
                    parts.append(response.text)
        except Exception as exc:  # pragma: no cover - defensive runtime path
            self.logger.warning("context_engine_llm_failed", error=str(exc))
            return []
        payload = self._parse_json_payload("".join(parts))
        insights = payload.get("insights", []) if isinstance(payload, dict) else []
        if not isinstance(insights, list):
            return []
        filtered: list[dict[str, Any]] = []
        for insight in insights:
            if not isinstance(insight, dict):
                continue
            try:
                confidence = float(insight.get("confidence", 0))
                urgency = float(insight.get("urgency", 0))
            except (TypeError, ValueError):
                continue
            if confidence < self.config.context_engine.min_confidence:
                continue
            if urgency < self.config.context_engine.min_urgency:
                continue
            title = str(insight.get("title", "")).strip()
            body = str(insight.get("body", "")).strip()
            if not title or not body:
                continue
            normalized = dict(insight)
            normalized["title"] = self._truncate(title, 80)
            normalized["body"] = self._truncate(body, 600)
            normalized["confidence"] = round(confidence, 3)
            normalized["urgency"] = round(urgency, 3)
            normalized["fingerprint"] = str(insight.get("fingerprint") or self._insight_fingerprint(insight))
            filtered.append(normalized)
        return filtered

    def _parse_json_payload(self, content: str) -> dict[str, Any]:
        candidate = content.strip()
        if not candidate:
            return {"insights": []}
        fenced = re.search(r"\{[\s\S]*\}", candidate)
        if fenced is not None:
            candidate = fenced.group(0)
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            self.logger.warning("context_engine_invalid_json", content_preview=candidate[:400])
            return {"insights": []}
        return payload if isinstance(payload, dict) else {"insights": []}

    async def _load_insight_history(self, user_id: str) -> list[dict[str, Any]]:
        path = self._insight_history_path(user_id)
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict) and isinstance(payload.get("sent"), list):
            return [item for item in payload["sent"] if isinstance(item, dict)]
        return []

    def _trim_history(self, history: list[dict[str, Any]]) -> dict[str, Any]:
        dedupe_cutoff = datetime.now(timezone.utc) - timedelta(days=self.config.context_engine.dedupe_days)
        kept: list[dict[str, Any]] = []
        for item in history:
            sent_at = self._parse_datetime(str(item.get("sent_at", "")))
            if sent_at is None or sent_at >= dedupe_cutoff:
                kept.append(item)
        return {"sent": kept[-200:]}

    def _already_notified(self, history: list[dict[str, Any]], fingerprint: str) -> bool:
        dedupe_cutoff = datetime.now(timezone.utc) - timedelta(days=self.config.context_engine.dedupe_days)
        for item in history:
            if str(item.get("fingerprint", "")) != fingerprint:
                continue
            sent_at = self._parse_datetime(str(item.get("sent_at", "")))
            if sent_at is not None and sent_at >= dedupe_cutoff:
                return True
        return False

    def _insight_fingerprint(self, insight: dict[str, Any]) -> str:
        provided = str(insight.get("fingerprint", "")).strip()
        if provided:
            return provided
        raw = json.dumps(
            {
                "title": str(insight.get("title", "")).strip().lower(),
                "body": str(insight.get("body", "")).strip().lower(),
                "category": str(insight.get("category", "")).strip().lower(),
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def _severity_for_urgency(self, urgency: float) -> str:
        if urgency >= 0.9:
            return "critical"
        if urgency >= 0.75:
            return "warning"
        return "info"

    def _in_quiet_hours(self, profile: dict[str, Any]) -> bool:
        start = str(profile.get("quiet_hours_start", "")).strip()
        end = str(profile.get("quiet_hours_end", "")).strip()
        if not start or not end:
            return False
        now = datetime.now().strftime("%H:%M")
        if start <= end:
            return start <= now <= end
        return now >= start or now <= end

    def _snapshot_path(self, user_id: str) -> Path:
        return self.snapshot_dir / f"{user_id}.json"

    def _insight_history_path(self, user_id: str) -> Path:
        return self.insights_dir / f"{user_id}.json"

    async def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_text, json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")

    def _truncate(self, value: str, limit: int) -> str:
        cleaned = re.sub(r"\s+", " ", value).strip()
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[: limit - 1].rstrip() + "…"

    def _parse_datetime(self, value: str) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
