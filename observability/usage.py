"""Cost / usage rollups over turns.jsonl.

Two query surfaces use this module:
- ``/usage`` slash command (gateway/commands/handlers.py) — user-facing.
- Ad-hoc Python (REPL, one-off scripts, future ``scripts/usage_analysis.py``).

Both call the same rollup function. The slash-command handler does date
parsing; this module takes already-parsed `datetime` boundaries so the
functions stay pure and easy to test.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from observability.telemetry import TURNS_LOG

_IL_TZ = ZoneInfo("Asia/Jerusalem")

GroupBy = Literal["day", "week", "scope", "day+scope"]

# USD per million tokens. Update from https://ai.google.dev/pricing when the
# model changes. Cache-reads are billed separately at a discounted rate; the
# rate below is a best-effort estimate (Gemini Flash family historically
# ~25% of the normal input price) — verify against the live pricing page
# before reading dollar figures literally.
MODEL_PRICES: dict[str, dict[str, float]] = {
    "gemini-3-flash-preview": {
        "input_per_m": 0.075,
        "cache_read_per_m": 0.01875,
        "output_per_m": 0.30,
    },
}
_ZERO_PRICE = {"input_per_m": 0.0, "cache_read_per_m": 0.0, "output_per_m": 0.0}


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------


def estimate_usd(
    input_tokens: int,
    cache_read_tokens: int,
    output_tokens: int,
    model: str | None,
) -> float:
    """Estimate USD cost of one turn. cache_read_tokens are *part of* the
    reported input_tokens (Gemini bills them separately at a lower rate), so
    we subtract them from the billable-input bucket before applying rates."""
    p = MODEL_PRICES.get(model or "", _ZERO_PRICE)
    billable_input = max(0, int(input_tokens) - int(cache_read_tokens))
    return (
        billable_input * p["input_per_m"] / 1_000_000
        + int(cache_read_tokens) * p["cache_read_per_m"] / 1_000_000
        + int(output_tokens) * p["output_per_m"] / 1_000_000
    )


# ---------------------------------------------------------------------------
# Raw load
# ---------------------------------------------------------------------------


def load_turns(
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[dict]:
    """Read turns.jsonl, parse each line, filter by [since, until) timestamps.

    Missing or unparseable lines are skipped silently — the log is an
    audit-trail, not a strict format. Returns records in disk order
    (chronological by append, with no sort applied)."""
    if not os.path.exists(TURNS_LOG):
        return []
    out: list[dict] = []
    with open(TURNS_LOG, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                ts = datetime.fromisoformat(r["ts"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except (KeyError, ValueError, json.JSONDecodeError):
                continue
            if since is not None and ts < since:
                continue
            if until is not None and ts >= until:
                continue
            out.append(r)
    return out


# ---------------------------------------------------------------------------
# Rollups
# ---------------------------------------------------------------------------


def _bucket_key(rec: dict, group_by: GroupBy) -> str:
    """Compose the grouping key for one record. Israel-local date so the
    bucket boundary matches the user's day, not UTC midnight."""
    ts = datetime.fromisoformat(rec["ts"])
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    local = ts.astimezone(_IL_TZ)
    day = local.date().isoformat()
    scope = rec.get("scope", "?")
    if group_by == "day":
        return day
    if group_by == "scope":
        return scope
    if group_by == "day+scope":
        return f"{day} {scope}"
    if group_by == "week":
        iso = local.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    raise ValueError(f"unknown group_by: {group_by!r}")


def _empty_bucket(key: str) -> dict:
    return {
        "group": key,
        "turns": 0,
        "llm_calls": 0,
        "tool_calls": 0,
        "input_tokens": 0,
        "cache_read_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "no_action_count": 0,
        "errors": 0,
        "usd_cost": 0.0,
    }


def summarize_usage(
    since: datetime | None = None,
    until: datetime | None = None,
    group_by: GroupBy = "day",
    scope_filter: str | None = None,
) -> list[dict]:
    """Group + roll up turns in [since, until). Returns rows sorted ascending
    by group key. Each row also carries ``no_action_rate`` for convenience.

    Pure: no I/O beyond load_turns; no global state. Suitable for REPL use:
        >>> from tools.core.usage import summarize_usage
        >>> rows = summarize_usage(group_by="day+scope")
    """
    turns = load_turns(since, until)
    if scope_filter:
        turns = [t for t in turns if t.get("scope") == scope_filter]
    buckets: dict[str, dict] = {}
    for t in turns:
        key = _bucket_key(t, group_by)
        b = buckets.setdefault(key, _empty_bucket(key))
        b["turns"] += 1
        b["llm_calls"] += int(t.get("llm_calls") or 0)
        b["tool_calls"] += int(t.get("tool_calls") or 0)
        b["input_tokens"] += int(t.get("input_tokens") or 0)
        b["cache_read_tokens"] += int(t.get("cache_read_tokens") or 0)
        b["output_tokens"] += int(t.get("output_tokens") or 0)
        b["total_tokens"] += int(t.get("total_tokens") or 0)
        if t.get("no_action"):
            b["no_action_count"] += 1
        if t.get("error"):
            b["errors"] += 1
        b["usd_cost"] += estimate_usd(
            int(t.get("input_tokens") or 0),
            int(t.get("cache_read_tokens") or 0),
            int(t.get("output_tokens") or 0),
            t.get("model"),
        )
    rows = sorted(buckets.values(), key=lambda r: r["group"])
    for r in rows:
        r["no_action_rate"] = (r["no_action_count"] / r["turns"]) if r["turns"] else 0.0
    return rows


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def _human_tokens(n: int) -> str:
    """Short token counts: 12, 3.4k, 1.2M."""
    n = int(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _cache_pct(input_tokens: int, cache_read_tokens: int) -> str:
    """Render '23% cached' or just '' when there's nothing meaningful to show."""
    if input_tokens <= 0 or cache_read_tokens <= 0:
        return ""
    return f", {cache_read_tokens / input_tokens:.0%} cached"


def _row_line(r: dict) -> str:
    """One bullet for a group row: key — turns, tokens, cost, extras."""
    extras = []
    if r.get("no_action_count"):
        extras.append(f"{r['no_action_count']} NO_ACTION")
    if r.get("errors"):
        extras.append(f"{r['errors']} error{'s' if r['errors'] != 1 else ''}")
    extras_str = f" · {' · '.join(extras)}" if extras else ""
    return (
        f"• *{r['group']}* — {r['turns']} turn{'s' if r['turns'] != 1 else ''}, "
        f"{_human_tokens(r['input_tokens'])} in"
        f"{_cache_pct(r['input_tokens'], r['cache_read_tokens'])}"
        f" → {_human_tokens(r['output_tokens'])} out"
        f", ${r['usd_cost']:.4f}{extras_str}"
    )


def format_usage_table(rows: list[dict], title: str = "") -> str:
    """Render rollup rows as a Telegram-friendly compact summary:

        *Title*
        Total: N turns · IN → OUT tokens · $USD [· extras]

        • *group key* — N turns, IN in [, P% cached] → OUT out, $USD [· extras]
        ...

    Function name kept (format_usage_table) for compatibility with the slash
    command and existing callers, but the layout is now a vertical summary
    rather than a fixed-width table — readable on mobile, no horizontal scroll.
    """
    if not rows:
        return (
            f"{title}\n\n_No usage records in this period._" if title
            else "_No usage records in this period._"
        )

    totals_in = sum(r["input_tokens"] for r in rows)
    totals_cache = sum(r["cache_read_tokens"] for r in rows)
    totals_out = sum(r["output_tokens"] for r in rows)
    totals_turns = sum(r["turns"] for r in rows)
    totals_llm = sum(r["llm_calls"] for r in rows)
    totals_tools = sum(r["tool_calls"] for r in rows)
    totals_no_action = sum(r["no_action_count"] for r in rows)
    totals_errors = sum(r["errors"] for r in rows)
    totals_usd = sum(r["usd_cost"] for r in rows)

    extras = []
    if totals_no_action:
        extras.append(f"{totals_no_action} NO_ACTION")
    if totals_errors:
        extras.append(f"{totals_errors} error{'s' if totals_errors != 1 else ''}")
    extras_str = f" · {' · '.join(extras)}" if extras else ""

    out: list[str] = []
    if title:
        out.append(title)
    out.append(
        f"Total: {totals_turns} turn{'s' if totals_turns != 1 else ''} · "
        f"{totals_llm} LLM · "
        f"{totals_tools} tool{'s' if totals_tools != 1 else ''} · "
        f"{_human_tokens(totals_in)} in"
        f"{_cache_pct(totals_in, totals_cache)} → "
        f"{_human_tokens(totals_out)} out · "
        f"${totals_usd:.4f}{extras_str}"
    )
    # Single-row tables (one bucket = whole period) are redundant — totals already
    # cover the whole story. Show the per-row breakdown only when there are 2+.
    if len(rows) > 1:
        out.append("")
        for r in rows:
            out.append(_row_line(r))
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Date-range helpers (used by the /usage handler; exposed here so the same
# semantics are reusable from scripts)
# ---------------------------------------------------------------------------


def israel_day_range(day_iso: str) -> tuple[datetime, datetime]:
    """[00:00 Israel, next-day 00:00 Israel) for a YYYY-MM-DD date, in UTC."""
    d = datetime.fromisoformat(day_iso).date()
    start_local = datetime.combine(d, datetime.min.time(), _IL_TZ)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def israel_last_n_days(n: int) -> tuple[datetime, datetime]:
    """[N days ago at 00:00 Israel, tomorrow 00:00 Israel) in UTC. N=7 = last week."""
    today_local = datetime.now(timezone.utc).astimezone(_IL_TZ).date()
    start_local = datetime.combine(today_local - timedelta(days=n - 1), datetime.min.time(), _IL_TZ)
    end_local = datetime.combine(today_local + timedelta(days=1), datetime.min.time(), _IL_TZ)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)
