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
import re

from gateway.base import InboundMessage, OnMessage
from gateway.commands import list_commands
from gateway.channels.jarvis_app import media_cache
from gateway.channels.jarvis_app.channel import JarvisAppChannel
from gateway.channels.jarvis_app.client import (
    HubClient,
    PINNED_CONTRACT_VERSION,
)
from gateway.channels.jarvis_app.confirmation import AppConfirmationUI

logger = logging.getLogger(__name__)

# Degraded-mode backoff: a failed poll waits, doubling to a ceiling, so a hub
# outage neither spins nor logs a line per attempt.
_BACKOFF_START_S = 1.0
_BACKOFF_MAX_S = 60.0

# The hub pins attachment ids to this shape. The id names the cache file, so a
# value that doesn't match is rejected before it can reach a filesystem path.
_ATT_ID_RE = re.compile(r"^att_[0-9A-HJKMNP-TV-Z]{26}$")

# The hub's wire vocabulary is image | audio | file, where "file" is everything
# else — so a PDF and a video arrive under the same tag. The neutral vocabulary
# the agent branches on is image | video | audio | document, and resolving one
# into the other is the channel's job: only the channel knows its source's
# vocabulary. "file" survives as "unidentified" and reaches the agent as text.
_FILE_MIME_KINDS = {
    "video/mp4": "video",
    "video/webm": "video",
    "application/pdf": "document",
}


def _neutral_kind(hub_kind: str, mime_type: str) -> str:
    """Resolve the hub's kind + mime into the gateway's neutral media kind."""
    if hub_kind != "file":
        return hub_kind
    mime = mime_type.split(";")[0].strip().lower()
    return _FILE_MIME_KINDS.get(mime, "file")


class JarvisAppInboundRouter:
    def __init__(
        self,
        channel: JarvisAppChannel,
        client: HubClient,
        on_message: OnMessage,
        confirmation_ui: AppConfirmationUI,
    ) -> None:
        self._channel = channel
        self._client = client
        self._on_message = on_message
        self._confirmation_ui = confirmation_ui
        self._queue: asyncio.Queue = asyncio.Queue()
        self._stop = asyncio.Event()
        self._degraded = False  # True while the hub is unreachable — for log-once

    def request_stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        """Poll and answer until request_stop(); then drain in-flight turns."""
        await self._check_contract()
        await self._declare_commands()
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

    async def _declare_commands(self) -> None:
        """Publish the shared slash-command list to the hub so the app's command
        menu matches the gateway's commands (same source Telegram's menu uses).
        Non-fatal — a hub that's down at startup just skips it; the poll loop's
        degraded mode carries on."""
        commands = [
            {"name": c.name, "description": c.description} for c in list_commands()
        ]
        try:
            await self._client.declare_commands(commands)
        except Exception as exc:
            logger.warning("jarvis-app command declaration skipped: %s", exc)
        else:
            logger.info("declared %d slash commands to the jarvis-app hub", len(commands))

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
        if update.get("type") == "action":
            await self._confirmation_ui.handle_action(
                action_id=update.get("action_id"),
                message_id=update.get("message_id"),
                block_kind=update.get("block_kind"),
                callback_id=update.get("callback_id"),
            )
            return
        if update.get("type") != "message":
            return  # neither message nor action; the poll already acked it
        message = update.get("message") or {}
        # Only the owner's own messages drive a turn — never the agent's own sends
        # echoed back, which would loop.
        if message.get("role") != "user":
            return
        text = message.get("text") or ""

        raw_attachments = message.get("attachments") or []
        attachments = await self._download_attachments(raw_attachments)
        # An attachment-only message still needs text so the model knows something
        # arrived. If every download failed, say that rather than run an empty turn.
        if not text:
            if attachments:
                kinds = ", ".join(a["kind"] for a in attachments)
                text = f"[attachments: {kinds}]"
            elif raw_attachments:
                text = "[an attachment was sent but could not be retrieved]"

        # The hub carries no per-message user id (the bot token scopes the single
        # owner), so user_id/chat_id are placeholders — nothing downstream reads
        # them; the thread id is what namespaces the conversation.
        inbound = InboundMessage(
            user_id=0,
            chat_id=0,
            thread_id=self._channel.owner_thread_id,
            user_text=text,
            attachments=attachments,
        )
        reply = await self._on_message(inbound)
        if reply:
            await self._channel.send(self._channel.owner_thread_id, reply)

    async def _download_attachments(self, raw: list[dict]) -> list[dict]:
        """Download each inbound attachment to the channel-owned cache and return
        the neutral dicts the agent reads (kind, path, mime_type, source). A blob
        that fails to download is logged and skipped — one bad attachment must not
        sink the whole turn."""
        out: list[dict] = []
        for att in raw:
            att_id = att.get("id")
            hub_kind = att.get("kind")
            if not att_id or not hub_kind:
                continue
            if not _ATT_ID_RE.match(att_id):
                logger.warning("jarvis-app skipping attachment with malformed id %r", att_id)
                continue
            mime_type = att.get("mime_type") or ""
            # Resolve before caching, so the cached filename reflects what the
            # blob actually is rather than the hub's catch-all tag.
            kind = _neutral_kind(hub_kind, mime_type)
            try:
                data = await self._client.download_attachment(att_id)
                path = await asyncio.to_thread(media_cache.save, data, kind, att_id)
            except Exception as exc:
                logger.warning("jarvis-app attachment %s download failed: %s", att_id, exc)
                continue
            out.append(
                {
                    "kind": kind,
                    "path": path,
                    "mime_type": mime_type,
                    "source": "jarvis-app",
                }
            )
        return out
