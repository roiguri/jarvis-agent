"""find_files, send_file, delete_file: the stored files as a library."""

from __future__ import annotations

import asyncio
import concurrent.futures
from datetime import datetime

from langchain_core.tools import tool

import turn_context
from files import references, search, store
from gateway import outbox as outbox_mod
from gateway.base import media_send_wait_s
from gateway.outbox import EVENT_HEARTBEAT
from timeutils import ISRAEL_TZ
from tools.registry import tool_register

_DELETE_MAX = 20
# A text file goes out as a document; every other kind keeps its own name.
_CHANNEL_KIND = {"text": "document"}


@tool_register(namespace="files")
@tool
def find_files(query: str = "", kind: str = "", source: str = "", since: str = "",
               until: str = "", limit: int = 10) -> str:
    """Find stored files: photos, voice notes, videos, documents and text files
    the owner sent, and files you sent. Searches their names, the messages they
    came with, and what was recorded of them (descriptions, transcripts,
    document text). Each result starts with the file's marker; use its id with
    open_file, send_file or a collection's file field.

    Args:
        query: plain words, any language. Empty lists the newest files.
        kind: optional: image, video, audio, document, text or file.
        source: optional: where it came from, as results show it.
        since: optional. '2026-05-08' = start of that Israel day; or a full
            ISO 8601 timestamp with a UTC offset.
        until: optional, same formats; a plain date includes that whole day.
        limit: max files (up to 30).
    """
    return search.find(query, kind, source, since or None, until or None, limit)


@tool_register(namespace="files")
@tool
def send_file(file_id: str, caption: str = "") -> str:
    """Send a stored file to the owner, by its id (the `file:…` in its marker).

    Args:
        file_id: the file's id.
        caption: optional text sent with it.
    """
    from gateway.factory import origin_outbox

    rec = store.get(file_id)
    if rec is None:
        return f"No file with id {file_id!r}."
    if rec.state not in ("available", "trash"):
        return f"Can't send {rec.marker()}: the original is no longer stored."
    try:
        data = store.read_bytes(rec.id)
    except (LookupError, OSError) as e:
        return f"Can't send {rec.marker()}: the original could not be read ({e})."

    # A heartbeat-sent file reaches the owner thread through the pending-mirror
    # drain; a chat-turn send is already in the conversation as this result.
    event = EVENT_HEARTBEAT if turn_context.current_scope() == "heartbeat" else None
    wait = media_send_wait_s(len(data))
    try:
        outcome = outbox_mod.submit(origin_outbox().notify_owner_media(
            _CHANNEL_KIND.get(rec.kind, rec.kind), data, caption or None, event=event,
            filename=rec.filename, mime_type=rec.mime_type, kept=(rec.id, rec.marker()),
        )).result(timeout=wait)
    except concurrent.futures.TimeoutError:
        return (f"Error: sending {rec.marker()} was not confirmed within "
                f"{wait:.0f}s — it may still arrive. Do not send it again; ask "
                "the owner whether it arrived.")
    if not outcome.ok:
        return (f"Error: {rec.marker()} could not be sent — {outcome.error}. Tell the owner "
                "rather than sending it again now.")
    return f"Sent {rec.marker()} to the owner."


@tool_register(namespace="files", destructive=True)
@tool
def delete_file(file_ids: list[str]) -> str:
    """Permanently delete stored files, by id, after the owner confirms. Other
    copies of the same file are deleted with it, and the owner is shown where
    each is used. Their markers then read as deleted.

    Args:
        file_ids: the ids to delete (the `file:…` in their markers).
    """
    from gateway.factory import get_confirmation

    if not file_ids:
        return "Error: give at least one file id."
    targets: dict[str, store.FileRecord] = {}
    for raw in file_ids[:_DELETE_MAX]:
        rec = store.get(raw)
        if rec is None:
            return f"Nothing deleted: no file with id {raw!r}."
        if rec.state == "deleted":
            continue
        for r in [rec, *store.sharing_bytes(rec.id)]:
            targets.setdefault(r.id, r)
    if not targets:
        return "Nothing to delete: those files are already deleted."
    try:
        used = references.scan()
    except references.ReferenceScanFailed as e:
        return f"Nothing deleted: couldn't check where the files are used ({e})."

    asked = {store.get(raw).id for raw in file_ids[:_DELETE_MAX]}
    lines = [f"delete {len(targets)} file{'s' if len(targets) != 1 else ''} permanently:"]
    for rec in targets.values():
        line = f"• {rec.marker()} — {_when(rec)}"
        if rec.id not in asked:
            line += " (a copy of the same file)"
        holders = sorted({h.name for h in used.get(rec.id, [])})
        if holders:
            line += f" — used in: {', '.join(holders)}"
        lines.append(line)
    ids = list(targets)

    async def _run() -> str:
        return await asyncio.to_thread(_delete, ids)

    try:
        return get_confirmation().request_confirmation_sync(
            description="\n".join(lines), action_fn=_run,
            result_ok_text=f"Deleted {len(ids)} file{'s' if len(ids) != 1 else ''}.",
            result_cancel_text="Nothing was deleted.",
        )
    except Exception as e:
        return f"Error requesting confirmation: {e}"


def _delete(ids: list[str]) -> str:
    for file_id in ids:
        rec = store.get(file_id)
        if rec is not None and rec.state != "deleted":
            store.delete(file_id, "deleted by the owner")
    return f"Deleted {len(ids)} file(s)."


def _when(rec: store.FileRecord) -> str:
    return datetime.fromisoformat(rec.created).astimezone(ISRAEL_TZ).strftime("%Y-%m-%d %H:%M")
