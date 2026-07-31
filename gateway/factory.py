"""
Channel factory and the channel-agnostic accessors proactive code depends on.

Callers that need to reach the user without a chat_id (heartbeat, reminders,
confirmation outcomes) use default_outbox(); destructive tools use
get_confirmation(); domain code that must address the owner's conversation
thread uses default_owner_thread_id(). None of them imports a concrete
channel module.

The factory also owns the channel's config env: the bot token and the
owner-config value (ALLOWED_USER_ID for Telegram) are read here and nowhere
else — the host process never sees channel-specific configuration.
"""

import asyncio
import logging
import os
from dataclasses import dataclass, field

from typing import Awaitable, Callable, Protocol

import turn_context

from gateway import outbox as outbox_mod
from gateway.base import Channel, OnMessage
from gateway.confirmation.base import Confirmation
from gateway.confirmation.store import InMemoryConfirmationStore
from gateway.outbox import LogSink, Outbox
from gateway.channels.telegram.channel import TelegramChannel
from gateway.channels.telegram.confirmation import TelegramConfirmationUI
from gateway.channels.telegram.host import TelegramHost
from gateway.channels.telegram.router import TelegramInboundRouter
from gateway.channels.jarvis_app.channel import JarvisAppChannel
from gateway.channels.jarvis_app.client import HubClient
from gateway.channels.jarvis_app.confirmation import AppConfirmationUI
from gateway.channels.jarvis_app.router import JarvisAppInboundRouter

logger = logging.getLogger(__name__)


class Stack(Protocol):
    """What the host process needs from a built stack, regardless of channel:
    an outbox for proactive sends and a start/stop lifecycle. Concrete stacks
    (e.g. TelegramStack) carry channel-specific wiring beyond this."""

    outbox: Outbox

    async def start(self) -> None: ...
    async def stop(self) -> None: ...


@dataclass
class TelegramStack:
    channel: TelegramChannel
    router: TelegramInboundRouter
    store: InMemoryConfirmationStore
    confirmation_ui: TelegramConfirmationUI
    outbox: Outbox
    host: TelegramHost

    async def start(self) -> None:
        """Bring the channel fully up (PTB lifecycle, polling, sweeper)."""
        await self.host.start()

    async def stop(self) -> None:
        await self.host.stop()


@dataclass
class JarvisAppStack:
    channel: JarvisAppChannel
    client: HubClient
    router: JarvisAppInboundRouter
    store: InMemoryConfirmationStore
    confirmation_ui: AppConfirmationUI
    outbox: Outbox
    _task: "asyncio.Task | None" = field(default=None, init=False)

    async def start(self) -> None:
        """Bind the thread->loop bridge, start the confirmation TTL sweeper, then
        launch the poll-loop router as a background task; returns immediately.

        Telegram's host binds the bridge in its own start(); there is no
        equivalent host object here, and this stack may build and start before
        or without Telegram's, so bind explicitly rather than assume it already
        happened — bind_loop() to the same running loop is a no-op if it did.
        """
        outbox_mod.bind_loop(asyncio.get_running_loop())
        self.store.start_sweeper()
        self._task = asyncio.create_task(self.router.run())

    async def stop(self) -> None:
        """Signal the router to drain in-flight turns, await it, close the client."""
        self.router.request_stop()
        if self._task is not None:
            await self._task
        await self.client.aclose()


# Runtime channel registry for proactive routing. Populated when a stack is
# built; proactive sends resolve through it by name at call time, so the
# configured default can change without rebinding callers.
@dataclass
class _Registered:
    channel: Channel
    outbox: Outbox


_registry: dict[str, _Registered] = {}
_default_channel_name: str | None = None
# Confirmation stores keyed by channel name — kept on their own axis (not in
# _registry): a confirmation resolves by the turn's ORIGIN channel, a different
# lookup than the proactive default.
_confirmation_stores: dict[str, Confirmation] = {}


def register_channel(channel: Channel, outbox: Outbox) -> None:
    """Register a built channel and its outbox under channel.name."""
    _registry[channel.name] = _Registered(channel, outbox)


def set_default_channel(name: str) -> None:
    """Override the proactive default channel at runtime (otherwise
    JARVIS_DEFAULT_CHANNEL decides). Lets the default flip without a rebuild."""
    global _default_channel_name
    _default_channel_name = name


def _default_name() -> str:
    """The proactive default channel name: runtime override, else
    JARVIS_DEFAULT_CHANNEL, else 'telegram'. Read at call time — never baked in."""
    return _default_channel_name or os.getenv("JARVIS_DEFAULT_CHANNEL", "telegram")


def _default_entry() -> _Registered:
    name = _default_name()
    entry = _registry.get(name)
    if entry is None:
        raise RuntimeError(
            f"No channel registered as the proactive default ({name!r}); "
            f"registered: {sorted(_registry)}."
        )
    return entry


def default_owner_thread_id() -> str:
    """The agent thread id of the owner's conversation on the default channel.
    Origin-less, owner-addressed proactive code uses this; reactive traffic
    (chat replies, confirmations) routes to its own origin channel instead."""
    return _default_entry().channel.owner_thread_id


def default_outbox() -> Outbox:
    """The outbox owner-addressed proactive sends go through (heartbeat,
    reminders, media). Resolves the configured default channel at call time."""
    return _default_entry().outbox


def register_confirmation(name: str, store: Confirmation) -> None:
    """Register a channel's confirmation store under its channel name."""
    _confirmation_stores[name] = store


def _channel_name_for_thread(thread_id: str | None) -> str | None:
    """The channel a thread belongs to, by its '<name>_<id>' prefix, when that
    channel has a registered confirmation store. None for origin-less threads
    (e.g. the 'heartbeat' thread)."""
    if not thread_id:
        return None
    name = thread_id.split("_", 1)[0]
    return name if name in _confirmation_stores else None


def get_confirmation() -> Confirmation:
    """The confirmation store for the channel of the running turn (its origin),
    so a destructive tool's prompt and ack both stay on the channel the turn
    came from. Falls back to the default channel for origin-less turns
    (heartbeat). Destructive tools call this with no args; the origin is read
    from ambient turn context."""
    name = _channel_name_for_thread(turn_context.current_thread_id()) or _default_name()
    store = _confirmation_stores.get(name)
    if store is None:
        raise RuntimeError(
            f"No confirmation store registered for channel {name!r}; "
            f"registered: {sorted(_confirmation_stores)}."
        )
    return store


def _build_telegram_stack(
    on_message: OnMessage,
    on_confirmation_outcome: Callable[[str, str, Outbox], Awaitable[None]] | None = None,
    log_sink: LogSink | None = None,
) -> TelegramStack:
    """Construct and wire the Telegram channel, router, confirmation UI + store,
    outbox, and PTB host, and register the defaults for proactive sends /
    confirmation. Reads TELEGRAM_BOT_TOKEN and ALLOWED_USER_ID (the channel's
    owner-config) from the environment.

    on_confirmation_outcome: domain callback that turns a confirmation outcome
    into a conversational acknowledgement (keeps the agent out of the gateway).
    log_sink: host-injected notification-log writer the Outbox records
    event-tagged sends through (keeps the gateway free of tools-layer imports).
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN not set in the environment")
    owner_env = os.getenv("ALLOWED_USER_ID")
    if not owner_env:
        raise ValueError("ALLOWED_USER_ID not set in the environment")
    owner_id = int(owner_env)

    channel = TelegramChannel(owner_id)
    outbox = Outbox(channel, log_sink)
    confirmation_ui = TelegramConfirmationUI(channel)
    store = InMemoryConfirmationStore(
        confirmation_ui, outbox, channel.owner_thread_id, on_confirmation_outcome
    )
    confirmation_ui.bind_store(store)
    router = TelegramInboundRouter(channel, on_message)
    host = TelegramHost(token, channel, router, confirmation_ui, store)

    register_channel(channel, outbox)
    register_confirmation(channel.name, store)
    logger.info("Telegram stack built (owner_id=%d)", owner_id)
    return TelegramStack(
        channel=channel, router=router, store=store,
        confirmation_ui=confirmation_ui, outbox=outbox, host=host,
    )


def _build_jarvis_app_stack(
    on_message: OnMessage,
    on_confirmation_outcome: Callable[[str, str, Outbox], Awaitable[None]] | None = None,
    log_sink: LogSink | None = None,
) -> JarvisAppStack:
    """Construct and wire the jarvis-app channel — HubClient, channel, confirmation
    store/UI, outbox, and the poll-loop router — and register the channel for
    proactive routing and the confirmation backend for destructive tools. Reads
    APP_HUB_URL / APP_HUB_BOT_TOKEN / APP_OWNER_USER_ID from the environment.

    on_confirmation_outcome: domain callback that turns a confirmation outcome
    into a conversational acknowledgement (keeps the agent out of the gateway) —
    same callback the Telegram stack uses, so a resolved confirmation acks on
    whichever channel's thread it originated from.
    """
    base_url = os.getenv("APP_HUB_URL")
    if not base_url:
        raise ValueError("APP_HUB_URL not set in the environment")
    token = os.getenv("APP_HUB_BOT_TOKEN")
    if not token:
        raise ValueError("APP_HUB_BOT_TOKEN not set in the environment")
    owner = os.getenv("APP_OWNER_USER_ID")
    if not owner:
        raise ValueError("APP_OWNER_USER_ID not set in the environment")

    client = HubClient(base_url, token)
    channel = JarvisAppChannel(client, owner)
    outbox = Outbox(channel, log_sink)
    confirmation_ui = AppConfirmationUI(client)
    store = InMemoryConfirmationStore(
        confirmation_ui, outbox, channel.owner_thread_id, on_confirmation_outcome
    )
    confirmation_ui.bind_store(store)
    router = JarvisAppInboundRouter(channel, client, on_message, confirmation_ui)

    register_channel(channel, outbox)
    register_confirmation(channel.name, store)
    logger.info("jarvis-app stack built (owner=%s)", owner)
    return JarvisAppStack(
        channel=channel, client=client, router=router,
        store=store, confirmation_ui=confirmation_ui, outbox=outbox,
    )


_STACK_BUILDERS: dict[str, Callable[..., Stack]] = {
    "telegram": _build_telegram_stack,
    "jarvis-app": _build_jarvis_app_stack,
}


def build_stack(
    name: str,
    on_message: OnMessage,
    on_confirmation_outcome: Callable[[str, str, Outbox], Awaitable[None]] | None = None,
    log_sink: LogSink | None = None,
) -> Stack:
    """Build the named channel's stack and register its proactive/confirmation
    defaults. With one channel registered this is byte-identical to building it
    directly; the name dispatch is the seam a second channel plugs into."""
    try:
        builder = _STACK_BUILDERS[name]
    except KeyError:
        raise ValueError(f"Unknown channel: {name!r}") from None
    return builder(on_message, on_confirmation_outcome, log_sink)
