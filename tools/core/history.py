import asyncio
import json
import os
import tempfile
import threading
from datetime import datetime, timedelta, timezone

from langchain_core.tools import tool

import config
from episodes import search as episode_search
from tools.registry import tool_register

# Activity logs: append-only JSONL the agent never file-reads; the episode store
# (episodes/) holds the same activity and is what search_history queries. Lives outside
# the memory surface (not the memory dir). config creates DATA_DIR/logs at import
# — the write path here just open()s, with no lazy mkdir of its own.
_LOG_DIR = os.path.join(config.DATA_DIR, "logs")

NOTIFICATION_LOG = os.path.join(_LOG_DIR, "notifications.jsonl")
CHAT_LOG = os.path.join(_LOG_DIR, "chat_history.jsonl")
LOG_RETENTION_DAYS = 90

# Serializes appends across all JSONL writers in this process. Append-mode
# writes are atomic only up to PIPE_BUF (~4 KB); a row can exceed that, and
# heartbeat + user turns write concurrently. Same pattern as _WRITE_LOCK in tools/core/memory.py.
_APPEND_LOCK = threading.Lock()


def trim_log(path: str, retention_days: int = LOG_RETENTION_DAYS) -> None:
    """Drop lines older than retention_days from a JSONL log file.

    Lines without a parseable 'ts' field are kept (conservative — don't drop
    what we can't date). Write is atomic: temp file + rename so the file is
    never left in a partial state.
    """
    if not os.path.exists(path):
        return
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    kept = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                ts = datetime.fromisoformat(record["ts"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts < cutoff:
                    continue
            except (KeyError, ValueError, json.JSONDecodeError):
                pass  # keep lines we can't parse or date
            kept.append(line)
    dir_ = os.path.dirname(path)
    with tempfile.NamedTemporaryFile("w", dir=dir_, delete=False,
                                     encoding="utf-8", suffix=".tmp") as tmp:
        tmp.write("\n".join(kept) + ("\n" if kept else ""))
        tmp_path = tmp.name
    os.replace(tmp_path, path)


def _append_line(path: str, record: dict) -> None:
    """Append one JSON record to a JSONL file. Synchronous, process-locked."""
    line = json.dumps(record, ensure_ascii=False) + "\n"
    with _APPEND_LOCK:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)


def append_notification_log(event_type: str, message: str, metadata: dict | None = None) -> None:
    """Append one notification event to notifications.jsonl. Synchronous."""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event_type,
        "message": message,
    }
    if metadata:
        record.update(metadata)
    _append_line(NOTIFICATION_LOG, record)


async def async_append_notification_log(event_type: str, message: str, metadata: dict | None = None) -> None:
    """Async wrapper around append_notification_log for use in async contexts."""
    await asyncio.to_thread(append_notification_log, event_type, message, metadata)


def append_chat_log(role: str, content: str, thread_id: str, file_ids: list[str] | None = None) -> None:
    """Append one chat message to chat_history.jsonl. Synchronous.

    Args:
        role: "user" or "assistant"
        content: text content
        thread_id: conversation thread identifier
        file_ids: ids of the files the message brought (e.g. ["file:k7m2q9xd"])
    """
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "thread_id": thread_id,
        "role": role,
        "content": content,
    }
    if file_ids:
        record["media"] = file_ids
    _append_line(CHAT_LOG, record)


@tool_register(namespace="core")
@tool
def search_history(query: str = "", since: str | None = None, until: str | None = None,
                   kind: str = "all", limit: int = 10) -> str:
    """Search past activity: Roi's messages and your replies, tool calls and
    their results, messages sent to Roi, and heartbeat activity. Use it for
    anything no longer in this conversation. Each hit includes its turn's
    message and reply.

    Args:
        query: keywords, any language. Empty returns the newest entries.
        since: optional. '2026-05-08' = start of that Israel day; or a full
            ISO 8601 timestamp with a UTC offset.
        until: optional, same formats; a plain date includes that whole day.
        kind: "chat", "notifications" (sent to Roi), "background"
            (heartbeat), or "all".
        limit: max turns or entries (up to 30).
    """
    return episode_search.search(query, since, until, kind, limit)
