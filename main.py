import asyncio
import logging
import os
import subprocess
import uvicorn
from datetime import datetime, timezone
from dotenv import load_dotenv
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.date import DateTrigger

from agent import ask_jarvis, ask_jarvis_once
from heartbeat import init_scheduler, run_heartbeat, fire_reminder
from tools.core import _load_events
from gateway.base import InboundMessage
from gateway.commands import try_handle_command
from gateway.factory import build_telegram_stack, default_outbox, default_owner_thread_id
from gateway.webhook.notifier import MediaNotificationManager
from gateway.webhook.server import create_webhook_app
from tools.core import (
    append_chat_log,
    async_append_notification_log,
    trim_log,
    CHAT_LOG,
    NOTIFICATION_LOG,
)
from observability import TURNS_LOG, TOOL_CALLS_LOG

# 1. Enable logging for debugging and monitoring
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
# Suppress noisy polling and ASGI request logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# 2. Load environment variables securely. Channel-specific config (bot token,
# owner id) is read by the channel factory, not here.
load_dotenv("/app/secrets/.env")


async def process_inbound_message(inbound: InboundMessage) -> str | None:
    """Domain-level processing: chat history + agent call. No channel-specific parsing here."""
    command_reply = await try_handle_command(inbound)
    if command_reply is not None:
        await asyncio.to_thread(append_chat_log, "user", inbound.user_text, inbound.thread_id)
        await asyncio.to_thread(append_chat_log, "assistant", command_reply, inbound.thread_id)
        return command_reply

    await asyncio.to_thread(
        append_chat_log,
        "user",
        inbound.user_text,
        inbound.thread_id,
        media_paths=[a.get("path") for a in inbound.attachments if a.get("path")],
    )

    final_response = await asyncio.to_thread(
        ask_jarvis,
        inbound.user_text,
        inbound.thread_id,
        media_attachments=[
            {
                "kind": a.get("kind"),
                "path": a.get("path"),
                "mime_type": a.get("mime_type"),
            }
            for a in inbound.attachments
            if a.get("kind") and a.get("path")
        ]
        or None,
    )

    if final_response:
        await asyncio.to_thread(append_chat_log, "assistant", final_response, inbound.thread_id)
    return final_response


def _running_provenance() -> dict:
    """Git identity of the tree this process is running from — for the startup
    log, so the journal always answers 'what code is prod on?'. Never raises:
    any failure (git absent, not a repo) degrades to 'unknown' rather than
    blocking startup. Returns fields; main() formats them.

    (instance name + memory/data roots join this in slices 2-3, once config.py
    exists — see docs/plans/STAGING_AND_DEPLOY.md.)"""
    repo = os.path.dirname(os.path.abspath(__file__))

    def _git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", repo, *args],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()

    try:
        # --exact-match exits non-zero (empty stdout) when HEAD is not on a tag.
        tag = _git("describe", "--tags", "--exact-match", "HEAD")
        return {
            "branch": _git("rev-parse", "--abbrev-ref", "HEAD") or "unknown",
            "sha": _git("rev-parse", "--short", "HEAD") or "unknown",
            "dirty": bool(_git("status", "--porcelain")),
            "subject": _git("log", "-1", "--format=%s") or "unknown",
            "date": _git("log", "-1", "--format=%cs") or "unknown",  # %cs = YYYY-MM-DD
            "deploy": tag if tag.startswith("deploy-") else "none",
        }
    except Exception:
        return {"branch": "unknown", "sha": "unknown", "dirty": False,
                "subject": "unknown", "date": "unknown", "deploy": "none"}


def _provenance_block(p: dict) -> str:
    """Multi-line, labelled startup readout — legible in the journal/terminal tail.

    STABLE LOG ANCHOR: the ``Running code:`` header is a contract, not just a
    label — ``scripts/jrestart.sh`` greps it to capture the block, and it is the
    last startup line by design. Do not rename it or move it off the tail;
    adding labelled rows below is fine (slices 2-3 do exactly that)."""
    sha = p["sha"] + (" (uncommitted)" if p["dirty"] else "")
    return (
        "Running code:\n"
        f"    branch : {p['branch']} @ {sha}\n"
        f"    commit : {p['subject']} — {p['date']}\n"
        f"    deploy : {p['deploy']}"
    )


async def main() -> None:
    """
    Starts the channel stack and the FastAPI webhook server inside a single
    asyncio event loop, so they share the stack without inter-process
    communication.
    """
    provenance = _running_provenance()
    logger.info("Starting Jarvis...")
    # Compact identity early, so a startup that crashes before 'online' still
    # says what code it was. The full block is logged last (tail-visible).
    logger.info(
        "Running: %s @ %s%s",
        provenance["branch"], provenance["sha"],
        " (uncommitted)" if provenance["dirty"] else "",
    )

    for log_path in (CHAT_LOG, NOTIFICATION_LOG, TURNS_LOG, TOOL_CALLS_LOG):
        trim_log(log_path)
        logger.info("Log trimmed: %s", log_path)

    async def on_confirmation_outcome(system_text: str) -> None:
        """Feed a resolved confirmation back through the agent on the owner's
        thread so Jarvis acknowledges it conversationally, then reply."""
        thread_id = default_owner_thread_id()
        try:
            await asyncio.to_thread(append_chat_log, "user", system_text, thread_id)
            reply = await asyncio.to_thread(ask_jarvis, system_text, thread_id)
            if reply:
                await asyncio.to_thread(append_chat_log, "assistant", reply, thread_id)
                # Conversational reply, already chat-logged — no notification event.
                await default_outbox().notify_owner(reply)
        except Exception:
            logger.exception("Failed to deliver confirmation outcome")

    # Build and wire the channel stack (channel + router + confirmation
    # store/UI + outbox + host). Registers the defaults for proactive sends
    # and the confirmation backend for destructive tools; reads its own
    # config env.
    stack = build_telegram_stack(
        on_message=process_inbound_message,
        on_confirmation_outcome=on_confirmation_outcome,
        log_sink=async_append_notification_log,
    )

    # Media notifications go through the stack's Outbox (send + log-on-success).
    async def _llm_format(prompt: str) -> str:
        return await asyncio.to_thread(ask_jarvis_once, prompt)

    notifier = MediaNotificationManager(stack.outbox, llm_format=_llm_format)

    # FastAPI app wired to the shared notifier
    fastapi_app = create_webhook_app(notifier)
    webhook_server = uvicorn.Server(
        uvicorn.Config(fastapi_app, host="0.0.0.0", port=8000, log_level="warning")
    )

    # Init APScheduler and register the heartbeat interval job before the
    # channel comes up, so an inbound turn can never observe a missing
    # scheduler. Jobs don't run until scheduler.start() below.
    HEARTBEAT_INTERVAL_HOURS = 1
    scheduler = init_scheduler()
    scheduler.add_job(
        run_heartbeat,
        IntervalTrigger(hours=HEARTBEAT_INTERVAL_HOURS),
        id="heartbeat",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Channel up: binds the outbox loop and starts inbound handling, so
    # everything after this point (past-due reminders, scheduler jobs) can send.
    await stack.start()

    try:
        # Restore pending reminders from file (wakeups are handled via HEARTBEAT.md)
        # Hold references to past-due fire tasks so they can't be GC'd mid-flight.
        past_due_tasks: list[asyncio.Task] = []
        for event in _load_events().get("events", []):
            if event.get("type") != "reminder":
                continue
            fire_at_dt = datetime.fromisoformat(event["fire_at"])
            if fire_at_dt > datetime.now(timezone.utc):
                scheduler.add_job(
                    fire_reminder,
                    DateTrigger(run_date=fire_at_dt),
                    id=f"event_{event['id']}",
                    args=[event],
                    replace_existing=True,
                )
                logger.info("Restored reminder %s for %s", event["id"], fire_at_dt)
            else:
                # Past-due: fire_reminder annotates the message with the original time
                past_due_tasks.append(asyncio.create_task(fire_reminder(event)))
                logger.info("Past-due reminder %s — firing with original time annotation", event["id"])

        scheduler.start()
        logger.info("Scheduler started. Heartbeat interval: %dh.", HEARTBEAT_INTERVAL_HOURS)
        logger.info("Jarvis is online. Channel active. Webhook server on :8000.")
        # The full provenance block is the LAST startup line, so `journalctl -n`/
        # `systemctl status` show it in the tail without scrolling past boot noise.
        logger.info("%s", _provenance_block(provenance))

        # server.serve() blocks until SIGINT/SIGTERM, then exits gracefully
        await webhook_server.serve()
    finally:
        logger.info("Shutdown signal received. Stopping channel...")
        scheduler.shutdown(wait=False)
        await stack.stop()

    logger.info("Jarvis shut down cleanly.")


if __name__ == "__main__":
    asyncio.run(main())
