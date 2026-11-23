"""Saving the files that pass through Jarvis: in through a channel, out to the
owner, or fetched by a tool.

An attachment is a plain dict — kind, mime_type, data (bytes), filename, source,
origin_id. Channels hand over the files a message brings in this shape before its
turn runs; the Outbox builds the same shape for a file Jarvis sends. Each one
becomes a file record here. A tool keeps what it fetched with save_fetched(). An incoming file that can't be saved becomes a note in
the message instead, so the reply can say so rather than ignore it.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from files import blobs, store
from files.model_input import article

logger = logging.getLogger(__name__)


def save_attachments(attachments: list[dict], *, caption: str = "",
                     turn_id: str | None = None) -> tuple[list[store.FileRecord], list[str]]:
    """(records saved, notes for those that weren't). `caption` is the message text,
    kept on each record so the file can be found by it later."""
    records, notes = [], []
    for att in attachments or []:
        kind = att.get("kind") or "file"
        what = "photo" if kind == "image" else kind
        data = att.get("data")
        if not data:
            notes.append(f"[{article(what).capitalize()} {what} was sent but arrived empty.]")
            continue
        origin = {"id": att["origin_id"]} if att.get("origin_id") else {}
        try:
            records.append(store.add(
                data, source=att.get("source") or "unknown", filename=att.get("filename"),
                caption=caption, origin=origin, turn_id=turn_id,
                mime_hint=att.get("mime_type")))
        except store.FileTooLarge:
            mb = len(data) / (1024 * 1024)
            notes.append(f"[{article(what).capitalize()} {what} was sent but is too large "
                         f"to keep ({mb:.0f} MB).]")
        except Exception as e:
            logger.exception("could not save a %s attachment", kind)
            notes.append(f"[{article(what).capitalize()} {what} was sent but couldn't be "
                         f"saved ({type(e).__name__}).]")
    return records, notes


def save_sent(attachment: dict, caption: str | None) -> tuple[str, str] | None:
    """Save a file Jarvis sent: (file id, marker), or None when it couldn't be kept.
    The send already happened, so a failure only logs."""
    records, notes = save_attachments([attachment], caption=caption or "")
    if not records:
        logger.warning("a sent file was not kept: %s", " ".join(notes))
        return None
    return records[0].id, records[0].marker()


def save_fetched(data: bytes, *, source: str, filename: str | None = None,
                 caption: str | None = None, origin: dict | None = None,
                 mime_hint: str | None = None, record=None) -> store.FileRecord | None:
    """Keep a file a tool obtained, tied to the running turn. `record` is a written
    record the tool already made (see store.add). None when it couldn't be kept:
    the tool's answer goes on without the file."""
    from observability import telemetry

    try:
        return store.add(data, source=source, filename=filename, caption=caption,
                         origin=origin, turn_id=telemetry.TURN_ID.get(),
                         mime_hint=mime_hint, record=record)
    except Exception:
        logger.exception("could not keep a fetched file (%s)", source)
        return None


async def resolve_origin_attachment(
    channel: str,
    origin_id: str,
    fetch: Callable[[], Awaitable[bytes]],
    meta: dict,
) -> str:
    """Resolve a quoted channel attachment `(channel, origin_id)` to its
    `[file:…]` marker without downloading when already linked in the store.

    Fallback (pre-link messages): calls `fetch()` once, deduplicates by SHA-256
    against existing non-deleted records (linking if found), else saves once.
    Never raises — download or storage failures become an honest note."""
    try:
        rec = await asyncio.to_thread(store.get_by_origin, channel, origin_id)
    except Exception as exc:
        logger.warning("%s origin lookup for %s failed: %s", channel, origin_id, exc)
        rec = None
    if rec is not None:
        return rec.marker()

    try:
        data = await fetch()
    except Exception as exc:
        logger.warning("%s quoted attachment %s download failed: %s", channel, origin_id, exc)
        return "[quoted attachment could not be retrieved]"

    if not data:
        return "[quoted attachment could not be retrieved]"

    def _dedup_or_save() -> str:
        sha = blobs.digest(data)
        existing = store.latest_by_hash(sha)
        if existing is not None and existing.state != "missing":
            store.link_origin(existing.id, channel, origin_id)
            return existing.marker()
        attachment = {
            "kind": meta.get("kind") or "file",
            "mime_type": meta.get("mime_type") or "",
            "data": data,
            "filename": meta.get("filename"),
            "source": channel,
            "origin_id": origin_id,
        }
        records, notes = save_attachments(
            [attachment], caption=meta.get("caption") or ""
        )
        if records:
            return records[0].marker()
        return notes[0] if notes else "[quoted attachment could not be retrieved]"

    try:
        return await asyncio.to_thread(_dedup_or_save)
    except Exception as exc:
        logger.warning("%s quoted attachment %s save failed: %s", channel, origin_id, exc)
        return "[quoted attachment could not be retrieved]"
