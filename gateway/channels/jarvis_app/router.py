"""
jarvis-app inbound router — long-polls the hub and runs one turn at a time.

The poll loop is two tasks over a queue, and the shape is load-bearing. A fetcher
long-polls and, per update, advances the offset and enqueues it *before* any turn
runs — so the next poll, which acks the previous batch, goes out while a turn is
still in flight. A single consumer runs turns one at a time, so two messages in a
row never overlap (the per-conversation ordering the other transports provide).
Each inbound update becomes an InboundMessage and flows through the shared
on_message handler, exactly like any channel.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from gateway.base import InboundMessage, OnMessage
from gateway.channels.jarvis_app.channel import JarvisAppChannel
from gateway.channels.jarvis_app.client import (
    HubClient,
    PINNED_CONTRACT_VERSION,
)

logger = logging.getLogger(__name__)

# Degraded-mode backoff: a failed poll waits, doubling to a ceiling, so a hub
# outage neither spins nor logs a line per attempt.
_BACKOFF_START_S = 1.0
_BACKOFF_MAX_S = 60.0


class JarvisAppInboundRouter:
    def __init__(
        self, channel: JarvisAppChannel, client: HubClient, on_message: OnMessage
    ) -> None:
        self._channel = channel
        self._client = client
        self._on_message = on_message
        self._queue: asyncio.Queue = asyncio.Queue()
        self._stop = asyncio.Event()
        self._degraded = False  # True while the hub is unreachable — for log-once

    def request_stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        """Poll and answer until request_stop(); then drain in-flight turns."""
        await self._check_contract()
        fetcher = asyncio.create_task(self._fetch_loop())
        consumer = asyncio.create_task(self._consume_loop())

        await self._stop.wait()

        # Drain: stop fetching (a hanging poll just drops — anything fetched is
        # already queued, since the ack was the poll), finish queued turns, exit.
        fetcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await fetcher
        await self._queue.join()
        consumer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await consumer

    async def _check_contract(self) -> None:
        """Warn (never fail) if the hub's contract_version differs from the one
        this adapter was written against. Silently skipped if the hub is down —
        the poll loop's degraded mode reports that."""
        try:
            health = await self._client.get_health()
        except Exception:
            return
        reported = health.get("contract_version")
        if reported != PINNED_CONTRACT_VERSION:
            logger.warning(
                "jarvis-app hub contract_version %r != pinned %r — proceeding, "
                "payloads may skew",
                reported,
                PINNED_CONTRACT_VERSION,
            )

    async def _fetch_loop(self) -> None:
        offset = 0
        backoff = _BACKOFF_START_S
        while not self._stop.is_set():
            try:
                updates = await self._client.get_updates(offset)
            except Exception as exc:
                # A dropped poll must not kill the fetcher — the consumer would sit
                # idle forever. Log once on entering degraded mode, back off, retry.
                if not self._degraded:
                    logger.warning("jarvis-app hub unreachable, backing off: %s", exc)
                    self._degraded = True
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _BACKOFF_MAX_S)
                continue
            if self._degraded:
                logger.info("jarvis-app hub reachable again")
                self._degraded = False
            backoff = _BACKOFF_START_S
            for update in updates:
                # Advance + enqueue before any turn runs: the next poll (which acks
                # this batch) must not wait behind the turn.
                offset = update["update_id"] + 1
                await self._queue.put(update)

    async def _consume_loop(self) -> None:
        while True:
            update = await self._queue.get()
            try:
                await self._handle(update)
            except Exception:  # one bad turn must not take the consumer down
                logger.exception("jarvis-app turn failed")
            finally:
                self._queue.task_done()

    async def _handle(self, update: dict) -> None:
        if update.get("type") != "message":
            return  # non-message updates carry no turn; the poll already acked them
        message = update.get("message") or {}
        # Only the owner's own messages drive a turn — never the agent's own sends
        # echoed back, which would loop.
        if message.get("role") != "user":
            return
        text = message.get("text") or ""

        # The hub carries no per-message user id (the bot token scopes the single
        # owner), so user_id/chat_id are placeholders — nothing downstream reads
        # them; the thread id is what namespaces the conversation.
        inbound = InboundMessage(
            user_id=0,
            chat_id=0,
            thread_id=self._channel.owner_thread_id,
            user_text=text,
        )
        reply = await self._on_message(inbound)
        if reply:
            await self._channel.send(self._channel.owner_thread_id, reply)
