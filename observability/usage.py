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

# USD per million tokens, read from https://ai.google.dev/gemini-api/docs/pricing
# and verified 2026-07-29. Re-verify on the same page before trusting any dollar
# figure — these are transcribed constants, not a live feed, and a stale entry
# silently corrupts every rollup rather than failing. The `cost-review` skill
# exists to diff this table against the page on demand.
#
# Every model we might plausibly switch to is priced here, not just the one in
# use: an unknown model falls back to _ZERO_PRICE, so a one-line model swap in
# agent.py would otherwise report $0.00 forever. summarize_usage flags that case
# via `unpriced_models`, but a present entry is better than a caught mistake.
#
# Known limitation: audio input bills at roughly 2x text on these models (e.g.
# $1.00/M vs $0.50/M for gemini-3-flash-preview), and Telegram voice notes do
# reach the model. One rate per model cannot express that, so audio-heavy days
# under-report slightly. Deliberately not modelled — modality tiering is a lot
# of machinery for a rounding error at current volumes.
MODEL_PRICES: dict[str, dict[str, float]] = {
    "gemini-3-flash-preview": {
        "input_per_m": 0.50,
        "cache_read_per_m": 0.05,
        "output_per_m": 3.00,
    },
    "gemini-3.6-flash": {
        "input_per_m": 1.50,
        "cache_read_per_m": 0.15,
        "output_per_m": 7.50,
    },
    "gemini-3.5-flash": {
        "input_per_m": 1.50,
        "cache_read_per_m": 0.15,
        "output_per_m": 9.00,
    },
    "gemini-3.5-flash-lite": {
        "input_per_m": 0.30,
        # No context caching on the standard tier for this model. Pricing cache
        # reads at the full input rate keeps estimate_usd's subtract-then-reprice
        # arithmetic correct; a lower number here would invent a discount that
        # the bill does not give us.
        "cache_read_per_m": 0.30,
        "output_per_m": 2.50,
    },
    "gemini-3.1-flash-lite": {
        "input_per_m": 0.25,
        "cache_read_per_m": 0.025,
        "output_per_m": 1.50,
    },
    "gemini-2.5-flash": {
        "input_per_m": 0.30,
        "cache_read_per_m": 0.03,
        "output_per_m": 2.50,
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
        # Models seen in this bucket that MODEL_PRICES has no entry for, so their
        # tokens contributed $0.00 to usd_cost. A set while accumulating;
        # summarize_usage converts it to a sorted list before returning so rows
        # stay JSON-serializable.
        "unpriced_models": set(),
    }


def summarize_usage(
    since: datetime | None = None,
    until: datetime | None = None,
    group_by: GroupBy = "day",
    scope_filter: str | None = None,
) -> list[dict]:
    """Group + roll up turns in [since, until). Returns rows sorted ascending
    by group key. Each row also carries ``no_action_rate`` for convenience.

    Rows carry ``unpriced_models`` (sorted list): models whose tokens fell
    through to _ZERO_PRICE and so contributed nothing to ``usd_cost``. A
    zero-dollar row is indistinguishable from a free one otherwise, which is
    how a wrong price table stays hidden — callers should surface it.

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
        model = t.get("model")
        # A null model means the turn made no LLM call (nothing to price), which
        # is not a pricing gap — only a named model we cannot price is.
        if model and model not in MODEL_PRICES:
            b["unpriced_models"].add(model)
        b["usd_cost"] += estimate_usd(
            int(t.get("input_tokens") or 0),
            int(t.get("cache_read_tokens") or 0),
            int(t.get("output_tokens") or 0),
            model,
        )
    rows = sorted(buckets.values(), key=lambda r: r["group"])
    for r in rows:
        r["no_action_rate"] = (r["no_action_count"] / r["turns"]) if r["turns"] else 0.0
        r["unpriced_models"] = sorted(r["unpriced_models"])
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
    """Render ' (23% cached)' or just '' when there's nothing meaningful to show."""
    if input_tokens <= 0 or cache_read_tokens <= 0:
        return ""
    return f" ({cache_read_tokens / input_tokens:.0%} cached)"


def _usd(amount: float) -> str:
    """Money at a precision that suits the magnitude. A 30-day total wants
    '$21.15', not '$21.1543'; a single cheap turn genuinely needs '$0.0008'.

    Two tiers, not three: cents everywhere they carry information, and 4dp only
    below a cent. A middle tier reads as inconsistent when sibling rows in one
    breakdown land on either side of it ('$3.24' next to '$0.784')."""
    if amount >= 0.01:
        return f"${amount:,.2f}"
    return f"${amount:.4f}"


def _unpriced_note(models: list[str]) -> str:
    """Render '⚠ unpriced: gemini-x' or '' — the marker that keeps a $0.00 from
    reading as free. Rendered on the totals block as well as per-row, because
    single-bucket rollups skip the per-row breakdown entirely."""
    if not models:
        return ""
    return f"⚠ unpriced: {', '.join(models)}"


def _extras(no_action: int, errors: int, unpriced: list[str]) -> list[str]:
    """The optional trailing annotations shared by the totals block and each
    row. Returned as parts so callers choose their own separator — the totals
    block renders them as a standalone line, a row appends them inline."""
    parts = []
    if no_action:
        parts.append(f"{no_action} NO_ACTION")
    if errors:
        parts.append(f"{errors} error{'s' if errors != 1 else ''}")
    note = _unpriced_note(unpriced)
    if note:
        parts.append(note)
    return parts


def _row_line(r: dict) -> str:
    """One markdown list item for a group row: key — turns, tokens, cost, extras.

    A real '- ' list item, not a literal '•': Telegram's converter renders '- '
    as '• ', while a CommonMark client (the app) needs genuine list syntax —
    given bare newlines it treats them as soft breaks and flows every row onto
    one line. Same reasoning as the /help handler.
    """
    parts = _extras(
        r.get("no_action_count", 0), r.get("errors", 0), r.get("unpriced_models") or []
    )
    return (
        f"- **{r['group']}** — {r['turns']:,} turn{'s' if r['turns'] != 1 else ''} · "
        f"{_human_tokens(r['input_tokens'])} in"
        f"{_cache_pct(r['input_tokens'], r['cache_read_tokens'])}"
        f" → {_human_tokens(r['output_tokens'])} out · "
        f"{_usd(r['usd_cost'])}"
        + (f" · {' · '.join(parts)}" if parts else "")
    )


def format_usage_table(rows: list[dict], title: str = "") -> str:
    """Render rollup rows as a markdown summary:

        **Title**

        - **N** turns · N LLM calls · N tool calls
        - **IN** in (P% cached) → **OUT** out
        - **$USD** total
        - N NO_ACTION · N errors            (omitted when all zero)

        **Breakdown**

        - **group key** — N turns · IN in (P% cached) → OUT out · $USD [· extras]
        ...

    Every block is a real markdown list separated by blank lines, because the
    output crosses two renderers: Telegram converts '- ' to '• ', while a
    CommonMark client (the app) collapses bare newlines into one paragraph and
    needs genuine list syntax to keep rows apart. Emphasis is '**bold**' for the
    same reason — single '*' is *italic* in both, which is not what the numbers
    want. See the /help handler for the precedent.

    `extras` carries NO_ACTION / error counts and, when any model in the period
    is missing from MODEL_PRICES, an '⚠ unpriced' marker — without it a $0.00
    from a stale price table is indistinguishable from a genuinely free period.

    Function name kept (format_usage_table) for compatibility with the slash
    command and existing callers, though the layout is a vertical summary rather
    than a fixed-width table — readable on mobile, no horizontal scroll.
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

    totals_unpriced = sorted({m for r in rows for m in (r.get("unpriced_models") or [])})
    extras_parts = _extras(totals_no_action, totals_errors, totals_unpriced)

    out: list[str] = []
    if title:
        # Blank line after the header, or a CommonMark client runs it into the
        # first list item.
        out.extend([title, ""])
    # One metric family per line rather than eight fields on one — the single
    # line wrapped mid-metric on a phone, which is what made it unreadable.
    out.append(
        f"- **{totals_turns:,}** turn{'s' if totals_turns != 1 else ''} · "
        f"{totals_llm:,} LLM call{'s' if totals_llm != 1 else ''} · "
        f"{totals_tools:,} tool call{'s' if totals_tools != 1 else ''}"
    )
    out.append(
        f"- **{_human_tokens(totals_in)}** in"
        f"{_cache_pct(totals_in, totals_cache)}"
        f" → **{_human_tokens(totals_out)}** out"
    )
    out.append(f"- **{_usd(totals_usd)}** total")
    if extras_parts:
        out.append(f"- {' · '.join(extras_parts)}")
    # Single-row tables (one bucket = whole period) are redundant — totals already
    # cover the whole story. Show the per-row breakdown only when there are 2+.
    if len(rows) > 1:
        out.extend(["", "**Breakdown**", ""])
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
