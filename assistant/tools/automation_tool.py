"""Automation management tools — lets the LLM agent create, list, and delete
reminders and recurring cron jobs by calling the AutomationEngine directly.

Without this module the LLM has no callable tool for reminders/crons and
simply hallucinates success while nothing is actually persisted or scheduled.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from assistant.tools.registry import ToolDefinition


def build_automation_tools(automation_engine) -> list[ToolDefinition]:  # noqa: C901
    """Return tool definitions that wrap AutomationEngine reminder/cron APIs."""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _parse_run_at(value: str) -> datetime:
        """Parse an ISO datetime string or a natural-language offset like
        '10 minutes', '2 hours', 'tomorrow 9am', etc. and return a UTC datetime.
        """
        value = value.strip()
        now = datetime.now(timezone.utc)

        # ISO / standard formats first
        for fmt in (
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M%z",
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
        ):
            try:
                dt = datetime.strptime(value, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except ValueError:
                pass

        # Natural-language relative offsets e.g. "in 10 minutes", "2 hours", "after 3 hours"
        lower = value.lower().lstrip("in ").lstrip("after ")
        m = re.match(r"^(\d+(?:\.\d+)?)\s*(second|minute|hour|day|week)s?$", lower)
        if m:
            amount = float(m.group(1))
            unit = m.group(2)
            delta_map = {
                "second": timedelta(seconds=amount),
                "minute": timedelta(minutes=amount),
                "hour": timedelta(hours=amount),
                "day": timedelta(days=amount),
                "week": timedelta(weeks=amount),
            }
            return now + delta_map[unit]

        # "tomorrow [HH:MM]"
        if lower.startswith("tomorrow"):
            rest = lower[len("tomorrow"):].strip()
            base = now + timedelta(days=1)
            if rest:
                t = datetime.strptime(rest, "%H:%M") if ":" in rest else datetime.strptime(rest, "%I%p")
                return base.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
            return base.replace(hour=9, minute=0, second=0, microsecond=0)

        raise ValueError(
            f"Could not parse '{value}' as a datetime. Use ISO format (e.g. '2026-04-24T09:00:00+05:30') "
            "or a natural offset like '30 minutes', '2 hours', '1 day'."
        )

    def _natural_to_cron(value: str) -> str:
        """Convert a natural-language recurrence description to a 5-field cron expression.

        Examples:
          'every 2 minutes'  → '*/2 * * * *'
          'every hour'       → '0 * * * *'
          'every day at 9am' → '0 9 * * *'
          'every monday'     → '0 9 * * 1'
          '*/5 * * * *'      → passed through unchanged
        """
        value = value.strip()
        # Already a cron expression?
        if re.match(r"^[\d*/,\-]+(\s+[\d*/,\-]+){4}$", value):
            return value

        lower = value.lower()

        # "every N minutes"
        m = re.search(r"every\s+(\d+)\s+minute", lower)
        if m:
            return f"*/{m.group(1)} * * * *"

        # "every N hours"
        m = re.search(r"every\s+(\d+)\s+hour", lower)
        if m:
            return f"0 */{m.group(1)} * * *"

        # "every hour"
        if re.search(r"every\s+(hour|1\s*hour)", lower):
            return "0 * * * *"

        # "every minute"
        if "every minute" in lower:
            return "* * * * *"

        # "every day at HH:MM" or "daily at HH:MM" or "every day"
        m = re.search(r"(?:every\s+day|daily)(?:\s+at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?)?", lower)
        if m:
            hour_str = m.group(1) or "9"
            minute_str = m.group(2) or "0"
            ampm = m.group(3) or ""
            hour = int(hour_str)
            if ampm == "pm" and hour != 12:
                hour += 12
            elif ampm == "am" and hour == 12:
                hour = 0
            return f"{int(minute_str)} {hour} * * *"

        # Day-of-week
        dow_map = {"monday": "1", "tuesday": "2", "wednesday": "3", "thursday": "4",
                   "friday": "5", "saturday": "6", "sunday": "0"}
        for day_name, day_num in dow_map.items():
            if day_name in lower:
                m2 = re.search(r"at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", lower)
                if m2:
                    hour = int(m2.group(1))
                    minute = int(m2.group(2) or 0)
                    ampm = m2.group(3) or ""
                    if ampm == "pm" and hour != 12:
                        hour += 12
                    elif ampm == "am" and hour == 12:
                        hour = 0
                    return f"{minute} {hour} * * {day_num}"
                return f"0 9 * * {day_num}"

        # "every N days"
        m = re.search(r"every\s+(\d+)\s+days?", lower)
        if m:
            return f"0 9 */{m.group(1)} * *"

        raise ValueError(
            f"Could not convert '{value}' to a cron schedule. "
            "Try: 'every 5 minutes', 'every hour', 'every day at 9am', 'every Monday at 8am', "
            "or a 5-field cron string like '*/5 * * * *'."
        )

    # ------------------------------------------------------------------
    # Tool handlers
    # ------------------------------------------------------------------

    async def create_reminder(payload: dict[str, Any]) -> dict[str, Any]:
        """Create a one-time reminder that fires at a specific time."""
        user_id = str(payload.get("user_id", "default")).strip()
        message = str(payload.get("message", "")).strip()
        run_at_raw = str(payload.get("run_at", "")).strip()

        if not message:
            raise ValueError("Reminder message cannot be empty.")
        if not run_at_raw:
            raise ValueError("run_at is required. Provide an ISO datetime or a natural offset like '30 minutes'.")

        run_at_dt = _parse_run_at(run_at_raw)
        result = await automation_engine.create_one_time_reminder(user_id, run_at_dt, message)
        return {
            "status": "created",
            "reminder_id": result["reminder_id"],
            "message": result["message"],
            "run_at": result["run_at"],
            "confirmation": (
                f"✅ Reminder set! I'll remind you: \"{message}\" at "
                f"{run_at_dt.strftime('%Y-%m-%d %H:%M')} UTC. ID: {result['reminder_id']}"
            ),
        }

    async def create_recurring_reminder(payload: dict[str, Any]) -> dict[str, Any]:
        """Create a recurring cron-based reminder (repeating at a schedule)."""
        user_id = str(payload.get("user_id", "default")).strip()
        message = str(payload.get("message", "")).strip()
        schedule_raw = str(payload.get("schedule", "")).strip()

        if not message:
            raise ValueError("Reminder message cannot be empty.")
        if not schedule_raw:
            raise ValueError(
                "schedule is required. Examples: 'every 5 minutes', 'every hour', "
                "'every day at 9am', 'every Monday', or '*/5 * * * *'."
            )

        cron_schedule = _natural_to_cron(schedule_raw)
        result = await automation_engine.create_dynamic_cron_job(user_id, cron_schedule, message)
        return {
            "status": "created",
            "cron_id": result["cron_id"],
            "schedule": result["schedule"],
            "message": result["message"],
            "confirmation": (
                f"✅ Recurring reminder set! I'll remind you: \"{message}\" "
                f"on schedule '{cron_schedule}'. ID: {result['cron_id']}"
            ),
        }

    async def list_reminders(payload: dict[str, Any]) -> dict[str, Any]:
        """List all active one-time and recurring reminders for the user."""
        user_id = str(payload.get("user_id", "default")).strip()
        one_time = await automation_engine.list_one_time_reminders(user_id)
        recurring = await automation_engine.list_dynamic_cron_jobs(user_id)
        return {
            "one_time_reminders": [
                {
                    "reminder_id": str(r.get("reminder_id", "")),
                    "message": str(r.get("message", "")),
                    "run_at": str(r.get("run_at", "")),
                    "fired": bool(r.get("fired", False)),
                    "paused": bool(r.get("paused", False)),
                }
                for r in one_time
            ],
            "recurring_reminders": [
                {
                    "cron_id": str(r.get("cron_id", "")),
                    "message": str(r.get("message", "")),
                    "schedule": str(r.get("schedule", "")),
                    "paused": bool(r.get("paused", False)),
                }
                for r in recurring
            ],
            "total_one_time": len(one_time),
            "total_recurring": len(recurring),
        }

    async def delete_reminder(payload: dict[str, Any]) -> dict[str, Any]:
        """Delete a one-time reminder by its reminder_id."""
        user_id = str(payload.get("user_id", "default")).strip()
        reminder_id = str(payload.get("reminder_id", "")).strip()
        if not reminder_id:
            raise ValueError("reminder_id is required.")
        deleted = await automation_engine.delete_one_time_reminder(user_id, reminder_id)
        return {"deleted": deleted, "reminder_id": reminder_id}

    async def delete_recurring_reminder(payload: dict[str, Any]) -> dict[str, Any]:
        """Delete a recurring (cron) reminder by its cron_id."""
        user_id = str(payload.get("user_id", "default")).strip()
        cron_id = str(payload.get("cron_id", "")).strip()
        if not cron_id:
            raise ValueError("cron_id is required.")
        deleted = await automation_engine.delete_dynamic_cron_job(user_id, cron_id)
        return {"deleted": deleted, "cron_id": cron_id}

    # ------------------------------------------------------------------
    # Tool definitions
    # ------------------------------------------------------------------
    return [
        ToolDefinition(
            name="create_reminder",
            description=(
                "Create a one-time reminder that fires at a specific date/time and sends a notification message. "
                "Use this whenever the user asks to be reminded ONCE at a specific time. "
                "The run_at field accepts ISO datetimes or natural offsets like '30 minutes', '2 hours', '1 day', "
                "'tomorrow 9am', or '2026-04-25T09:00:00+05:30'."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "The reminder message the user will receive.",
                    },
                    "run_at": {
                        "type": "string",
                        "description": (
                            "When to fire the reminder. Accepts ISO 8601 datetime strings "
                            "(e.g. '2026-04-24T09:30:00+05:30') or natural offsets like "
                            "'30 minutes', '2 hours', '1 day', 'tomorrow 9am'."
                        ),
                    },
                    "user_id": {"type": "string", "description": "User ID (injected automatically)."},
                },
                "required": ["message", "run_at"],
            },
            handler=create_reminder,
        ),
        ToolDefinition(
            name="create_recurring_reminder",
            description=(
                "Create a RECURRING reminder that repeats on a cron schedule. "
                "Use this whenever the user asks to be reminded REPEATEDLY or on an interval. "
                "Examples: 'every 2 minutes', 'every hour', 'every day at 9am', 'every Monday at 8:30am', "
                "'*/5 * * * *'. The message is what the user will receive each time it fires."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "The reminder message the user will receive each time it fires.",
                    },
                    "schedule": {
                        "type": "string",
                        "description": (
                            "How often to repeat. Accepts natural language like 'every 2 minutes', "
                            "'every hour', 'every day at 9am', 'every Monday', or a 5-field cron "
                            "expression like '*/2 * * * *'."
                        ),
                    },
                    "user_id": {"type": "string", "description": "User ID (injected automatically)."},
                },
                "required": ["message", "schedule"],
            },
            handler=create_recurring_reminder,
        ),
        ToolDefinition(
            name="list_reminders",
            description=(
                "List all active one-time and recurring reminders for the current user."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "User ID (injected automatically)."},
                },
            },
            handler=list_reminders,
        ),
        ToolDefinition(
            name="delete_reminder",
            description="Delete a one-time reminder by its reminder_id.",
            parameters={
                "type": "object",
                "properties": {
                    "reminder_id": {"type": "string", "description": "The reminder_id to delete."},
                    "user_id": {"type": "string", "description": "User ID (injected automatically)."},
                },
                "required": ["reminder_id"],
            },
            handler=delete_reminder,
        ),
        ToolDefinition(
            name="delete_recurring_reminder",
            description="Delete a recurring (cron-based) reminder by its cron_id.",
            parameters={
                "type": "object",
                "properties": {
                    "cron_id": {"type": "string", "description": "The cron_id to delete."},
                    "user_id": {"type": "string", "description": "User ID (injected automatically)."},
                },
                "required": ["cron_id"],
            },
            handler=delete_recurring_reminder,
        ),
    ]
