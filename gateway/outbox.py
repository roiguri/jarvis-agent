"""
Outbox — the single seam for owner-addressed outbound messages.

Every domain-layer caller that pushes a message to the owner (heartbeat,
reminders, confirmation outcomes, webhook notifications) goes through an
Outbox instead of calling Channel.send_to_owner directly. The Outbox
standardizes three things the call sites used to hand-roll:

- notification logging: sends tagged with an event type are appended to
  notifications.jsonl via an injected sink (the gateway imports nothing from
  the tools layer);
- failure reporting: a send never raises — it returns a SendOutcome so the
  caller can decide what a failed delivery means for its own bookkeeping;
- thread->event-loop bridging: module-level bind_loop()/submit() let sync
  worker threads schedule coroutines on the host loop.

Reply-context sends (router replies, confirmation UI prompts) stay on the
Channel — they live inside the channel package and address a chat, not the
owner.
"""

import asyncio
import concurrent.futures
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Coroutine

from gateway.base import Channel

logger = logging.getLogger(__name__)

# Event types for notifications.jsonl. Values are frozen: agent.py filters
# event == "heartbeat" for the user-scope prompt slice, and existing log rows
# already use these strings.
EVENT_HEARTBEAT = "heartbeat"
EVENT_REMINDER = "reminder"
EVENT_MEDIA = "notification"
EVENT_LLM_MEDIA = "llm_notification"

# Records a sent notification: (event_type, text, metadata). Injected by the
# host so the gateway depends on neither the agent nor the tools layer.
LogSink = Callable[[str, str, dict], Awaitable[None]]


@dataclass
class SendOutcome:
    ok: bool
    error: str | None = None


# ---------------------------------------------------------------------------
# Thread -> event-loop bridge, shared by anything that must reach the channel
# from a sync worker thread.
# ---------------------------------------------------------------------------

_loop: asyncio.AbstractEventLoop | None = None


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Capture the host event loop. Called once at startup, from inside the
    loop, before any worker thread needs to send."""
    global _loop
    _loop = loop


def submit(coro: Coroutine) -> "concurrent.futures.Future":
    """Schedule a coroutine on the bound host loop from any thread."""
    if _loop is None:
        raise RuntimeError("Outbox loop not bound — call bind_loop() at startup first.")
    return asyncio.run_coroutine_threadsafe(coro, _loop)


def loop_bound() -> bool:
    return _loop is not None


class Outbox:
    def __init__(self, channel: Channel, log_sink: LogSink | None = None) -> None:
        self._channel = channel
        self._log_sink = log_sink

    async def notify_owner(
        self,
        text: str,
        *,
        event: str | None = None,
        metadata: dict | None = None,
    ) -> SendOutcome:
        """Send text to the owner. Logs to the notification sink on success
        when an event type is given; never raises."""
        try:
            await self._channel.send_to_owner(text)
        except Exception as e:
            logger.exception("Outbox: failed to send owner message (event=%s)", event)
            return SendOutcome(ok=False, error=str(e))
        await self._log(event, text, metadata)
        return SendOutcome(ok=True)

    async def notify_owner_media(
        self,
        kind: str,
        payload: bytes,
        caption: str | None = None,
        *,
        event: str | None = None,
        metadata: dict | None = None,
    ) -> SendOutcome:
        """Send media to the owner. Same logging/failure semantics as
        notify_owner; the caption is what gets logged."""
        try:
            await self._channel.send_to_owner_media(kind, payload, caption)
        except Exception as e:
            logger.exception("Outbox: failed to send owner media (event=%s)", event)
            return SendOutcome(ok=False, error=str(e))
        await self._log(event, caption or f"[{kind}]", metadata)
        return SendOutcome(ok=True)

    async def _log(self, event: str | None, text: str, metadata: dict | None) -> None:
        """Notification logging must never turn a delivered message into a
        reported failure — the send already happened."""
        if event is None or self._log_sink is None:
            return
        try:
            await self._log_sink(event, text, metadata or {})
        except Exception:
            logger.exception("Outbox: notification log write failed (event=%s)", event)
