import asyncio
import datetime
import logging
from timeutils import ISRAEL_TZ

from apscheduler.schedulers.asyncio import AsyncIOScheduler

import heartbeat_state


logger = logging.getLogger(__name__)

HEARTBEAT_THREAD_ID = "heartbeat"

_scheduler: AsyncIOScheduler | None = None

# Start time of the last tick that reached the model — guards against
# back-to-back turns if ticks ever fire in quick succession.
_MIN_TICK_SPACING = datetime.timedelta(seconds=30)
_last_tick_start: datetime.datetime | None = None

# A reminder whose send fails is kept and retried; past the cap it is dropped
# so a permanently failing send can't reschedule itself forever.
_REMINDER_RETRY_DELAY = datetime.timedelta(minutes=5)
_REMINDER_MAX_RETRIES = 3


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
    issues the imperative and delivers per the heartbeat_respond ack.

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
    from gateway.factory import default_outbox
    from gateway.outbox import EVENT_HEARTBEAT
    from turn_budget import FAILED, TurnOutcome

    now_israel = now_utc.astimezone(ISRAEL_TZ)
    today = now_israel.strftime("%Y-%m-%d")

    prompt = (
        "Run the scheduled heartbeat check now. Work the due tasks shown in "
        "the HEARTBEAT.md section of your context, following the heartbeat "
        "rules above it.\n\n"
        f"Today's daily log file: daily/daily_{today}.md."
    )

    logger.info("Heartbeat: running agent turn")
    try:
        outcome = await asyncio.wait_for(
            asyncio.to_thread(
                ask_jarvis, prompt, HEARTBEAT_THREAD_ID,
                scope="heartbeat", heartbeat_due_tasks=due_names,
            ),
            timeout=90,
        )
    except asyncio.TimeoutError:
        logger.error("Heartbeat: agent turn timed out after 90s — skipping")
        await _notify_tick_failed(
            TurnOutcome(FAILED, "", cause="it timed out after 90s"),
            due_names, now_israel,
        )
        return
    if not outcome.finished:
        logger.error("Heartbeat: agent turn ended %s: %s", outcome.kind, outcome.cause)

    # Structured tick-ack: delivery and stamping key off it.
    try:
        ack = await asyncio.to_thread(get_heartbeat_ack, HEARTBEAT_THREAD_ID)
    except Exception:
        logger.exception("Heartbeat: failed to read heartbeat_respond ack")
        ack = None
    acted: list[str] = []
    if ack is None:
        logger.warning("Heartbeat: no heartbeat_respond call this tick — not stamping")
        # A tick that broke before acking is the one silence the owner must
        # hear about; a finished tick that merely forgot the ack is not.
        if not outcome.finished:
            await _notify_tick_failed(outcome, due_names, now_israel)
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

    # Delivery: the ack decides what Roi sees — a tick without one (already
    # warned above) delivers nothing and its tasks re-run next tick.
    text = ack.get("notification_text", "") if ack else ""
    deliver = bool(ack and ack.get("notify") and text)
    delivered_ok = True
    if deliver:
        logger.info("Heartbeat: sending message to user")
        sent = await default_outbox().notify_owner(text, event=EVENT_HEARTBEAT)
        delivered_ok = sent.ok
        if not sent.ok:
            logger.error("Heartbeat: failed to send message: %s", sent.error)
    else:
        logger.info("Heartbeat: nothing to send")

    # Stamping happens only after delivery is settled: a failed send leaves
    # the acted tasks unstamped so they come due again next tick and the
    # notification gets another chance, instead of being silently dropped.
    if acted:
        if not delivered_ok:
            logger.warning(
                "Heartbeat: delivery failed — not stamping %s; tasks re-run next tick",
                acted,
            )
        else:
            # Code-owned last_run stamps, advanced only for tasks the agent
            # reported acting on. Stamp with the tick's start time (when the
            # gate decided), not completion time — the turn's duration must
            # not shift the task's schedule.
            try:
                stamped = await asyncio.to_thread(
                    heartbeat_state.stamp, acted, now_utc
                )
                logger.info("Heartbeat: stamped last_run for %s", stamped)
            except Exception:
                logger.exception("Heartbeat: failed to stamp last_run state")


async def _notify_tick_failed(
    outcome, due_names: list[str] | None, now_israel: datetime.datetime
) -> None:
    """Tell the owner a tick broke. Built in code, not by the model — the model
    may be what failed. Sent with an event so it is logged and mirrored into
    the owner thread, where the chat side learns of the failure as history."""
    from gateway.factory import default_outbox
    from gateway.outbox import EVENT_HEARTBEAT_FAILED
    from turn_budget import committed_summary

    tasks = ", ".join(due_names) if due_names else "all tasks"
    text = (
        f"Heartbeat check at {now_israel.strftime('%H:%M')} Israel time didn't finish: "
        f"{outcome.cause}. Tasks: {tasks}."
    )
    if outcome.committed_calls:
        text += f" Already done before it stopped: {committed_summary(outcome.committed_calls)}."
    text += " They run again on the next tick while still due."
    result = await default_outbox().notify_owner(text, event=EVENT_HEARTBEAT_FAILED)
    if not result.ok:
        logger.error("Heartbeat: failed to send the tick-failure notice: %s", result.error)


async def fire_reminder(event: dict) -> None:
    """Send the reminder text directly. No LLM. The event is removed from the
    events file only after a successful send; a failed send is retried a few
    times, and the persisted event survives a restart either way."""
    from apscheduler.triggers.date import DateTrigger
    from gateway.factory import default_outbox
    from gateway.outbox import EVENT_REMINDER
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
    outcome = await default_outbox().notify_owner(text, event=EVENT_REMINDER)
    if outcome.ok:
        await asyncio.to_thread(_remove_event, event["id"])
        logger.info("Heartbeat: reminder id=%s removed from events file", event.get("id"))
        return

    retries = int(event.get("retries", 0))
    if retries >= _REMINDER_MAX_RETRIES:
        logger.error(
            "Heartbeat: reminder id=%s undeliverable after %d retries (%s) — dropping",
            event.get("id"), retries, outcome.error,
        )
        await asyncio.to_thread(_remove_event, event["id"])
        return

    retry_at = datetime.datetime.now(datetime.timezone.utc) + _REMINDER_RETRY_DELAY
    get_scheduler().add_job(
        fire_reminder,
        DateTrigger(run_date=retry_at),
        id=f"event_{event['id']}",
        args=[{**event, "retries": retries + 1}],
        replace_existing=True,
    )
    logger.warning(
        "Heartbeat: reminder id=%s send failed (%s) — retry %d/%d at %s",
        event.get("id"), outcome.error, retries + 1, _REMINDER_MAX_RETRIES,
        retry_at.isoformat(timespec="seconds"),
    )


