"""Notification delivery helpers for automation results."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from typing import Any

from assistant.automation.models import Notification


class NotificationDispatcher:
    def __init__(self, config, store, user_profiles, connection_manager) -> None:
        self.config = config
        self.store = store
        self.user_profiles = user_profiles
        self.connection_manager = connection_manager

    async def dispatch(self, notification: Notification) -> Notification:
        await self.store.create_notification(notification)
        profile = await self.user_profiles.get_profile(notification.user_id)
        delivery_channels = await self._resolve_channels(notification, profile)
        delivered = False

        for channel_name in delivery_channels:
            if await self._deliver_to_channel(notification, channel_name):
                delivered = True

        if delivered:
            notification.status = "delivered"
            notification.updated_at = notification.updated_at
            await self.store.update_notification_status(notification.notification_id, "delivered")
            await self.store.mark_rule_notified(
                notification.user_id,
                str(notification.metadata.get("rule_name", notification.source)),
            )
        else:
            notification.status = "queued"
            await self.store.update_notification_status(notification.notification_id, "queued")

        await self.connection_manager.send_user_event(
            notification.user_id,
            "notification.created",
            self._notification_payload(notification),
            channel_name="webchat",
        )
        return notification

    async def _resolve_channels(self, notification: Notification, profile: dict[str, Any]) -> list[str]:
        primary = str(profile.get("primary_channel") or self.config.users.primary_channel)
        fallbacks = [str(item) for item in profile.get("fallback_channels", [])]
        quiet_mode = self._in_quiet_hours(profile)
        if quiet_mode and notification.metadata.get("delivery_policy", "primary") != "immediate":
            return ["webchat"]
        ordered = [primary, *fallbacks]
        result: list[str] = []
        for item in ordered:
            if item and item not in result:
                result.append(item)
        if "webchat" not in result:
            result.append("webchat")
        return result

    def _in_quiet_hours(self, profile: dict[str, Any]) -> bool:
        start = str(profile.get("quiet_hours_start", "")).strip()
        end = str(profile.get("quiet_hours_end", "")).strip()
        if not start or not end:
            return False
        now = datetime.now().strftime("%H:%M")
        if start <= end:
            return start <= now <= end
        return now >= start or now <= end

    async def _deliver_to_channel(self, notification: Notification, channel_name: str) -> bool:
        if channel_name == "telegram":
            identity = await self.user_profiles.get_identity(notification.user_id, "telegram")
            if identity is None:
                recipient = self._fallback_telegram_recipient()
                if not recipient:
                    await self.store.record_delivery(
                        notification.notification_id,
                        "telegram",
                        notification.user_id,
                        "failed",
                        "No linked Telegram identity for this user.",
                    )
                    return False
            else:
                recipient = str(identity.get("metadata", {}).get("chat_id") or identity.get("identity_value"))
            message_text = self._format_channel_message(notification, channel_name)
            try:
                await self.connection_manager.send_channel_message("telegram", recipient, message_text)
                await self.store.record_delivery(notification.notification_id, "telegram", recipient, "delivered")
                return True
            except Exception as exc:
                await self.store.record_delivery(notification.notification_id, "telegram", recipient, "failed", str(exc))
                return False

        if channel_name in {"windows", "windows-toast", "toast"}:
            recipient = "windows-toast"
            if sys.platform != "win32":
                await self.store.record_delivery(
                    notification.notification_id,
                    "windows",
                    recipient,
                    "failed",
                    "Windows toast notifications are only available on Windows.",
                )
                return False
            try:
                shown = await self._show_windows_toast(notification.title, notification.body)
            except Exception as exc:
                await self.store.record_delivery(notification.notification_id, "windows", recipient, "failed", str(exc))
                return False
            status = "delivered" if shown else "failed"
            await self.store.record_delivery(notification.notification_id, "windows", recipient, status)
            return shown

        if channel_name == "webchat":
            active = self.connection_manager.active_user_connections(notification.user_id, channel_name="webchat")
            recipient = ",".join(active) or "webchat-inbox"
            status = "delivered" if active else "queued"
            await self.store.record_delivery(notification.notification_id, "webchat", recipient, status)
            return bool(active)

        if channel_name == "cli":
            active = self.connection_manager.active_user_connections(notification.user_id, channel_name="cli")
            if not active:
                await self.store.record_delivery(notification.notification_id, "cli", "cli", "queued")
                return False
            sent = await self.connection_manager.send_user_event(
                notification.user_id,
                "notification.created",
                self._notification_payload(notification),
                channel_name="cli",
            )
            status = "delivered" if sent else "queued"
            await self.store.record_delivery(notification.notification_id, "cli", "cli", status)
            return bool(sent)

        return False

    def _notification_payload(self, notification: Notification) -> dict[str, Any]:
        return {
            "notification_id": notification.notification_id,
            "title": notification.title,
            "body": notification.body,
            "source": notification.source,
            "severity": notification.severity,
            "status": notification.status,
            "created_at": notification.created_at,
        }

    def _format_channel_message(self, notification: Notification, channel_name: str) -> str:
        body = str(notification.body).strip()
        if channel_name != "telegram":
            return body
        source = str(notification.source or "").strip().lower()
        if source == "context-engine":
            return f"[Life Context] {body}" if body else "[Life Context]"
        if source or notification.metadata.get("rule_name") or notification.metadata.get("event_id"):
            return f"[Automation] {body}" if body else "[Automation]"
        return body

    async def _show_windows_toast(self, title: str, body: str) -> bool:
        normalized_title = (title or "SonarBot").strip() or "SonarBot"
        normalized_body = (body or "").strip() or normalized_title
        script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$message = '" + self._ps_escape(normalized_body[:280]) + "'; "
            "$caption = '" + self._ps_escape(normalized_title) + "'; "
            "[void][System.Windows.Forms.MessageBox]::Show($message, $caption, [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Information, [System.Windows.Forms.MessageBoxDefaultButton]::Button1, [System.Windows.Forms.MessageBoxOptions]::DefaultDesktopOnly)"
        )
        process = await asyncio.create_subprocess_exec(
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await process.communicate()
        return process.returncode == 0 and not str((stderr or b"").decode("utf-8", errors="ignore")).strip()

    def _xml_escape(self, value: str) -> str:
        return (
            value.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;")
        )

    def _ps_escape(self, value: str) -> str:
        return value.replace("'", "''")

    def _fallback_telegram_recipient(self) -> str:
        allowed = getattr(self.config.telegram, "allowed_user_ids", []) or []
        if len(allowed) == 1:
            return str(allowed[0])
        return ""
