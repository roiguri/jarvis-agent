import asyncio
import datetime
import logging
from timeutils import ISRAEL_TZ

import heartbeat_state


logger = logging.getLogger(__name__)

HEARTBEAT_THREAD_ID = "heartbeat"

# Every turn on the heartbeat thread — hourly ticks and scheduled wakes — runs
# under this lock, one at a time. A turn that arrives while another runs waits
# for it instead of being dropped.
TURN_LOCK = asyncio.Lock()


async def run_heartbeat() -> None:
    """Periodic agent turn. HEARTBEAT.md + recent daily logs + tick rules are
    injected by the agent's build_system_prompt (scope='heartbeat'); this
    issues the imperative and delivers per the heartbeat_respond ack.

    A model turn only happens when at least one task is cadence-due per the
    code-owned last_run state. The gate fails open: any error in it runs the
    model rather than silently killing the heartbeat."""
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    try:
        due, due_names = await asyncio.to_thread(heartbeat_state.any_due, now_utc)
    except Exception:
        logger.exception("Heartbeat: due-gate failed — running the model (fail open)")
        due, due_names = True, None
    if not due:
        logger.info("Heartbeat: nothing due — skipping model turn")
        return
    logger.info("Heartbeat: due tasks: %s",
                "unknown (running all)" if due_names is None else due_names)

    async with TURN_LOCK:
        await _run_tick(now_utc, due_names)


async def _run_tick(now_utc: datetime.datetime, due_names: list[str] | None) -> None:
    from agent import ask_jarvis

    now_israel = now_utc.astimezone(ISRAEL_TZ)
    today = now_israel.strftime("%Y-%m-%d")

    prompt = (
        "Run the scheduled heartbeat check now. Work the due tasks shown in "
        "the HEARTBEAT.md section of your context, following the heartbeat "
        "rules above it.\n\n"
        f"Today's daily log file: daily/daily_{today}.md."
    )

    logger.info("Heartbeat: running agent turn")
    # Bounded by the heartbeat's turn budget inside the turn itself. Not
    # wrapped in asyncio.wait_for: that only stops waiting — the thread keeps
    # running, and the next tick could then start a second turn on this thread.
    outcome = await asyncio.to_thread(
        ask_jarvis, prompt, HEARTBEAT_THREAD_ID,
        scope="heartbeat", heartbeat_due_tasks=due_names,
    )
    if not outcome.finished:
        logger.error("Heartbeat: agent turn ended %s: %s", outcome.kind, outcome.cause)

    # Structured tick-ack: delivery and stamping key off it.
    ack = await _read_ack()
    acted: list[str] = []
    if ack is None:
        logger.warning("Heartbeat: no heartbeat_respond call this tick — not stamping")
        # A tick that broke before acking is the one silence the owner must
        # hear about; a finished tick that merely forgot the ack is not.
        if not outcome.finished:
            tasks = ", ".join(due_names) if due_names else "all tasks"
            await _notify_failed(
                f"Heartbeat check at {now_israel.strftime('%H:%M')} Israel time didn't finish: "
                f"{outcome.cause}. Tasks: {tasks}.",
                outcome,
                "They run again on the next tick while still due.",
            )
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
    delivered_ok = await _deliver(ack) is None

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


async def _read_ack() -> dict | None:
    from agent import get_heartbeat_ack

    try:
        return await asyncio.to_thread(get_heartbeat_ack, HEARTBEAT_THREAD_ID)
    except Exception:
        logger.exception("Heartbeat: failed to read heartbeat_respond ack")
        return None


async def _deliver(ack: dict | None, metadata: dict | None = None) -> str | None:
    """Send the ack's notification, if it asks for one. Returns the text when
    the send failed (so the caller can keep it), else None."""
    from gateway.factory import default_outbox
    from gateway.outbox import EVENT_HEARTBEAT

    text = ack.get("notification_text", "") if ack else ""
    if not (ack and ack.get("notify") and text):
        logger.info("Heartbeat: nothing to send")
        return None
    logger.info("Heartbeat: sending message to user")
    sent = await default_outbox().notify_owner(text, event=EVENT_HEARTBEAT, metadata=metadata)
    if sent.ok:
        return None
    logger.error("Heartbeat: failed to send message: %s", sent.error)
    return text


async def run_wake(trigger) -> None:
    """Run a scheduled wake: a heartbeat-scope turn on the heartbeat thread
    with the trigger's instruction, delivered per its heartbeat_respond ack.

    A wake runs at most once — it may already have acted, so a broken one is
    reported, not retried, and it leaves the store before its turn starts (a
    crash mid-turn loses it rather than re-running it on restart). Only a
    failed delivery is retried, as a plain send of the text the turn wrote."""
    from agent import ask_jarvis
    from triggers import runner, store
    from triggers.model import ORIGIN_OWNER, Send, Trigger

    async with TURN_LOCK:
        now_israel = datetime.datetime.now(ISRAEL_TZ)
        source = "set by the owner in chat" if trigger.origin == ORIGIN_OWNER else "set by you in an earlier background turn"
        prompt = (
            f"Scheduled wake [{trigger.id}], {source}. Work only this instruction, "
            "following the scheduled-wake rules above:\n\n"
            f"{trigger.action.instruction}"
        )
        await asyncio.to_thread(store.remove, trigger.id)
        logger.info("Heartbeat: running wake %s", trigger.id)
        outcome = await asyncio.to_thread(
            ask_jarvis, prompt, HEARTBEAT_THREAD_ID,
            scope="heartbeat", heartbeat_due_tasks=[], trigger=trigger,
        )
        if not outcome.finished:
            logger.error("Heartbeat: wake %s ended %s: %s", trigger.id, outcome.kind, outcome.cause)
        ack = await _read_ack()
        if ack is None:
            logger.warning("Heartbeat: wake %s left no heartbeat_respond call", trigger.id)
            if not outcome.finished:
                await _notify_failed(
                    f"Scheduled wake at {now_israel.strftime('%H:%M')} Israel time didn't finish: "
                    f"{outcome.cause}. It was: {trigger.action.instruction[:200]}",
                    outcome,
                    "It won't run again on its own.",
                )
            return
        logger.info("Heartbeat: wake %s ack notify=%s summary=%r",
                    trigger.id, ack.get("notify"), str(ack.get("summary", ""))[:200])
        undelivered = await _deliver(ack, metadata={"trigger": trigger.id})
        if undelivered is not None:
            retry = Trigger(trigger.id, trigger.when, Send(undelivered), trigger.origin, trigger.parent)
            await asyncio.to_thread(store.add, retry)
            logger.warning("Heartbeat: wake %s delivery failed — retrying as a send", trigger.id)
            runner.retry(retry, 0, "delivery failed")


async def _notify_failed(head: str, outcome, tail: str) -> None:
    """Tell the owner a heartbeat-thread turn broke. Built in code, not by the
    model — the model may be what failed. Sent as a heartbeat event so it is
    logged and mirrored into the owner thread, where the chat side learns of
    the failure as history; `tick_failed` marks the row apart from ordinary
    briefings."""
    from gateway.factory import default_outbox
    from gateway.outbox import EVENT_HEARTBEAT
    from turn_budget import committed_summary

    text = head
    if outcome.committed_calls:
        text += f" Already done before it stopped: {committed_summary(outcome.committed_calls)}."
    text += f" {tail}"
    result = await default_outbox().notify_owner(
        text, event=EVENT_HEARTBEAT, metadata={"tick_failed": True}
    )
    if not result.ok:
        logger.error("Heartbeat: failed to send the failure notice: %s", result.error)
