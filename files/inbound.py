"""Saving the files a message brings, before its turn runs.

Channels hand over attachments as plain dicts — kind, mime_type, data (bytes),
filename, source, origin_id — and never store anything themselves. Each one
becomes a file record here. A file that can't be saved becomes a note in the
message instead, so the reply can say so rather than ignore it.
"""

from __future__ import annotations

import logging

from files import store
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
