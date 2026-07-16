import os
import asyncio
import logging
import uvicorn
from datetime import datetime, timezone
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.date import DateTrigger

from agent import ask_jarvis, ask_jarvis_once
from heartbeat import init_scheduler, run_heartbeat, fire_reminder
from tools.core import _load_events
from gateway.base import InboundMessage
from gateway.commands import try_handle_command
from gateway import outbox as gateway_outbox
from gateway.factory import build_telegram_stack, default_outbox
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

# 2. Load environment variables securely
load_dotenv("/app/secrets/.env")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_ID = os.getenv("ALLOWED_USER_ID")

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN not found in /app/secrets/.env")
if not ALLOWED_USER_ID:
    raise ValueError("ALLOWED_USER_ID not found in /app/secrets/.env")


async def process_inbound_message(inbound: InboundMessage) -> str | None:
    """Domain-level processing: chat history + agent call. No Telegram-specific parsing here."""
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


async def main() -> None:
    """
    Starts both the Telegram bot and the FastAPI webhook server inside a single
    asyncio event loop. This allows them to share the channel stack without
    inter-process communication.
    """
    logger.info("Starting Jarvis...")

    for log_path in (CHAT_LOG, NOTIFICATION_LOG, TURNS_LOG, TOOL_CALLS_LOG):
        trim_log(log_path)
        logger.info("Log trimmed: %s", log_path)

    async def on_confirmation_outcome(system_text: str) -> None:
        """Feed a resolved confirmation back through the agent on the owner's
        thread so Jarvis acknowledges it conversationally, then reply."""
        thread_id = f"telegram_{int(ALLOWED_USER_ID)}"
        try:
            await asyncio.to_thread(append_chat_log, "user", system_text, thread_id)
            reply = await asyncio.to_thread(ask_jarvis, system_text, thread_id)
            if reply:
                await asyncio.to_thread(append_chat_log, "assistant", reply, thread_id)
                # Conversational reply, already chat-logged — no notification event.
                await default_outbox().notify_owner(reply)
        except Exception:
            logger.exception("Failed to deliver confirmation outcome")

    # Build and wire the Telegram channel stack (channel + router + confirmation
    # store/UI). Registers the default channel for proactive sends and the
    # confirmation backend for destructive tools.
    stack = build_telegram_stack(
        owner_id=int(ALLOWED_USER_ID),
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

    # Telegram bot using PTB's async context manager
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    # Slash commands flow through handle_text so the gateway's try_handle_command
    # can short-circuit them before the agent — no separate CommandHandler needed.
    application.add_handler(MessageHandler(filters.TEXT, stack.router.handle_text))
    application.add_handler(MessageHandler(filters.PHOTO, stack.router.handle_photo))
    application.add_handler(MessageHandler(filters.VIDEO, stack.router.handle_video))
    application.add_handler(MessageHandler(filters.VOICE, stack.router.handle_voice))
    application.add_handler(CallbackQueryHandler(stack.confirmation_ui.handle_callback))

    async with application:
        # Attach the live bot and bind the event loop — must happen inside
        # async with, after the Application is initialized, so application.bot
        # is ready. Then start the confirmation TTL sweeper.
        stack.channel.attach(application.bot)
        gateway_outbox.bind_loop(asyncio.get_running_loop())
        stack.store.start_sweeper()
        await stack.channel.register_command_menu()

        # Init APScheduler and register the heartbeat interval job
        HEARTBEAT_INTERVAL_HOURS = 1
        scheduler = init_scheduler()
        scheduler.add_job(
            run_heartbeat,
            IntervalTrigger(hours=HEARTBEAT_INTERVAL_HOURS),
            id="heartbeat",
            replace_existing=True,
            misfire_grace_time=3600,
        )

        # Restore pending reminders from file (wakeups are handled via HEARTBEAT.md)
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
                asyncio.create_task(fire_reminder(event))
                logger.info("Past-due reminder %s — firing with original time annotation", event["id"])

        scheduler.start()
        logger.info("Scheduler started. Heartbeat interval: %dh.", HEARTBEAT_INTERVAL_HOURS)

        await application.start()
        await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        logger.info("Jarvis is online. Telegram polling active. Webhook server on :8000.")

        # server.serve() blocks until SIGINT/SIGTERM, then exits gracefully
        await webhook_server.serve()

        logger.info("Shutdown signal received. Stopping Telegram polling...")
        scheduler.shutdown(wait=False)
        await application.updater.stop()
        await application.stop()

    logger.info("Jarvis shut down cleanly.")


if __name__ == "__main__":
    asyncio.run(main())
