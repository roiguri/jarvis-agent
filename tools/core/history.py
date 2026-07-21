import asyncio
import json
import os
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from langchain_core.tools import tool

from tools.registry import tool_register

# Tool-owned activity logs: append-only JSONL the agent never file-reads —
# queried only via get_chat_history / get_notification_history. Lives outside
# the memory surface (not /app/jarvis_memory).
_LOG_DIR = "/app/jarvis_data/logs"
os.makedirs(_LOG_DIR, exist_ok=True)

NOTIFICATION_LOG = os.path.join(_LOG_DIR, "notifications.jsonl")
CHAT_LOG = os.path.join(_LOG_DIR, "chat_history.jsonl")
LOG_RETENTION_DAYS = 90

# Serializes appends across all JSONL writers in this process. Append-mode
# writes are atomic only up to PIPE_BUF (~4 KB); tool_calls.jsonl carries
# tracebacks that can exceed that, and heartbeat + user turns write
# concurrently. Same pattern as _WRITE_LOCK in tools/core/memory.py.
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


def _read_tail(path: str, limit: int) -> list[dict]:
    """Read the last `limit` lines from a JSONL file efficiently (seeks from end)."""
    if not os.path.exists(path):
        return []
    with open(path, "rb") as f:
        f.seek(0, 2)  # seek to end
        end = f.tell()
        if end == 0:
            return []
        buf = b""
        pos = end - 1
        newlines_found = 0
        # Walk backwards counting newlines to find the last `limit` lines
        while pos >= 0 and newlines_found <= limit:
            f.seek(pos)
            char = f.read(1)
            if char == b"\n" and pos != end - 1:
                newlines_found += 1
            buf = char + buf
            pos -= 1
        lines = buf.decode("utf-8").strip().splitlines()
        records = []
        for line in lines[-limit:]:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return records


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


def append_chat_log(role: str, content: str, thread_id: str, media_paths: list[str] | None = None) -> None:
    """Append one chat message to chat_history.jsonl. Synchronous.
    
    Args:
        role: "user" or "assistant"
        content: text content
        thread_id: conversation thread identifier
        media_paths: optional list of relative paths to media files (e.g. ["media/img_123.jpg"])
    """
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "thread_id": thread_id,
        "role": role,
        "content": content,
    }
    if media_paths:
        record["media"] = media_paths
    _append_line(CHAT_LOG, record)


@tool_register(namespace="core")
@tool
def get_notification_history(limit: int = 20) -> str:
    """Read the last N notification events that Jarvis sent (media ready, upgrades,
    failures, system alerts). Use when the user asks what notifications were sent recently."""
    records = _read_tail(NOTIFICATION_LOG, limit)
    if not records:
        return "No notification history found."
    lines = []
    for r in records:
        ts = r.get("ts", "?")
        event = r.get("event", "?")
        message = r.get("message", "").replace("\n", " ").strip()
        if len(message) > 120:
            message = message[:120] + "..."
        lines.append(f"[{ts}] ({event}) {message}")
    return "\n".join(lines)


@tool_register(namespace="core")
@tool
def get_chat_history(limit: int = 20, since: str | None = None) -> str:
    """Read chat exchanges between Roi and Jarvis. Use when you need history
    from earlier days — today's chat is already in context.

    Args:
        limit: max number of entries to return.
        since: optional ISO 8601 timestamp, offset required. Days are Israel
               time: '2026-05-08T00:00:00+03:00', not '...Z' (starts at 03:00).
    """
    if since is not None:
        # Time-filtered path: read all entries and filter, then take last `limit`.
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError:
            return f"Invalid 'since' timestamp: {since!r}. Use ISO 8601 with an offset, e.g. '2026-05-08T00:00:00+03:00'."
        if since_dt.tzinfo is None:
            # Log entries are offset-aware; comparing them against a naive
            # bound raises TypeError mid-scan. Reject with the offset spelled
            # out rather than guessing which day boundary was meant.
            return f"Ambiguous 'since' timestamp: {since!r} has no UTC offset. Use e.g. '{since}+03:00' for Israel time."
        if not os.path.exists(CHAT_LOG):
            return "No chat history found."
        records = []
        with open(CHAT_LOG, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    ts = datetime.fromisoformat(rec["ts"])
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if ts >= since_dt:
                        records.append(rec)
                except (KeyError, ValueError, json.JSONDecodeError):
                    pass
        records = records[-limit:]
    else:
        records = _read_tail(CHAT_LOG, limit)

    if not records:
        return "No chat history found."
    lines = []
    for r in records:
        ts = r.get("ts", "?")
        role = r.get("role", "?")
        content = r.get("content", "").replace("\n", " ").strip()
        if len(content) > 200:
            content = content[:200] + "..."
        lines.append(f"[{ts}] {role}: {content}")
    return "\n".join(lines)
