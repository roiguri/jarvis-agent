"""
Channel factory and the channel-agnostic accessors proactive code depends on.

Callers that need to reach the user without a chat_id (heartbeat, reminders,
confirmation outcomes) use default_user_channel(); destructive tools use
get_confirmation(). Neither imports a concrete channel module.
"""

import logging
from dataclasses import dataclass

from typing import Awaitable, Callable

from gateway.base import Channel, OnMessage
from gateway.confirmation.base import Confirmation
from gateway.confirmation.store import InMemoryConfirmationStore
from gateway.outbox import LogSink, Outbox
from gateway.channels.telegram.channel import TelegramChannel
from gateway.channels.telegram.confirmation import TelegramConfirmationUI
from gateway.channels.telegram.router import TelegramInboundRouter

logger = logging.getLogger(__name__)


@dataclass
class TelegramStack:
    channel: TelegramChannel
    router: TelegramInboundRouter
    store: InMemoryConfirmationStore
    confirmation_ui: TelegramConfirmationUI
    outbox: Outbox


# Registry for proactive sends / confirmation. Set when a stack is built.
_default_channel: Channel | None = None
_confirmation: Confirmation | None = None
_default_outbox: Outbox | None = None


def set_default_user_channel(channel: Channel) -> None:
    global _default_channel
    _default_channel = channel


def default_user_channel() -> Channel:
    """The channel proactive sends target. Today the single Telegram channel;
    when a second ships this becomes a routing decision living here, not in
    callers."""
    if _default_channel is None:
        raise RuntimeError("No default user channel configured.")
    return _default_channel


def set_default_outbox(outbox: Outbox) -> None:
    global _default_outbox
    _default_outbox = outbox


def default_outbox() -> Outbox:
    """The outbox owner-addressed proactive sends go through."""
    if _default_outbox is None:
        raise RuntimeError("No default outbox configured.")
    return _default_outbox


def set_confirmation(confirmation: Confirmation) -> None:
    global _confirmation
    _confirmation = confirmation


def get_confirmation() -> Confirmation:
    """The active confirmation backend destructive tools call."""
    if _confirmation is None:
        raise RuntimeError("Confirmation system not configured.")
    return _confirmation


def build_telegram_stack(
    owner_id: int,
    on_message: OnMessage,
    on_confirmation_outcome: Callable[[str], Awaitable[None]] | None = None,
    log_sink: LogSink | None = None,
) -> TelegramStack:
    """Construct and wire the Telegram channel, router, confirmation UI + store,
    and register them as the defaults for proactive sends / confirmation.

    on_confirmation_outcome: domain callback that turns a confirmation outcome
    into a conversational acknowledgement (keeps the agent out of the gateway).
    log_sink: host-injected notification-log writer the Outbox records
    event-tagged sends through (keeps the gateway free of tools-layer imports).
    """
    channel = TelegramChannel(owner_id)
    outbox = Outbox(channel, log_sink)
    confirmation_ui = TelegramConfirmationUI(channel)
    store = InMemoryConfirmationStore(confirmation_ui, outbox, on_confirmation_outcome)
    confirmation_ui.bind_store(store)
    router = TelegramInboundRouter(channel, on_message)

    set_default_user_channel(channel)
    set_confirmation(store)
    set_default_outbox(outbox)
    logger.info("Telegram stack built (owner_id=%d)", owner_id)
    return TelegramStack(
        channel=channel, router=router, store=store,
        confirmation_ui=confirmation_ui, outbox=outbox,
    )
