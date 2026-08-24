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

from gateway.apps import AppError, dispatch, list_apps
from gateway.base import InboundMessage, OnMessage
from gateway.blocks import BlockAction, render_submission
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

# The "Jarvis is thinking" indicator is driven entirely by these beats: the
# client draws the row while they keep arriving and retires it once `ttl_ms` has
# passed since the last one. Nothing on the wire says "done" — a turn's end is
# left to speak for itself, so an agent killed mid-turn is discovered exactly the
# way a healthy one's silence is.
#
# The TTL is ~2.5x the cadence, and the ratio is the point: at 1x a single slow
# or dropped beat would hide the row mid-turn, so the indicator would flicker on
# ordinary jitter rather than only on a genuinely dead turn. `ttl_ms` rides on
# every beat instead of being a value the client also hardcodes — the two sides
# share no code, so a duplicated constant would drift silently. Change the
# cadence and the TTL together, keeping the ratio.
_THINKING_INTERVAL_S = 4.0
_THINKING_TTL_MS = 10_000

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
        # App queries get their own queue and drain task. The turn queue is
        # serialised so two messages never run overlapping LLM turns on one
        # thread; a query names no thread and needs no model, so that reason
        # does not reach it — and a client is parked on it with a timeout
        # shorter than a turn can take, so sharing the queue would fail it
        # outright whenever a conversation happened to be in flight.
        self._app_queue: asyncio.Queue = asyncio.Queue()
        self._stop = asyncio.Event()
        self._degraded = False  # True while the hub is unreachable — for log-once

    def request_stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        """Poll and answer until request_stop(); then drain in-flight turns."""
        await self._check_contract()
        await self._declare_all()
        fetcher = asyncio.create_task(self._fetch_loop())
        consumer = asyncio.create_task(self._consume_loop())
        app_consumer = asyncio.create_task(self._app_query_loop())

        await self._stop.wait()

        # Drain: stop fetching (a hanging poll just drops — anything fetched is
        # already queued, since the ack was the poll), finish queued work, exit.
        # Queries drain first: they are fast and somebody is waiting on each one,
        # whereas a turn's reply has no deadline.
        fetcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await fetcher
        await self._app_queue.join()
        app_consumer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await app_consumer
        await self._queue.join()
        consumer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await consumer

    async def _declare_all(self) -> None:
        """Publish everything the hub holds on the agent's behalf: the slash
        commands and the app manifest.

        Called at startup *and* on every reconnect. The hub keeps both registries
        in memory, so a hub restart forgets them — declaring only on first boot
        would leave the app's command menu and Apps screen empty until the agent
        itself happened to restart. Each declare replaces that whole list, so
        re-sending is idempotent. Neither call raises: a declare that fails is
        logged and retried on the next reconnect."""
        await self._declare_commands()
        await self._declare_apps()

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

    async def _declare_apps(self) -> None:
        """Publish the registry's app manifest so the app can draw its Apps
        screen. Maps the neutral AppSpec/AppEntry into the hub's wire shape here
        — the registry stays channel-agnostic. Non-fatal, same as commands."""
        apps = [
            {
                "ns": app.ns,
                "name": app.name,
                "entries": [
                    {"id": e.id, "method": e.method, "params": list(e.params)}
                    for e in app.entries
                ],
            }
            for app in list_apps()
        ]
        try:
            await self._client.declare_apps(apps)
        except Exception as exc:
            logger.warning("jarvis-app app declaration skipped: %s", exc)
        else:
            logger.info("declared %d apps to the jarvis-app hub", len(apps))

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
                # The outage may have been a hub restart, which drops the
                # in-memory command and app registries — re-declare before
                # serving, since only this transition knows a link was re-made.
                # A restart can equally mean a new hub version, so re-check the
                # contract too; startup-only checking left an upgrade invisible
                # until this process's own next restart.
                await self._check_contract()
                await self._declare_all()
            backoff = _BACKOFF_START_S
            for update in updates:
                # Advance + enqueue before any turn runs: the next poll (which acks
                # this batch) must not wait behind the turn. Queries split off
                # here, at fetch time — routing them later would already have put
                # them behind whatever the turn queue is holding.
                offset = update["update_id"] + 1
                if update.get("type") == "app_query":
                    await self._app_queue.put(update)
                else:
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

    async def _app_query_loop(self) -> None:
        """Answer app queries, independent of the turn consumer."""
        while True:
            update = await self._app_queue.get()
            try:
                await self._answer_app_query(update)
            except Exception:  # one bad query must not take the loop down
                logger.exception("jarvis-app app_query failed")
            finally:
                self._app_queue.task_done()

    async def _answer_app_query(self, update: dict) -> None:
        """Run one query through the app registry and post the result.

        Deterministic dispatch — no model in this path. The result body carries
        exactly one of data/error, by field presence: `{"data": None}` is a
        valid success, an empty body is not.
        """
        query_id = update.get("query_id")
        ns = update.get("ns")
        entry_id = update.get("entry_id")
        if not query_id or not ns or not entry_id:
            logger.warning("jarvis-app malformed app_query: %s", update)
            return
        params = update.get("params") or {}
        try:
            body = {"data": await dispatch(ns, entry_id, params)}
        except AppError as exc:
            body = {"error": {"code": exc.code, "message": str(exc)}}
        except Exception as exc:
            # Neither closed code fits a fault on this side — both describe a
            # bad request. Reporting one would blame the caller for our bug, so
            # this deliberately takes the documented fallback: an unrecognised
            # code reaches the client as a 502, which is what actually happened.
            logger.exception("jarvis-app app %s/%s raised", ns, entry_id)
            body = {"error": {"code": "internal_error", "message": str(exc)}}
        try:
            delivered = await self._client.post_app_result(query_id, body)
        except Exception as exc:
            logger.warning("jarvis-app could not answer %s: %s", query_id, exc)
            return
        if not delivered:
            # Nobody was waiting: timed out, hung up, or queued before a hub
            # restart. Expected, and unretryable — the park is gone.
            logger.info("jarvis-app query %s expired before it was answered", query_id)
        else:
            # The only positive trace that a query was served, and the only
            # record of an AppError refusal — which is otherwise silent on this
            # side even though this is the side issuing it.
            outcome = "ok" if "data" in body else body["error"]["code"]
            logger.info("jarvis-app served %s/%s -> %s", ns, entry_id, outcome)

    async def _handle(self, update: dict) -> None:
        if update.get("type") == "action":
            await self._handle_action(
                BlockAction(
                    kind=update["block_kind"],
                    action_id=update["action_id"],
                    message_id=update["message_id"],
                    callback_id=update.get("callback_id"),
                    values=update.get("values"),
                )
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

        await self._run_turn(text, attachments)

    async def _handle_action(self, action: BlockAction) -> None:
        """Dispatch one tap by block kind. `confirmation` resolves below the
        LLM via the store; `form` is content — its submission becomes an
        ordinary inbound turn; anything else has no handler yet."""
        if action.kind == "confirmation":
            await self._confirmation_ui.handle_action(
                action_id=action.action_id,
                message_id=action.message_id,
                block_kind=action.kind,
                callback_id=action.callback_id,
            )
            return
        if action.kind == "form":
            await self._handle_form_submit(action)
            return
        logger.info(
            "jarvis-app ignoring action for block_kind=%r (no handler yet)", action.kind
        )

    async def _handle_form_submit(self, action: BlockAction) -> None:
        """Run a submitted form's values through the agent, then close the card.

        The PATCH to `logged` comes strictly after the turn: `logged` means the
        turn actually ran. A turn that raises leaves the card untouched — the
        hub's re-tap guard lifts once the update is acked (which happened at
        fetch), so the live card itself is the retry path, with thread context
        (submission text logged before the turn) guarding against a re-run
        double-logging.
        """
        await self._run_turn(render_submission(action.callback_id, action.values), [])
        try:
            await self._client.patch_message_state(action.message_id, "logged")
        except Exception:
            # The turn already ran and replied; a lost PATCH costs a card stuck
            # on pending, which a re-tap or a later sweep can settle — never
            # worth failing the consumed update over.
            logger.exception(
                "jarvis-app PATCH logged failed for form %s", action.callback_id
            )

    async def _run_turn(self, text: str, attachments: list[dict]) -> None:
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
        beat = asyncio.create_task(self._thinking_beat())
        try:
            reply = await self._on_message(inbound)
            if reply:
                await self._channel.send(self._channel.owner_thread_id, reply)
        finally:
            # The beat exists only for the life of the turn — including the one
            # that raised, where a surviving task would report a dead agent as
            # still thinking.
            beat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await beat

    async def _thinking_beat(self) -> None:
        """Beat while a turn runs, so the app can show its thinking indicator.

        Beats before the first sleep: the client learns a turn started right
        away rather than one cadence in. Each failure is caught and the loop
        continues — a beat that stops is precisely how the client reports *the
        agent died*, so letting one transient POST failure end the loop would
        make a healthy agent look dead. The next cadence is the retry.
        """
        while True:
            try:
                await self._client.post_event(
                    "agent_thinking", {"ttl_ms": _THINKING_TTL_MS}
                )
            except Exception as exc:
                logger.warning("jarvis-app thinking beat failed: %s", exc)
            await asyncio.sleep(_THINKING_INTERVAL_S)

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
