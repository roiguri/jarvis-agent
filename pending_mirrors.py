"""Pending-mirror drain — proactive sends become owner-thread history.

notifications.jsonl (Outbox-written on delivery success) is the queue; the
cursor file holds the last mirrored row's timestamp. The next user turn on the
owner thread drains rows past the cursor as ONE user-role block (the reducer
drops leading assistant-role messages, and one message keeps the Gemini wire
alternating), and the cursor advances only after that turn completes — a
failed turn re-delivers, never loses. Rows older than 24 hours never drain,
which bounds the first run and any backlog. A rolling window, not a calendar
day: a day boundary in any fixed zone lands mid-evening somewhere the owner
travels, silently dropping a send between its delivery and the owner's reply.
"""

import datetime as _dt
import json
import logging
import os

import config

logger = logging.getLogger(__name__)

CURSOR_PATH = os.path.join(config.DATA_DIR, "agent", "mirror_cursor.json")
_NOTIF_LOG = os.path.join(config.DATA_DIR, "logs", "notifications.jsonl")

PREFIX = {
    "heartbeat": "[Heartbeat]",
    "reminder": "[Reminder]",
    "notification": "[Notification]",
    "llm_notification": "[Notification]",
}
HEADER = "[Messages Jarvis sent you since the last turn:]"
# Bounds one drain's block. Past the cap the OLDEST pending rows are dropped —
# bounded prompts win over total delivery once a day backs up past 20 sends.
MAX_ENTRIES = 20
ENTRY_CAP = 2000


def drain_pending() -> tuple[str | None, str | None]:
    """(user-role block, cursor value to stamp after the turn) or (None, None).

    ISO timestamps from a single writer compare lexicographically; equal-stamp
    splits and clock steps degrade to re-delivery, never loss.
    """
    if not os.path.exists(_NOTIF_LOG):
        return None, None
    try:
        with open(CURSOR_PATH, "r", encoding="utf-8") as f:
            cursor = json.load(f).get("last_ts", "")
    except (OSError, ValueError):
        cursor = ""
    since = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=24)
    entries: list[str] = []
    last_ts: str | None = None
    try:
        with open(_NOTIF_LOG, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    ts_s = rec["ts"]
                    event = rec.get("event", "")
                    if event == "heartbeat_outcome":  # 1d: context, not an utterance
                        continue
                    ts = _dt.datetime.fromisoformat(ts_s)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=_dt.timezone.utc)
                    if ts < since or (cursor and ts_s <= cursor):
                        continue
                    text = (rec.get("message") or "").strip()[:ENTRY_CAP]
                    if not text:
                        continue
                    entries.append(f"{PREFIX.get(event, '[Notification]')} {text}")
                    last_ts = ts_s
                except (KeyError, ValueError, json.JSONDecodeError):
                    continue
    except OSError:
        return None, None
    if not entries:
        return None, None
    entries = entries[-MAX_ENTRIES:]
    return HEADER + "\n" + "\n".join(entries), last_ts


def advance_cursor(last_ts: str) -> None:
    """Stamp after the turn completed; a failure re-delivers, never loses."""
    try:
        os.makedirs(os.path.dirname(CURSOR_PATH), exist_ok=True)
        tmp = CURSOR_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"last_ts": last_ts}, f)
        os.replace(tmp, CURSOR_PATH)
    except OSError:
        logger.exception("mirror cursor advance failed; rows will re-deliver")
