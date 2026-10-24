"""The file store's start-of-service upkeep: import old caches, collect unused
bytes, check free space. Each step is independent; one failing is logged and
the rest still run, and none of them stops the service from starting."""

from __future__ import annotations

import logging
from typing import Iterable

from files import legacy, store

logger = logging.getLogger(__name__)


def prepare(legacy_entries: Iterable[tuple[str, str]]) -> str:
    """Run the upkeep; return a one-line summary for the startup log."""
    parts = []
    try:
        parts.append(f"imported {legacy.import_caches(legacy_entries)} old files")
    except Exception:
        logger.exception("file store: legacy import failed (retried next start)")
        parts.append("legacy import failed")
    try:
        parts.append(f"collected {len(store.collect_unused())} unused")
    except Exception:
        logger.exception("file store: collecting unused bytes failed")
    try:
        status = store.disk_status()
        parts.append(f"{status['stored_bytes'] / 1024**2:.0f} MB stored, "
                     f"{status['free_bytes'] / 1024**3:.1f} GB free")
        if status["low"]:
            logger.warning("file store: free space is low (%.1f GB)", status["free_bytes"] / 1024**3)
    except Exception:
        logger.exception("file store: disk check failed")
    return ", ".join(parts)
