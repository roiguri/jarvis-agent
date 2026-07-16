"""
TelegramHost — owns the PTB Application lifecycle for the Telegram channel.

Everything python-telegram-bot lives inside this package: building the
Application from the bot token, wiring update handlers to the router and
confirmation UI, and starting/stopping polling. The host process (main.py)
only calls start()/stop() on the stack; it never sees a PTB type.

start() runs PTB's full manual bring-up (initialize -> start -> polling) and
stop() the corresponding teardown (updater.stop -> stop -> shutdown, which
`async with Application` would otherwise guarantee for initialize/shutdown),
so the host process can interleave its own startup (scheduler, webhook
server) between channel-up and shutdown.
"""

import asyncio
import logging

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

from gateway import outbox as outbox_mod
from gateway.channels.telegram.channel import TelegramChannel
from gateway.channels.telegram.confirmation import TelegramConfirmationUI
from gateway.channels.telegram.router import TelegramInboundRouter
from gateway.confirmation.store import InMemoryConfirmationStore

logger = logging.getLogger(__name__)


class TelegramHost:
    def __init__(
        self,
        token: str,
        channel: TelegramChannel,
        router: TelegramInboundRouter,
        confirmation_ui: TelegramConfirmationUI,
        store: InMemoryConfirmationStore,
    ) -> None:
        self._token = token
        self._channel = channel
        self._router = router
        self._confirmation_ui = confirmation_ui
        self._store = store
        self._application = None

    async def start(self) -> None:
        """Bring the Telegram channel fully up: build + initialize the PTB
        Application, attach the live bot, bind the shared outbox loop, start
        the confirmation sweeper, publish the command menu, begin polling."""
        application = ApplicationBuilder().token(self._token).build()
        # Slash commands flow through handle_text so the gateway's
        # try_handle_command can short-circuit them before the agent —
        # no separate CommandHandler needed.
        application.add_handler(MessageHandler(filters.TEXT, self._router.handle_text))
        application.add_handler(MessageHandler(filters.PHOTO, self._router.handle_photo))
        application.add_handler(MessageHandler(filters.VIDEO, self._router.handle_video))
        application.add_handler(MessageHandler(filters.VOICE, self._router.handle_voice))
        application.add_handler(CallbackQueryHandler(self._confirmation_ui.handle_callback))

        await application.initialize()
        # Track the app from initialize onward so a failure anywhere in the
        # rest of the bring-up still gets a proper PTB shutdown.
        self._application = application
        try:
            # attach + bind must happen after initialize, when application.bot
            # is live and we are inside the running loop.
            self._channel.attach(application.bot)
            outbox_mod.bind_loop(asyncio.get_running_loop())
            self._store.start_sweeper()
            await self._channel.register_command_menu()

            await application.start()
            await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        except Exception:
            await self.stop()
            raise
        logger.info("Telegram host started: polling active")

    async def stop(self) -> None:
        """Tear down whatever start() managed to bring up; safe to call after
        a partial start."""
        application = self._application
        if application is None:
            return
        self._application = None
        if application.updater and application.updater.running:
            await application.updater.stop()
        if application.running:
            await application.stop()
        await application.shutdown()
        logger.info("Telegram host stopped")
