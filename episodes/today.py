"""The today view: what the heartbeat did today, for the user-scope prompt.

One line per heartbeat tick that acted (or messaged the owner) and per
scheduled wake, from the tick's own heartbeat_respond call in the episode
store. Ticks that did nothing are left out. Built by code on every call, so it
is exact and current, unlike the model-written daily log.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from timeutils import ISRAEL_TZ
from episodes import store

MAX_LINES = 20

logger = logging.getLogger(__name__)


def background_today(now: datetime | None = None) -> str:
    """The prompt section, or "" when nothing happened (or the store can't be read)."""
    try:
        return _build(now or datetime.now(timezone.utc))
    except Exception:
        logger.exception("today view failed; prompt goes without it")
        return ""


def _build(now: datetime) -> str:
    local = now.astimezone(ISRAEL_TZ)
    day_start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    acks = store.rows(
        "SELECT a.ts, a.content, i.source, i.meta FROM episodes a "
        "LEFT JOIN episodes i ON i.turn_id = a.turn_id AND i.role = 'system' "
        "WHERE a.scope = 'heartbeat' AND a.role = 'tool_call' AND a.tool_name = 'heartbeat_respond' "
        "AND a.ts >= ? ORDER BY a.ts, a.id",
        (day_start.isoformat(),),
    )
    lines = []
    for ack in acks:
        try:
            args = json.loads(ack["content"])
        except ValueError:
            continue
        acted = args.get("acted_tasks") or []
        is_wake = ack["source"] == "wake"
        if not (acted or args.get("notify") or is_wake):
            continue
        if is_wake:
            task = (json.loads(ack["meta"]) if ack["meta"] else {}).get("task")
            label = f"wake ({task})" if task else "wake"
        else:
            label = ", ".join(acted) or "tick"
        when = datetime.fromisoformat(ack["ts"]).astimezone(ISRAEL_TZ).strftime("%H:%M")
        sent = " [messaged you]" if args.get("notify") else ""
        summary = " ".join(str(args.get("summary") or "").split())
        lines.append(f"- {when} {label}: {summary}{sent}")
    if not lines:
        return ""
    return "--- Today in the background (heartbeat) ---\n" + "\n".join(lines[-MAX_LINES:])
