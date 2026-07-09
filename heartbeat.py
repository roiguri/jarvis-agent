import asyncio
import datetime
import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")

logger = logging.getLogger(__name__)

HEARTBEAT_THREAD_ID = "heartbeat"

_scheduler: AsyncIOScheduler | None = None


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
    injected by the agent's build_system_prompt (scope='heartbeat'); this just
    issues the imperative and sends the reply if it isn't [NO_ACTION]."""
    from agent import ask_jarvis, get_heartbeat_ack
    from gateway.factory import default_user_channel
    from tools.core import append_notification_log

    now_israel = datetime.datetime.now(datetime.timezone.utc).astimezone(ISRAEL_TZ)
    today = now_israel.strftime("%Y-%m-%d")
    today_start = now_israel.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

    prompt = (
        "Run the scheduled heartbeat check now. Work through the tasks in "
        "HEARTBEAT.md (provided in your context); for each, read its state "
        "file and act only if it is due. If nothing is due, reply with "
        "exactly [NO_ACTION].\n\n"
        f"Then update today's daily log: write_memory('daily/daily_{today}.md', "
        f"<content>). Use get_chat_history(50, since='{today_start}') to fold "
        "today's user conversations in alongside heartbeat activity; if the "
        "file already exists, read it first and update rather than overwrite. "
        "Format: '## Conversations (today)' / '## Heartbeat Activity' / '## Notes'."
    )

    logger.info("Heartbeat: running agent turn")
    try:
        response = await asyncio.wait_for(
            asyncio.to_thread(ask_jarvis, prompt, HEARTBEAT_THREAD_ID, scope="heartbeat"),
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
        logger.warning("Heartbeat: no heartbeat_respond call this tick")
    else:
        logger.info(
            "Heartbeat: ack acted_tasks=%s notify=%s summary=%r",
            ack.get("acted_tasks"), ack.get("notify"), str(ack.get("summary", ""))[:200],
        )

    if response and not response.strip().startswith("[NO_ACTION]"):
        logger.info("Heartbeat: sending message to user")
        try:
            await default_user_channel().send_to_owner(response)
            await asyncio.to_thread(append_notification_log, "heartbeat", response)
        except Exception as e:
            logger.error("Heartbeat: failed to send message: %s", e)
    else:
        logger.info("Heartbeat: [NO_ACTION] — nothing to send")


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


