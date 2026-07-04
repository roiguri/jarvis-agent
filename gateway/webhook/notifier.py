"""
Media notification aggregator (Sonarr/Radarr/Jellyfin webhook side).

Per-batch aggregation with deterministic Markdown templates; the LLM is used
only for system alerts and unknown events. All sends go through the channel's
owner-addressed methods — the notifier does not know how the channel renders.
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import httpx

from gateway.base import Channel

logger = logging.getLogger(__name__)

# Injected by the host so the gateway depends on neither the agent nor the
# tools layer. LLMFormat turns a prompt into notification text; LogSink
# records a sent notification (event_type, text, metadata).
LLMFormat = Callable[[str], Awaitable[str]]
LogSink = Callable[[str, str, dict], Awaitable[None]]

SILENCE_MOVIE = 120    # 2-min fallback — used when expected count is unknown
SILENCE_SERIES = 600   # 10-min fallback — series timer if not all episodes arrive

# Jellyfin internal address (poster fetch). Set via env per deploy.
JELLYFIN_INTERNAL = os.getenv("JELLYFIN_INTERNAL_URL", "http://jellyfin.local:8096")
# Public Jellyfin URL shown to the user in "Watch at ..." lines. Set via env.
JELLYFIN_PUBLIC = os.getenv("JELLYFIN_PUBLIC_URL", "jellyfin.example.com")


@dataclass
class _Batch:
    """Tracks all events for one (series + season) or movie batch."""
    ready:    list[tuple[str, str | None]] = field(default_factory=list)  # (name, image_id)
    upgraded: list[tuple[str, str | None]] = field(default_factory=list)
    failed:   list[str]                    = field(default_factory=list)
    expected: int = 0          # episodes/movies expected from Arr; 0 = unknown
    timer:    asyncio.Task | None = None

    def is_complete(self) -> bool:
        return self.expected > 0 and (len(self.ready) + len(self.failed)) >= self.expected

    def has_content(self) -> bool:
        return bool(self.ready or self.upgraded or self.failed)


# ---------------------------------------------------------------------------
# Message formatters (deterministic — Markdown, no LLM)
# ---------------------------------------------------------------------------

def _format_batch_ready_message(key: str, batch: _Batch) -> str:
    count = len(batch.ready)
    nfail = len(batch.failed)

    if key == "Movies":
        title = batch.ready[0][0] if count == 1 else f"{count} movies"
        msg = f"**{title}** is ready on Jellyfin."
        if nfail:
            msg += f" ({nfail} failed)"
        return f"{msg}\nWatch at {JELLYFIN_PUBLIC}"

    series, season_code = key.rsplit("__", 1)
    season_num = int(season_code.lstrip("S"))

    if count == 1 and nfail == 0:
        return (
            f"**{series}** — {batch.ready[0][0]} is ready on Jellyfin."
            f"\nWatch at {JELLYFIN_PUBLIC}"
        )

    if batch.expected > 0 and nfail == 0 and count >= batch.expected:
        body = f"Season {season_num} is ready on Jellyfin. ({count} episodes)"
    elif batch.expected > 0:
        body = f"Season {season_num} — {count} of {batch.expected} episodes ready on Jellyfin."
        if nfail:
            body += f" ({nfail} failed)"
    else:
        body = f"Season {season_num} — {count} episodes ready on Jellyfin."

    return f"**{series}** — {body}\nWatch at {JELLYFIN_PUBLIC}"


def _format_batch_upgrade_message(key: str, batch: _Batch) -> str:
    count = len(batch.upgraded)

    if key == "Movies":
        title = batch.upgraded[0][0] if count == 1 else f"{count} movies"
        return (
            f"**{title}** upgraded to better quality on Jellyfin."
            f"\nWatch at {JELLYFIN_PUBLIC}"
        )

    series, season_code = key.rsplit("__", 1)
    season_num = int(season_code.lstrip("S"))

    if count == 1:
        return (
            f"**{series}** — {batch.upgraded[0][0]} upgraded to better quality on Jellyfin."
            f"\nWatch at {JELLYFIN_PUBLIC}"
        )
    return (
        f"**{series}** — Season {season_num} — {count} episodes upgraded to better quality on Jellyfin."
        f"\nWatch at {JELLYFIN_PUBLIC}"
    )


# ---------------------------------------------------------------------------
# LLM prompt builders (system/unknown events only)
# ---------------------------------------------------------------------------

def _build_system_prompt(event_type: str, payload: dict) -> str:
    message = payload.get("message") or payload.get("title") or ""
    wiki = payload.get("wikiUrl") or payload.get("sourceTitle") or ""
    return (
        f"System Alert from the home lab media stack (Sonarr/Radarr).\n"
        f"Event: {event_type}\n"
        f"Details: {message or '(no message in payload)'}\n"
        f"{'Reference: ' + wiki if wiki else ''}\n\n"
        f"Write a concise direct alert. State what happened and what the user may need to check. "
        f"1-2 sentences. No name prefix. Use **bold** for key terms."
    )


def _build_unknown_prompt(event_type: str, payload: dict) -> str:
    return (
        f"An unrecognized webhook event was received from the home lab media stack.\n"
        f"Event type: '{event_type}'\n"
        f"Full payload:\n{json.dumps(payload, indent=2, default=str)}\n\n"
        f"Analyse the payload and write a short, helpful message explaining what likely happened "
        f"and whether the user needs to take any action. Include the event type. No name prefix."
    )


# ---------------------------------------------------------------------------
# Image fetcher
# ---------------------------------------------------------------------------

async def _fetch_image(image_id: str) -> bytes | None:
    url = f"{JELLYFIN_INTERNAL}/Items/{image_id}/Images/Primary?fillWidth=400"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url)
            r.raise_for_status()
            return r.content
    except Exception as e:
        logger.warning("Could not fetch Jellyfin image %s: %s", image_id, e)
        return None


# ---------------------------------------------------------------------------
# Notification manager
# ---------------------------------------------------------------------------

class MediaNotificationManager:
    """Per-batch media aggregator. Dispatch fires when all expected items are
    accounted for, or on a silence timer."""

    def __init__(self, channel: Channel, llm_format: LLMFormat, log_notification: LogSink) -> None:
        self._channel = channel
        self._llm_format = llm_format
        self._log_notification = log_notification
        self._batches: dict[str, _Batch] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Buffered API — called by webhook handlers
    # ------------------------------------------------------------------

    async def reset_timer(self, key: str) -> None:
        async with self._lock:
            self._get_or_create_batch(key)
        self._reschedule_timer(key)

    async def record_arr_download(self, key: str, count: int) -> None:
        async with self._lock:
            batch = self._get_or_create_batch(key)
            batch.expected += count
            complete = batch.is_complete()
        logger.info("Arr download: key=%r expected=%d ready=%d complete=%s",
                    key, batch.expected, len(batch.ready), complete)
        if complete:
            await self._dispatch_batch(key)
        else:
            self._reschedule_timer(key)

    async def add_ready_item(self, key: str, item: str, image_id: str | None = None) -> None:
        async with self._lock:
            batch = self._get_or_create_batch(key)
            if item not in [n for n, _ in batch.ready]:
                batch.ready.append((item, image_id))
                logger.info("Buffered ready: key=%r item=%r image_id=%s", key, item, image_id)
            else:
                logger.info("Skipped duplicate: key=%r item=%r", key, item)
            complete = batch.is_complete()
        if complete:
            await self._dispatch_batch(key)
        else:
            self._reschedule_timer(key)

    async def add_upgrade(self, key: str, item: str, image_id: str | None = None) -> None:
        async with self._lock:
            batch = self._get_or_create_batch(key)
            if item not in [n for n, _ in batch.upgraded]:
                batch.upgraded.append((item, image_id))
                logger.info("Buffered upgrade: key=%r item=%r", key, item)
            else:
                logger.info("Skipped duplicate upgrade: key=%r item=%r", key, item)
        self._reschedule_timer(key)

    async def record_failure(self, key: str, label: str) -> None:
        async with self._lock:
            batch = self._get_or_create_batch(key)
            if label not in batch.failed:
                batch.failed.append(label)
                logger.info("Buffered failure: key=%r label=%r", key, label)
            complete = batch.is_complete()
        if complete:
            await self._dispatch_batch(key)
        else:
            self._reschedule_timer(key)

    # ------------------------------------------------------------------
    # Immediate API
    # ------------------------------------------------------------------

    def has_pending_download(self, key: str) -> bool:
        batch = self._batches.get(key)
        return batch is not None and batch.expected > 0

    async def dispatch_system_alert(self, event_type: str, payload: dict) -> None:
        logger.warning("System alert dispatched immediately: %s", event_type)
        await self._send_via_llm(_build_system_prompt(event_type, payload))

    async def dispatch_unknown_event(self, event_type: str, payload: dict) -> None:
        logger.warning("Unknown event forwarded: %s | keys: %s", event_type, list(payload.keys()))
        await self._send_via_llm(_build_unknown_prompt(event_type, payload))

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _get_or_create_batch(self, key: str) -> _Batch:
        if key not in self._batches:
            self._batches[key] = _Batch()
        return self._batches[key]

    def _reschedule_timer(self, key: str) -> None:
        batch = self._batches.get(key)
        if batch is None:
            return
        if batch.timer and not batch.timer.done():
            batch.timer.cancel()
        silence = SILENCE_MOVIE if key == "Movies" else SILENCE_SERIES
        batch.timer = asyncio.create_task(self._timer_dispatch(key, silence))

    async def _timer_dispatch(self, key: str, silence: int) -> None:
        try:
            await asyncio.sleep(silence)
        except asyncio.CancelledError:
            return
        await self._dispatch_batch(key)

    async def _dispatch_batch(self, key: str) -> None:
        async with self._lock:
            batch = self._batches.pop(key, None)
        if batch is None or not batch.has_content():
            return
        if batch.timer and not batch.timer.done() and batch.timer is not asyncio.current_task():
            batch.timer.cancel()

        logger.info("Dispatching batch %r: ready=%d upgraded=%d failed=%d expected=%d",
                    key, len(batch.ready), len(batch.upgraded), len(batch.failed), batch.expected)

        if batch.ready:
            image_id = next((img for _, img in batch.ready if img), None)
            await self._send_direct(_format_batch_ready_message(key, batch), image_id=image_id)
        elif batch.failed:
            await self._send_direct(f"Download failed: {', '.join(batch.failed)}")

        if batch.upgraded:
            image_id = next((img for _, img in batch.upgraded if img), None)
            await self._send_direct(_format_batch_upgrade_message(key, batch), image_id=image_id)

    async def _send_direct(self, text: str, image_id: str | None = None, log_event: str = "notification") -> None:
        try:
            if image_id:
                image_bytes = await _fetch_image(image_id)
                if image_bytes:
                    await self._channel.send_to_owner_media("image", image_bytes, caption=text)
                    logger.info("Photo notification sent (image_id=%s)", image_id)
                    await self._log_notification(log_event, text, {"has_image": True})
                    return
            await self._channel.send_to_owner(text)
            logger.info("Text notification sent")
            await self._log_notification(log_event, text, {"has_image": False})
        except Exception:
            logger.exception("Failed to send notification")

    async def _send_via_llm(self, prompt: str, image_id: str | None = None) -> None:
        try:
            response_text = await self._llm_format(prompt)
            if not response_text:
                logger.warning("LLM returned empty response — skipping send.")
                return
            await self._send_direct(response_text, image_id=image_id, log_event="llm_notification")
        except Exception:
            logger.exception("Failed to send LLM notification")
