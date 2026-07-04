"""Telegram-owned media cache.

The Telegram channel downloads inbound blobs and stores them here, returning
an **absolute** path. Nothing in `tools/*` or `agent.py` imports this module
or knows where Telegram media lives — the channel owns it end to end (a future
channel ships its own `gateway/<ch>/media_cache.py`). The agent receives the
absolute path via `InboundMessage.attachments[].path` and opens it directly.
"""

import os
from datetime import datetime, timedelta, timezone

# Channel-owned cache dir, resolved relative to this file (no hardcoded
# /app path, no dependency on the memory surface). Gitignored.
_CACHE_DIR = os.path.join(os.path.dirname(__file__), "media_cache")
os.makedirs(_CACHE_DIR, exist_ok=True)

_RETENTION_DAYS = 90

_EXT = {"image": ".jpg", "video": ".mp4", "audio": ".ogg"}


def save(data: bytes, kind: str, file_id: str) -> str:
    """Persist an inbound blob; return its **absolute** path.

    kind ∈ {"image","video","audio"}. The filename embeds the Telegram
    file_id so the same media re-resolves across turns without re-download.
    """
    filename = f"{kind}_{file_id[:20]}{_EXT.get(kind, '')}"
    abs_path = os.path.join(_CACHE_DIR, filename)
    with open(abs_path, "wb") as f:
        f.write(data)
    return abs_path


def trim(retention_days: int = _RETENTION_DAYS) -> None:
    """Evict cache files not modified within retention_days (mtime proxy —
    blobs are written once on arrival and never modified)."""
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


# Channel owns its own cache hygiene: prune stale blobs once at process start
# (same timing as the old main.py startup trim, now with no cross-layer call).
trim()
