"""Search over the episode store, rendered for the model.

Keyword search (FTS5, best match first, newest first on ties) or, with no
keywords, the newest entries in a date range. Hits are grouped by turn and
shown with the turn's owner message and final reply around them, so a match
inside a tool result still reads as part of a conversation.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone

from timeutils import ISRAEL_TZ
from episodes import store

KINDS = ("all", "chat", "notifications", "background")
_KIND_FILTER = {
    "all": "1 = 1",
    "chat": "e.scope = 'user' AND e.role <> 'jarvis_sent'",
    "notifications": "e.role = 'jarvis_sent'",
    "background": "e.scope = 'heartbeat'",
}
_OUTPUT_CAP = 8000      # chars for the whole answer
_LINE_CAP = 400         # chars for an owner or Jarvis line
_RESULT_CAP = 600       # chars for a matched tool result or call


def parse_bound(value: str | None, *, end: bool = False) -> tuple[datetime | None, str | None]:
    """A plain date is that Israel day (its start, or for ``end`` the next
    day's start); a full ISO timestamp needs a UTC offset. Returns (bound,
    error message)."""
    if not value:
        return None, None
    try:
        day = date.fromisoformat(value)
        start = datetime(day.year, day.month, day.day, tzinfo=ISRAEL_TZ)
        return (start + timedelta(days=1) if end else start), None
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None, (f"Invalid date {value!r}. Use 'YYYY-MM-DD' for an Israel day, "
                      "or a full ISO 8601 timestamp with an offset.")
    if dt.tzinfo is None:
        return None, (f"Ambiguous date {value!r}: no UTC offset. Pass a plain 'YYYY-MM-DD' "
                      "for an Israel day, or include an offset.")
    return dt, None


def _match_expr(query: str, any_term: bool) -> str | None:
    terms = re.findall(r"\w+", query)
    if not terms:
        return None
    # Prefix match for real words, so "walk" also finds "walked" and "walking".
    quoted = [f'"{t}"*' if len(t) >= 3 else f'"{t}"' for t in terms]
    return (" OR " if any_term else " ").join(quoted)


def _local(ts: str) -> str:
    return datetime.fromisoformat(ts).astimezone(ISRAEL_TZ).strftime("%Y-%m-%d %H:%M")


def _cut(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _label(row: dict) -> str:
    if row["role"] == "jarvis_sent":
        event = (json.loads(row["meta"]) if row.get("meta") else {}).get("event")
        return "sent to you" + (f", {event}" if event else "")
    if row["scope"] == "heartbeat":
        return row["source"]  # heartbeat | wake
    return "chat" + (f", {row['channel']}" if row.get("channel") else "") + \
        (", slash command" if row["source"] == "slash_command" else "")


def _render_line(row: dict, cap: int) -> str:
    role, content = row["role"], row["content"]
    if role == "owner":
        return f"You: {_cut(content, cap)}"
    if role == "jarvis":
        return f"Jarvis: {_cut(content, cap)}"
    if role == "jarvis_sent":
        return f"Sent: {_cut(content, cap)}"
    if role == "system":
        return f"Instruction: {_cut(content, cap)}"
    if role == "tool_call":
        return f"[called {row['tool_name']}] {_cut(content, cap)}"
    return f"[{row['tool_name']} result] {_cut(content, cap)}"


def search(query: str = "", since: str | None = None, until: str | None = None,
           kind: str = "all", limit: int = 10) -> str:
    if kind not in KINDS:
        return f"Unknown kind {kind!r}. Use one of: {', '.join(KINDS)}."
    since_dt, err = parse_bound(since)
    if err:
        return err
    until_dt, err = parse_bound(until, end=True)
    if err:
        return err
    limit = max(1, min(int(limit or 10), 30))

    # ts is stored as UTC ISO text, so bounds are compared as UTC text too.
    where = [_KIND_FILTER[kind]]
    params: list = []
    if since_dt:
        where.append("e.ts >= ?")
        params.append(since_dt.astimezone(timezone.utc).isoformat())
    if until_dt:
        where.append("e.ts < ?")
        params.append(until_dt.astimezone(timezone.utc).isoformat())

    hits: list[dict] = []
    for any_term in (False, True):
        expr = _match_expr(query, any_term)
        if expr is None:
            hits = store.rows(
                f"SELECT e.* FROM episodes e WHERE {' AND '.join(where)} ORDER BY e.ts DESC, e.id DESC LIMIT 300",
                tuple(params))
            break
        hits = store.rows(
            "SELECT e.* FROM episodes_fts f JOIN episodes e ON e.id = f.rowid "
            f"WHERE episodes_fts MATCH ? AND {' AND '.join(where)} "
            "ORDER BY bm25(episodes_fts), e.ts DESC LIMIT 300",
            (expr, *params))
        if hits:
            break
    if not hits:
        return "Nothing found." + (" Try other keywords or a wider date range." if query else "")

    groups: list[tuple[str, list[dict]]] = []
    index: dict[str, int] = {}
    for h in hits:
        key = h["turn_id"] or f"row{h['id']}"
        if key not in index:
            if len(groups) >= limit:
                continue
            index[key] = len(groups)
            groups.append((key, []))
        groups[index[key]][1].append(h)

    out: list[str] = []
    used = 0
    for shown, (key, matched) in enumerate(groups):
        lines = _render_group(key, matched)
        block = "\n".join(lines)
        if used + len(block) > _OUTPUT_CAP and out:
            out.append(f"({len(groups) - shown} more matching entries not shown; narrow the search.)")
            break
        out.append(block)
        used += len(block)
    return "\n\n".join(out)


def _render_group(key: str, matched: list[dict]) -> list[str]:
    first = min(matched, key=lambda r: r["id"])
    header = f"── {_local(first['ts'])} ({_label(first)})"
    if key.startswith("row"):
        return [header] + [_render_line(r, _RESULT_CAP) for r in sorted(matched, key=lambda r: r["id"])]
    turn = store.rows("SELECT * FROM episodes WHERE turn_id = ? ORDER BY id", (key,))
    matched_ids = {r["id"] for r in matched}
    inputs = [r for r in turn if r["role"] in ("owner", "system")]
    replies = [r for r in turn if r["role"] == "jarvis"]
    keep = {r["id"] for r in inputs[:1]} | {r["id"] for r in replies[-1:]} | matched_ids
    lines = [header]
    for r in turn:
        if r["id"] in keep:
            cap = _LINE_CAP if r["role"] in ("owner", "jarvis", "system") else _RESULT_CAP
            lines.append(_render_line(r, cap))
    return lines
