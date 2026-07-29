"""jarvis-app-owned media cache.

The jarvis-app channel downloads inbound attachments and stores them here,
returning an **absolute** path. Nothing in `tools/*` or `agent.py` imports this
module or knows where app media lives — the channel owns it end to end, the same
channel-owns-storage rule Telegram's `media_cache.py` follows. The agent receives
the absolute path via `InboundMessage.attachments[].path` and opens it directly.
"""

import os
from datetime import datetime, timedelta, timezone

# Channel-owned cache dir, resolved relative to this file (no hardcoded /app path,
# no dependency on the memory surface). Gitignored.
_CACHE_DIR = os.path.join(os.path.dirname(__file__), "media_cache")
os.makedirs(_CACHE_DIR, exist_ok=True)

_RETENTION_DAYS = 90

# The hub's attachment kinds are image | audio | file. Extensions are cosmetic —
# the agent reads bytes and branches on mime_type, not the filename — so an
# unknown kind just gets no suffix.
_EXT = {"image": ".jpg", "audio": ".ogg", "file": ""}


def save(data: bytes, kind: str, attachment_id: str) -> str:
    """Persist an inbound blob; return its **absolute** path.

    The filename embeds the hub's `att_…` id (unique per blob). `basename`
    strips any path separators as defense in depth — the caller already
    validates the id shape, so nothing untrusted should reach here.
    """
    filename = os.path.basename(f"{kind}_{attachment_id}{_EXT.get(kind, '')}")
    abs_path = os.path.join(_CACHE_DIR, filename)
    with open(abs_path, "wb") as f:
        f.write(data)
    return abs_path


def trim(retention_days: int = _RETENTION_DAYS) -> None:
    """Evict cache files not modified within retention_days (mtime proxy — blobs
    are written once on arrival and never modified)."""
    if not os.path.isdir(_CACHE_DIR):
        return
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    for filename in os.listdir(_CACHE_DIR):
        filepath = os.path.join(_CACHE_DIR, filename)
        if not os.path.isfile(filepath):
            continue
        mtime = datetime.fromtimestamp(os.path.getmtime(filepath), tz=timezone.utc)
        if mtime < cutoff:
            os.remove(filepath)


# Channel owns its own cache hygiene: prune stale blobs once at import.
trim()
