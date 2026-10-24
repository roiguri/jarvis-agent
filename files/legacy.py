"""One-time import of the channels' old media caches into the store.

TODO(#185): remove this module and its startup call once rolling back past the
file store is no longer wanted.

Runs at every start and is idempotent: a path already imported is skipped, so it
also picks up files older code wrote after a deploy and before the restart. It
copies; the caches stay so older code still finds them. Each old file becomes one
record per arrival the episode store remembers (`[attached: <path>]` on an owner
message), with that message's time, turn and text; a file no message mentions
gets one record dated by the file itself. The old path stays mapped to its
records, so old history still resolves.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Iterable

from episodes import store as episodes
from files import store

logger = logging.getLogger(__name__)

_ATTACHED_LINE = re.compile(r"\n?\[attached: [^\]\n]*\]")


def import_caches(entries: Iterable[tuple[str, str]]) -> int:
    """Import each (absolute path, source) not imported yet. Returns records made.
    One unreadable file is logged and skipped; it is retried on the next start."""
    made = 0
    for path, source in entries:
        if store.by_legacy_path(path) is not None:
            continue
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            logger.warning("legacy import: cannot read %s: %s", path, e)
            continue
        if not data:
            continue
        arrivals = episodes.rows(
            "SELECT ts, turn_id, content FROM episodes WHERE role = 'owner'"
            " AND instr(content, ?) > 0 ORDER BY ts", (f"[attached: {path}]",))
        if not arrivals:
            mtime = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
            arrivals = [{"ts": mtime.isoformat(), "turn_id": None, "content": ""}]
        for arrival in arrivals:
            try:
                rec = store.add(
                    data, source=source, filename=None, turn_id=arrival["turn_id"],
                    caption=_ATTACHED_LINE.sub("", arrival["content"]).strip() or None,
                    origin={"legacy_path": path}, created=arrival["ts"])
            except Exception as e:
                logger.warning("legacy import: cannot store %s: %s", path, e)
                break
            store.map_legacy_path(path, rec.id)
            made += 1
    return made
