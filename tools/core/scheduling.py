import logging
import uuid
from datetime import datetime, timezone
from timeutils import ISRAEL_TZ, owner_tz

from langchain_core.tools import tool

from tools.registry import tool_register
from triggers import scheduler, store
from triggers.model import At, Send, Trigger

logger = logging.getLogger(__name__)


def _owner_local_note(fire_at_dt: datetime) -> str:
    """Away-mode suffix (/tz): the same instant on the owner's own clock, so a
    wrong model conversion is visible without mental math. Empty at home, so
    every rendering stays byte-identical there."""
    tz = owner_tz()
    if tz is ISRAEL_TZ:
        return ""
    return f" ({fire_at_dt.astimezone(tz).strftime('%Y-%m-%d %H:%M')} {tz.key})"


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
    if action == "create":
        if not text or not fire_at:
            return "Error: create requires both text and fire_at."
        try:
            fire_at_dt = datetime.fromisoformat(fire_at.replace("Z", "+00:00"))
        except ValueError as e:
            return f"Error: invalid fire_at — {e}. Use ISO 8601 UTC, e.g. '2026-05-08T09:00:00Z'."
        if fire_at_dt.tzinfo is None:
            return ("Error: fire_at has no timezone offset, so the instant is ambiguous. "
                    "Use ISO 8601 UTC, e.g. '2026-05-08T09:00:00Z'.")
        if fire_at_dt <= datetime.now(timezone.utc):
            return "Error: fire_at must be in the future."

        event_id = str(uuid.uuid4())[:8]
        trigger = Trigger(id=event_id, when=At(fire_at_dt), action=Send(text))
        store.add(trigger)
        logger.info("manage_reminder create: id=%s fire_at=%s", event_id, fire_at_dt)

        try:
            scheduler.arm(trigger)
        except Exception as e:
            store.remove(event_id)
            return f"Error scheduling reminder: {e}"

        now_israel = datetime.now(ISRAEL_TZ)
        fire_israel = fire_at_dt.astimezone(ISRAEL_TZ)
        return (
            f"Reminder [{event_id}] scheduled for {fire_israel.strftime('%Y-%m-%d %H:%M Israel time')}"
            f"{_owner_local_note(fire_at_dt)}: \"{text}\". "
            f"(Current time is {now_israel.strftime('%Y-%m-%d %H:%M Israel time')}. Do not call manage_reminder again for this request.)"
        )

    elif action == "list":
        triggers = store.all_triggers()
        if not triggers:
            return "No pending reminders."
        now = datetime.now(timezone.utc)
        lines = []
        for t in sorted(triggers, key=lambda x: x.when.instant):
            fire_dt = t.when.instant
            fire_israel = fire_dt.astimezone(ISRAEL_TZ)
            total_secs = (fire_dt - now).total_seconds()
            due_str = f"in {int(total_secs // 3600)}h {int((total_secs % 3600) // 60)}m" if total_secs > 0 else "overdue"
            lines.append(f"[{t.id}] {fire_israel.strftime('%Y-%m-%d %H:%M Israel time')}{_owner_local_note(fire_dt)} ({due_str}): \"{t.action.text}\"")
        return "\n".join(lines)

    elif action == "delete":
        if not reminder_id:
            return "Error: delete requires reminder_id. Use action='list' to see current IDs."
        match = store.get(reminder_id)
        if not match:
            return f"No reminder found with id '{reminder_id}'. Use action='list' to see current IDs."
        store.remove(reminder_id)
        scheduler.disarm(reminder_id)
        fire_israel = match.when.instant.astimezone(ISRAEL_TZ).strftime("%Y-%m-%d %H:%M Israel time")
        return f"Deleted reminder [{reminder_id}] scheduled for {fire_israel}: \"{match.action.text}\"."

    else:
        return "Error: action must be 'create', 'list', or 'delete'."


