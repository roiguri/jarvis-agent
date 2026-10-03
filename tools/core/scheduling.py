import logging
import uuid
from datetime import datetime, timezone
from timeutils import ISRAEL_TZ, owner_tz

from langchain_core.tools import tool

import turn_context
from tools.registry import tool_register
from triggers import scheduler, store
from triggers.model import At, ORIGIN_JARVIS, ORIGIN_OWNER, PARENT_TICK, Send, Trigger, Turn

logger = logging.getLogger(__name__)


def _owner_local_note(fire_at_dt: datetime) -> str:
    """Away-mode suffix (/tz): the same instant on the owner's own clock, so a
    wrong model conversion is visible without mental math. Empty at home, so
    every rendering stays byte-identical there."""
    tz = owner_tz()
    if tz is ISRAEL_TZ:
        return ""
    return f" ({fire_at_dt.astimezone(tz).strftime('%Y-%m-%d %H:%M')} {tz.key})"


# A background turn may keep at most this many of its own wakes pending.
MAX_PENDING_JARVIS_WAKES = 10


def _creator() -> tuple[str, str | None, str | None]:
    """(origin, parent, refusal) for a trigger created in the running turn.
    Read from the turn itself, never from model arguments."""
    if turn_context.current_scope() != "heartbeat":
        return ORIGIN_OWNER, None, None
    running = turn_context.current_trigger()
    if running is None:
        return ORIGIN_JARVIS, PARENT_TICK, None
    if running.origin == ORIGIN_JARVIS:
        return ORIGIN_JARVIS, running.id, (
            "Error: this wake was itself scheduled by a background turn, so it can't schedule "
            "another wake. Message the owner instead if a follow-up is needed."
        )
    return ORIGIN_JARVIS, running.id, None


def _local(dt: datetime) -> str:
    return dt.astimezone(ISRAEL_TZ).strftime("%Y-%m-%d %H:%M Israel time")


# ---------------------------------------------------------------------------
# LangChain tools
# ---------------------------------------------------------------------------

@tool_register(namespace="core")
@tool
def manage_trigger(
    action: str,
    at: str | None = None,
    message: str | None = None,
    instruction: str | None = None,
    trigger_id: str | None = None,
) -> str:
    """Create, list, or cancel one-off scheduled actions: reminders and wakes.

    action='create': Schedule one action at `at`. Give exactly one of:
        message: a REMINDER — this text is sent to the user verbatim at that time.
            No model runs. Use for a fixed nudge ("call the dentist").
        instruction: a WAKE — at that time you run a background turn with this
            instruction and your tools, and can message the user (or send a form).
            Use when the moment needs fresh data or judgment ("check whether the
            download finished and tell the user", "ask how the workout went").
        Call create exactly ONCE per request; the response confirms the scheduled
        time so you can verify.
    action='list': Show every pending reminder and wake with IDs and times.
        Use before cancelling, or when the user asks what is scheduled.
    action='cancel': Cancel one by ID. To change one: cancel it, then create anew.

    For RECURRING work use manage_heartbeat_task, not this tool.

    Args:
        action: 'create', 'list', or 'cancel'
        at: ISO 8601 UTC datetime, e.g. '2026-05-08T09:00:00Z' (required for 'create')
        message: Reminder text (create a reminder)
        instruction: What to do when woken (create a wake)
        trigger_id: Short ID from 'list' output (required for 'cancel')
    """
    if action == "create":
        if not at or bool(message) == bool(instruction):
            return "Error: create requires `at` and exactly one of `message` or `instruction`."
        try:
            at_dt = datetime.fromisoformat(at.replace("Z", "+00:00"))
        except ValueError as e:
            return f"Error: invalid `at` — {e}. Use ISO 8601 UTC, e.g. '2026-05-08T09:00:00Z'."
        if at_dt.tzinfo is None:
            return ("Error: `at` has no timezone offset, so the instant is ambiguous. "
                    "Use ISO 8601 UTC, e.g. '2026-05-08T09:00:00Z'.")
        if at_dt <= datetime.now(timezone.utc):
            return "Error: `at` must be in the future."

        origin, parent, refusal = _creator()
        if instruction:
            if refusal:
                return refusal
            if origin == ORIGIN_JARVIS:
                pending = sum(1 for t in store.all_triggers()
                              if isinstance(t.action, Turn) and t.origin == ORIGIN_JARVIS)
                if pending >= MAX_PENDING_JARVIS_WAKES:
                    return (f"Error: {pending} wakes from background turns are already pending "
                            f"(limit {MAX_PENDING_JARVIS_WAKES}). Cancel one first.")
            act, kind, body = Turn(instruction), "Wake", instruction
        else:
            act, kind, body = Send(message), "Reminder", message

        trigger_id = str(uuid.uuid4())[:8]
        trigger = Trigger(id=trigger_id, when=At(at_dt), action=act, origin=origin, parent=parent)
        store.add(trigger)
        logger.info("manage_trigger create: id=%s kind=%s at=%s origin=%s parent=%s",
                    trigger_id, kind.lower(), at_dt, origin, parent)
        try:
            scheduler.arm(trigger)
        except Exception as e:
            store.remove(trigger_id)
            return f"Error scheduling {kind.lower()}: {e}"

        return (
            f"{kind} [{trigger_id}] scheduled for {_local(at_dt)}{_owner_local_note(at_dt)}: \"{body}\". "
            f"(Current time is {_local(datetime.now(timezone.utc))}. "
            "Do not call manage_trigger again for this request.)"
        )

    elif action == "list":
        triggers = store.all_triggers()
        if not triggers:
            return "Nothing scheduled."
        now = datetime.now(timezone.utc)
        lines = []
        for t in sorted(triggers, key=lambda x: x.when.instant):
            total_secs = (t.when.instant - now).total_seconds()
            due_str = f"in {int(total_secs // 3600)}h {int((total_secs % 3600) // 60)}m" if total_secs > 0 else "overdue"
            if isinstance(t.action, Turn):
                what = f"wake: \"{t.action.instruction}\""
            else:
                what = f"reminder: \"{t.action.text}\""
            lines.append(f"[{t.id}] {_local(t.when.instant)}{_owner_local_note(t.when.instant)} ({due_str}) {what}")
        return "\n".join(lines)

    elif action == "cancel":
        if not trigger_id:
            return "Error: cancel requires trigger_id. Use action='list' to see current IDs."
        match = store.get(trigger_id)
        if not match:
            return f"Nothing scheduled with id '{trigger_id}'. Use action='list' to see current IDs."
        store.remove(trigger_id)
        scheduler.disarm(trigger_id)
        kind = "wake" if isinstance(match.action, Turn) else "reminder"
        return f"Cancelled {kind} [{trigger_id}] scheduled for {_local(match.when.instant)}."

    else:
        return "Error: action must be 'create', 'list', or 'cancel'."
