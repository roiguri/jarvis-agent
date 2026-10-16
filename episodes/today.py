"""The today view and the day view, built by code from the episode store.

The today view: one line per heartbeat tick that acted (or messaged the owner),
per scheduled wake, and per background turn that failed — a tick's own
heartbeat_respond call, or its failure record. Ticks that did nothing are left
out. Shown in both scopes: the user conversation learns what happened in the
background, and a tick (which keeps no conversation) learns what earlier ticks
did today.

The day view: the same background lines for any day, plus the owner's messages
and what Jarvis sent him that day. The heartbeat reads yesterday's; /logs shows
any day's.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, time, timedelta, timezone

from timeutils import ISRAEL_TZ
from episodes import store

MAX_LINES = 20
DAY_CONVERSATION_LINES = 40
DAY_TEXT_CAP = 160

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
    lines = _background_lines(day_start, None)
    if not lines:
        return ""
    return "--- Today in the background (heartbeat) ---\n" + "\n".join(f"- {line}" for line in lines[-MAX_LINES:])


def _bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(), ISRAEL_TZ)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def _window(start: datetime, end: datetime | None) -> tuple[str, tuple]:
    if end is None:
        return "AND {t}.ts >= ?", (start.isoformat(),)
    return "AND {t}.ts >= ? AND {t}.ts < ?", (start.isoformat(), end.isoformat())


def _background_lines(start: datetime, end: datetime | None) -> list[str]:
    """What the heartbeat did between ``start`` and ``end`` (open-ended when
    None), oldest first."""
    where, params = _window(start, end)
    acks = store.rows(
        "SELECT a.ts, a.content, i.source, i.meta FROM episodes a "
        "LEFT JOIN episodes i ON i.turn_id = a.turn_id AND i.role = 'system' "
        "AND (i.meta IS NULL OR i.meta NOT LIKE '%\"failed\"%') "
        "WHERE a.scope = 'heartbeat' AND a.role = 'tool_call' AND a.tool_name = 'heartbeat_respond' "
        + where.format(t="a") + " ORDER BY a.ts, a.id",
        params,
    )
    failures = store.rows(
        "SELECT f.ts, f.content, f.source, i.meta FROM episodes f "
        "LEFT JOIN episodes i ON i.turn_id = f.turn_id AND i.role = 'system' AND i.id < f.id "
        "WHERE f.scope = 'heartbeat' AND f.role = 'system' AND f.meta LIKE '%\"failed\"%' "
        + where.format(t="f") + " ORDER BY f.ts, f.id",
        params,
    )
    lines = []
    for f in failures:
        task = (json.loads(f["meta"]) if f["meta"] else {}).get("task")
        label = "wake" + (f" ({task})" if task else "") if f["source"] == "wake" else "tick"
        cause = " ".join(f["content"].split())
        lines.append((f["ts"], f"{_hm(f['ts'])} {label}: {cause}"))
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
        sent = " [messaged you]" if args.get("notify") else ""
        summary = " ".join(str(args.get("summary") or "").split())
        lines.append((ack["ts"], f"{_hm(ack['ts'])} {label}: {summary}{sent}"))
    return [line for _, line in sorted(lines)]


def _conversation_lines(start: datetime, end: datetime) -> list[str]:
    """The owner's messages and Jarvis's own sends between ``start`` and
    ``end``, one shortened line each, the newest DAY_CONVERSATION_LINES."""
    rows = store.rows(
        "SELECT ts, role, content FROM episodes WHERE ts >= ? AND ts < ? AND "
        "((role = 'owner' AND scope = 'user' AND source <> 'slash_command') OR role = 'jarvis_sent') "
        "ORDER BY ts, id",
        (start.isoformat(), end.isoformat()),
    )
    lines = []
    for r in rows[-DAY_CONVERSATION_LINES:]:
        who = "Roi" if r["role"] == "owner" else "sent"
        text = " ".join(r["content"].split())
        if len(text) > DAY_TEXT_CAP:
            text = text[:DAY_TEXT_CAP - 1] + "…"
        lines.append(f"{_hm(r['ts'])} {who}: {text}")
    return lines


def day_view(day: date) -> tuple[list[str], list[str]]:
    """One Israel day: (background lines, conversation lines), oldest first,
    as bare text — each caller adds its own bullets."""
    start, end = _bounds(day)
    return _background_lines(start, end), _conversation_lines(start, end)


def yesterday(now: datetime | None = None) -> str:
    """Yesterday's day view as a prompt section, or "" when nothing happened."""
    try:
        day = (now or datetime.now(timezone.utc)).astimezone(ISRAEL_TZ).date() - timedelta(days=1)
        background, conversation = day_view(day)
    except Exception:
        logger.exception("yesterday view failed; prompt goes without it")
        return ""
    if not (background or conversation):
        return ""
    parts = [f"--- Yesterday, {day:%A %Y-%m-%d} ---"]
    if background:
        parts += ["In the background:", *(f"- {line}" for line in background)]
    if conversation:
        parts += ["Conversation (Roi's messages and what was sent to him):", *(f"- {line}" for line in conversation)]
    return "\n".join(parts)


def _hm(ts: str) -> str:
    return datetime.fromisoformat(ts).astimezone(ISRAEL_TZ).strftime("%H:%M")
