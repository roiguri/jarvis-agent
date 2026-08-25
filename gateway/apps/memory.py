"""The memory app — a read-only browse of the agent's memory directory.

Walks MEMORY_DIR directly and returns structured JSON. It deliberately does not
call the memory *tools*: their output is English written for a model, so parsing
it here would ship a client that reads prose and breaks when a docstring is
reworded.

THE SECURITY LINE. Param values arrive uninterpreted — whatever relays them
bounds their length and nothing else, because judging a path would mean knowing
what this app means by one. So `../../secrets/.env` reaches these handlers
verbatim and resolution here is the only defence there is. That resolution is
`_get_safe_path`, imported rather than reimplemented: a second copy of a
security boundary is one that can drift from the original, and the copy is
always the one with less scrutiny on it.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any

import config
from gateway.apps.registry import (
    AppEntry,
    AppInvalidRequest,
    AppNotFound,
    AppSpec,
    register_app,
)

# Imported private-to-public across a package boundary on purpose: these two ARE
# the sandbox, and the alternative is a second implementation of it here.
from tools.core.memory import _DENIED_PREFIX, _get_safe_path

# A read returns the whole file in one JSON payload. Past this it stops being a
# thing a phone can render, so it is refused rather than truncated — silently
# truncated memory reads as complete, which is worse than a visible failure.
_MAX_READ_BYTES = 1024 * 1024


def _iso(ts: float) -> str:
    """Epoch seconds as ISO-8601 UTC, whole seconds."""
    return (
        datetime.fromtimestamp(ts, tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _relative(abs_path: str) -> str:
    """The MEMORY_DIR-relative form echoed back to the caller; "" is the root."""
    rel = os.path.relpath(abs_path, config.MEMORY_DIR)
    return "" if rel == "." else rel


def _contained(abs_path: str) -> bool:
    """Is `abs_path` still inside MEMORY_DIR once symlinks are followed?

    Listing entries come from scandir, never _get_safe_path, so this is
    their only symlink check; on already-resolved paths it is an extra guard.
    """
    real = os.path.realpath(abs_path)
    root = os.path.realpath(config.MEMORY_DIR)
    return real == root or real.startswith(root + os.sep)


def _resolve(path: str) -> str:
    """Resolve a caller-supplied path inside the sandbox.

    Translates the sandbox's own ValueError — traversal, or the deny-listed
    checkpointer DB — into an app-level failure, so a refusal reaches the caller
    as a refusal rather than an internal fault.
    """
    try:
        resolved = _get_safe_path(path)
    except ValueError as exc:
        raise AppInvalidRequest(str(exc)) from exc
    if not _contained(resolved):
        raise AppInvalidRequest(f"Path leaves the memory directory: {path}")
    return resolved


def _list_sync(path: str) -> dict[str, Any]:
    base = _resolve(path)
    if not os.path.exists(base):
        raise AppNotFound(f"No such path: {path or '/'}")
    if not os.path.isdir(base):
        raise AppInvalidRequest(f"Not a directory: {path}")

    entries: list[dict[str, Any]] = []
    with os.scandir(base) as it:
        for item in it:
            # _get_safe_path blocks *access* to the checkpointer DB, but a walk
            # enumerates names without going through it — so the deny-list has
            # to be applied again here or it shows up in the browser.
            if item.name.startswith(_DENIED_PREFIX):
                continue
            # A link pointing out of the tree is unreadable through _resolve, so
            # listing it would only offer an entry that cannot be opened.
            if item.is_symlink() and not _contained(item.path):
                continue
            if item.is_dir():
                # No size/modified on a directory: omitted rather than faked.
                entries.append({"name": item.name, "kind": "dir"})
            else:
                st = item.stat()
                entries.append(
                    {
                        "name": item.name,
                        "kind": "file",
                        "size": st.st_size,
                        "modified": _iso(st.st_mtime),
                    }
                )
    # Directories first, then files, each alphabetical — most of the memory
    # tree's files live under daily/ and heartbeat/, so surfacing those first
    # is what makes the root look like anything at all.
    entries.sort(key=lambda e: (e["kind"] != "dir", e["name"].lower()))
    return {"path": _relative(base), "entries": entries}


def _read_sync(path: str) -> dict[str, Any]:
    resolved = _resolve(path)
    if not os.path.exists(resolved):
        raise AppNotFound(f"No such file: {path}")
    if os.path.isdir(resolved):
        raise AppInvalidRequest(f"Is a directory: {path}")

    st = os.stat(resolved)
    if st.st_size > _MAX_READ_BYTES:
        raise AppInvalidRequest(
            f"File is {st.st_size} bytes, over the {_MAX_READ_BYTES} limit"
        )
    try:
        with open(resolved, "r", encoding="utf-8") as fh:
            content = fh.read()
    except UnicodeDecodeError as exc:
        # Better a clear refusal than mojibake presented as the file's contents.
        raise AppInvalidRequest(f"Not a UTF-8 text file: {path}") from exc
    return {
        "path": _relative(resolved),
        "content": content,
        "modified": _iso(st.st_mtime),
    }


# Every filesystem call above blocks, and the whole channel — poll loop, turn
# consumer, query drain — shares one event loop. Running these inline would
# freeze the poll and any in-flight turn, re-coupling exactly what answering
# queries on their own task decoupled.
async def _list(params: dict[str, str]) -> Any:
    return await asyncio.to_thread(_list_sync, (params.get("path") or "").strip())


async def _read(params: dict[str, str]) -> Any:
    path = (params.get("path") or "").strip()
    if not path:
        raise AppInvalidRequest("read requires a 'path' parameter")
    return await asyncio.to_thread(_read_sync, path)


# Read-only in v1: both entries are GET, so a channel that honours `method`
# refuses a write outright. A write entry arrives with the confirmation rules
# that protect the identity files, not before.
MEMORY_APP = register_app(
    AppSpec(
        ns="memory",
        name="Memory",
        entries=(
            AppEntry(id="list", method="GET", params=("path",), handler=_list),
            AppEntry(id="read", method="GET", params=("path",), handler=_read),
        ),
    )
)
