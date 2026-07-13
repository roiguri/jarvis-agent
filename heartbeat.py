import asyncio
import datetime
import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

import heartbeat_state

ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")

logger = logging.getLogger(__name__)

HEARTBEAT_THREAD_ID = "heartbeat"

_scheduler: AsyncIOScheduler | None = None

# Start time of the last tick that reached the model — guards against
# back-to-back turns if ticks ever fire in quick succession.
_MIN_TICK_SPACING = datetime.timedelta(seconds=30)
_last_tick_start: datetime.datetime | None = None


def init_scheduler() -> AsyncIOScheduler:
    global _scheduler
    _scheduler = AsyncIOScheduler()
    return _scheduler


def get_scheduler() -> AsyncIOScheduler:
    if _scheduler is None:
        raise RuntimeError("Scheduler not initialized — call init_scheduler() first")
    return _scheduler


async def run_heartbeat() -> None:
    """Periodic agent turn. HEARTBEAT.md + recent daily logs + tick rules are
    injected by the agent's build_system_prompt (scope='heartbeat'); this
    issues the imperative and delivers per the heartbeat_respond ack
    (reply text is the fallback when the ack is missing).

    A model turn only happens when at least one task is cadence-due per the
    code-owned last_run state. The gate fails open: any error in it runs the
    model rather than silently killing the heartbeat."""
    global _last_tick_start

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    try:
        due, due_names = await asyncio.to_thread(heartbeat_state.any_due, now_utc)
    except Exception:
        logger.exception("Heartbeat: due-gate failed — running the model (fail open)")
        due, due_names = True, None
    if not due:
        logger.info("Heartbeat: nothing due — skipping model turn")
        return
    if _last_tick_start is not None and (now_utc - _last_tick_start) < _MIN_TICK_SPACING:
        logger.info("Heartbeat: last tick started <%ss ago — deferring",
                    int(_MIN_TICK_SPACING.total_seconds()))
        return
    _last_tick_start = now_utc
    logger.info("Heartbeat: due tasks: %s",
                "unknown (running all)" if due_names is None else due_names)

    from agent import ask_jarvis, get_heartbeat_ack
    from gateway.factory import default_user_channel
    from tools.core import append_notification_log

    now_israel = now_utc.astimezone(ISRAEL_TZ)
    today = now_israel.strftime("%Y-%m-%d")
    today_start = now_israel.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

    prompt = (
        "Run the scheduled heartbeat check now. Work the due tasks shown in "
        "the HEARTBEAT.md section of your context, following the heartbeat "
        "rules above it.\n\n"
        f"Today's daily log file: daily/daily_{today}.md. For today's chat "
        f"use get_chat_history(50, since='{today_start}')."
    )

    logger.info("Heartbeat: running agent turn")
    try:
        response = await asyncio.wait_for(
            asyncio.to_thread(
                ask_jarvis, prompt, HEARTBEAT_THREAD_ID,
                scope="heartbeat", heartbeat_due_tasks=due_names,
            ),
            timeout=90,
        )
    except asyncio.TimeoutError:
        logger.error("Heartbeat: agent turn timed out after 90s — skipping")
        return
    except Exception as e:
        logger.error("Heartbeat: agent turn failed: %s", e)
        return

    # Structured tick-ack: log what the model reports acting on. Message
    # delivery below still keys off the reply text.
    try:
        ack = await asyncio.to_thread(get_heartbeat_ack, HEARTBEAT_THREAD_ID)
    except Exception:
        logger.exception("Heartbeat: failed to read heartbeat_respond ack")
        ack = None
    if ack is None:
        logger.warning("Heartbeat: no heartbeat_respond call this tick — not stamping")
    else:
        logger.info(
            "Heartbeat: ack acted_tasks=%s notify=%s summary=%r",
            ack.get("acted_tasks"), ack.get("notify"), str(ack.get("summary", ""))[:200],
        )
        acted = ack.get("acted_tasks") or []
        # Only tasks that were actually due this tick may advance their
        # stamp — an ack echoing some other task (e.g. from thread history)
        # must not shift that task's schedule. When the gate failed open
        # (due_names is None) there is no due list to check against.
        if due_names is not None:
            rogue = [n for n in acted if n not in due_names]
            if rogue:
                logger.warning(
                    "Heartbeat: ack named non-due task(s) %s — not stamping those",
                    rogue,
                )
                acted = [n for n in acted if n in due_names]
        if acted:
            # Code-owned last_run stamps, advanced only for tasks the agent
            # reported acting on. The agent's own last_run: line in the notes
            # files keeps being written in parallel for cross-checking.
            try:
                # Stamp with the tick's start time (when the gate decided),
                # not completion time — the turn's duration must not shift
                # the task's schedule.
                stamped = await asyncio.to_thread(
                    heartbeat_state.stamp, acted, now_utc
                )
                logger.info("Heartbeat: stamped last_run for %s", stamped)
            except Exception:
                logger.exception("Heartbeat: failed to stamp last_run state")

    # Delivery: the ack is authoritative — notify/notification_text decide what
    # Roi sees; the reply text matters only when the ack is missing (already
    # warned above), so a dropped ack degrades to the old reply-keyed behavior
    # rather than losing a message.
    if ack is not None:
        text = ack.get("notification_text", "")
        deliver = bool(ack.get("notify") and text)
        source = "ack"
    else:
        # TODO(#27): remove this reply-text fallback (and the [NO_ACTION]
        # reply contract in prompts/heartbeat.md) once logs show it never
        # fires.
        text = response or ""
        deliver = bool(text and not text.strip().startswith("[NO_ACTION]"))
        source = "reply-text fallback"
    if deliver:
        logger.info("Heartbeat: sending message to user (%s)", source)
        try:
            await default_user_channel().send_to_owner(text)
            await asyncio.to_thread(append_notification_log, "heartbeat", text)
        except Exception as e:
            logger.error("Heartbeat: failed to send message: %s", e)
    else:
        logger.info("Heartbeat: nothing to send (%s)", source)


async def fire_reminder(event: dict) -> None:
    """Send the reminder text directly. No LLM. Remove from events file."""
    from gateway.factory import default_user_channel
    from tools.core import append_notification_log
    from tools.core import _remove_event

    text = event.get("text", "(reminder)")
    fire_at_str = event.get("fire_at", "")
    if fire_at_str:
        try:
            scheduled = datetime.datetime.fromisoformat(fire_at_str)
            delay = datetime.datetime.now(datetime.timezone.utc) - scheduled
            if delay.total_seconds() > 60:
                scheduled_local = scheduled.astimezone(ISRAEL_TZ).strftime("%H:%M Israel time")
                text = f"[Originally scheduled for {scheduled_local}]\n{text}"
        except Exception:
            pass

    logger.info("Heartbeat: firing reminder id=%s fire_at=%s text=%r",
                event.get("id"), event.get("fire_at"), text[:80])
    try:
        await default_user_channel().send_to_owner(text)
        await asyncio.to_thread(append_notification_log, "reminder", text)
    except Exception as e:
        logger.error("Heartbeat: failed to send reminder: %s", e)
    await asyncio.to_thread(_remove_event, event["id"])
    logger.info("Heartbeat: reminder id=%s removed from events file", event.get("id"))


