"""Automation services for cron, heartbeat, standing orders, and webhooks."""

from assistant.automation.delivery import NotificationDispatcher
from assistant.automation.desktop_watcher import DesktopAutomationWatcher
from assistant.automation.engine import AutomationEngine
from assistant.automation.models import (
    ApprovalRequest,
    AutomationEvent,
    AutomationRule,
    AutomationRun,
    DesktopAutomationRule,
    Notification,
    OneTimeReminder,
)
from assistant.automation.heartbeat import HeartbeatService
from assistant.automation.scheduler import AutomationScheduler
from assistant.automation.standing_orders import StandingOrdersManager
from assistant.automation.store import AutomationStore
from assistant.automation.webhook_handler import render_webhook_message, verify_webhook_signature

__all__ = [
    "ApprovalRequest",
    "AutomationEngine",
    "AutomationScheduler",
    "AutomationEvent",
    "AutomationRule",
    "AutomationRun",
    "DesktopAutomationRule",
    "DesktopAutomationWatcher",
    "HeartbeatService",
    "Notification",
    "NotificationDispatcher",
    "OneTimeReminder",
    "StandingOrdersManager",
    "AutomationStore",
    "render_webhook_message",
    "verify_webhook_signature",
]
