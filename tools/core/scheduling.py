import json
import logging
import os
import tempfile
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from langchain_core.tools import tool

import config
from tools.registry import tool_register

ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")

EVENTS_PATH = os.path.join(config.DATA_DIR, "scheduling", "scheduled_events.json")

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# File helpers — atomic reads/writes for scheduled_events.json
# ---------------------------------------------------------------------------

def _load_events() -> dict:
    try:
        with open(EVENTS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"events": []}


def _save_events(data: dict) -> None:
    dir_ = os.path.dirname(EVENTS_PATH)
    os.makedirs(dir_, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=dir_, delete=False, encoding="utf-8") as tmp:
        json.dump(data, tmp, indent=2)
        tmp_path = tmp.name
    os.replace(tmp_path, EVENTS_PATH)


def _append_event(event: dict) -> None:
    data = _load_events()
    data["events"].append(event)
    _save_events(data)


def _remove_event(event_id: str) -> None:
    data = _load_events()
    data["events"] = [e for e in data["events"] if e.get("id") != event_id]
    _save_events(data)


# ---------------------------------------------------------------------------
# LangChain tools
# ---------------------------------------------------------------------------

@tool_register(namespace="core")
@tool
def manage_reminder(
    action: str,
    text: str | None = None,
    fire_at: str | None = None,
    reminder_id: str | None = None,
) -> str:
    """Create, list, or delete scheduled reminders.

    action='create': Schedule a reminder. Requires text and fire_at. The message is sent
        verbatim at the scheduled time — no LLM involved at fire time. Call exactly ONCE
        per reminder request; the response confirms the scheduled time so you can verify.
    action='list': Show all pending reminders with IDs, times, and text.
        Use before deleting, or when the user asks what reminders exist.
    action='delete': Cancel a reminder by ID. To modify a reminder: delete it, then
        create a new one with the updated details.

    Args:
        action: 'create', 'list', or 'delete'
        text: Reminder message (required for 'create')
        fire_at: ISO 8601 UTC datetime, e.g. '2026-05-08T09:00:00Z' (required for 'create')
        reminder_id: Short ID from 'list' output (required for 'delete')
    """
    from apscheduler.triggers.date import DateTrigger
    from heartbeat import get_scheduler, fire_reminder

    if action == "create":
        if not text or not fire_at:
            return "Error: create requires both text and fire_at."
        try:
            fire_at_dt = datetime.fromisoformat(fire_at.replace("Z", "+00:00"))
        except ValueError as e:
            return f"Error: invalid fire_at — {e}. Use ISO 8601 UTC, e.g. '2026-05-08T09:00:00Z'."
        if fire_at_dt <= datetime.now(timezone.utc):
            return "Error: fire_at must be in the future."

        event_id = str(uuid.uuid4())[:8]
        event = {"id": event_id, "type": "reminder", "text": text, "fire_at": fire_at_dt.isoformat()}
        _append_event(event)
        logger.info("manage_reminder create: id=%s fire_at=%s", event_id, fire_at_dt)

        try:
            get_scheduler().add_job(
                fire_reminder,
                DateTrigger(run_date=fire_at_dt),
                id=f"event_{event_id}",
                args=[event],
                replace_existing=True,
            )
        except Exception as e:
            _remove_event(event_id)
            return f"Error scheduling reminder: {e}"

        now_israel = datetime.now(ISRAEL_TZ)
        fire_israel = fire_at_dt.astimezone(ISRAEL_TZ)
        return (
            f"Reminder [{event_id}] scheduled for {fire_israel.strftime('%Y-%m-%d %H:%M Israel time')}: \"{text}\". "
            f"(Current time is {now_israel.strftime('%Y-%m-%d %H:%M Israel time')}. Do not call manage_reminder again for this request.)"
        )

    elif action == "list":
        data = _load_events()
        events = [e for e in data.get("events", []) if e.get("type") == "reminder"]
        if not events:
            return "No pending reminders."
        now = datetime.now(timezone.utc)
        lines = []
        for e in sorted(events, key=lambda x: x.get("fire_at", "")):
            fire_dt = datetime.fromisoformat(e["fire_at"])
            fire_israel = fire_dt.astimezone(ISRAEL_TZ)
            delta = fire_dt - now
            total_secs = delta.total_seconds()
            due_str = f"in {int(total_secs // 3600)}h {int((total_secs % 3600) // 60)}m" if total_secs > 0 else "overdue"
            lines.append(f"[{e['id']}] {fire_israel.strftime('%Y-%m-%d %H:%M Israel time')} ({due_str}): \"{e['text']}\"")
        return "\n".join(lines)

    elif action == "delete":
        if not reminder_id:
            return "Error: delete requires reminder_id. Use action='list' to see current IDs."
        data = _load_events()
        match = next((e for e in data.get("events", []) if e.get("id") == reminder_id), None)
        if not match:
            return f"No reminder found with id '{reminder_id}'. Use action='list' to see current IDs."
        _remove_event(reminder_id)
        try:
            get_scheduler().remove_job(f"event_{reminder_id}")
        except Exception:
            pass
        fire_at_str = match.get("fire_at", "")
        try:
            fire_israel = datetime.fromisoformat(fire_at_str).astimezone(ISRAEL_TZ).strftime("%Y-%m-%d %H:%M Israel time")
        except Exception:
            fire_israel = fire_at_str[:16].replace("T", " ")
        return f"Deleted reminder [{reminder_id}] scheduled for {fire_israel}: \"{match['text']}\"."

    else:
        return "Error: action must be 'create', 'list', or 'delete'."


