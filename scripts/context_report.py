#!/usr/bin/env python3
"""The context instrument — one script, ~six numbers, run before and after.

Prints the readings the context work brackets every change with: per-scope
turn costs and cache ratio, what each model call's input is made of (system
prompt, tool declarations, earlier turns, this turn), heartbeat cost per
due-task set — all from the telemetry store — plus checkpoint weight per
thread from threads.sqlite and the assembled system prompt's section sizes from
build_system_prompt itself. Everything is read-only.

    JARVIS_ROOT=/app/jarvis_staging ./venv/bin/python scripts/context_report.py [--days 7]
"""

import argparse
import pathlib
import sqlite3
import sys
import os
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import config
from observability import store
from observability.usage import estimate_usd


def turn_stats(since: str) -> list[dict]:
    """Per-scope rollup of finished turns since `since`."""
    return store.rows(
        """
        SELECT scope, COUNT(*) AS turns, SUM(input_tokens) AS input,
               SUM(cache_read_tokens) AS cache_read, SUM(llm_calls) AS llm_calls,
               SUM(tool_calls) AS tool_calls
        FROM turns WHERE ts >= ? AND ended_at IS NOT NULL
        GROUP BY scope ORDER BY scope
        """,
        (since,),
    )


def composition(since: str) -> list[dict]:
    """Average input composition per model call, by scope, in chars and tokens.
    Only calls recorded with composition (not imported history)."""
    return store.rows(
        """
        SELECT t.scope AS scope, COUNT(*) AS calls,
               AVG(c.prompt_chars) AS prompt, AVG(c.schema_chars) AS schemas,
               AVG(c.history_chars) AS history, AVG(c.turn_chars) AS turn,
               AVG(c.bound_tool_count) AS tools, AVG(c.input_tokens) AS input,
               AVG(c.cache_read_tokens) AS cache_read, AVG(c.latency_ms) AS latency
        FROM llm_calls c JOIN turns t ON t.turn_id = c.turn_id
        WHERE c.ts >= ? AND c.prompt_chars IS NOT NULL
        GROUP BY t.scope ORDER BY t.scope
        """,
        (since,),
    )


def due_task_costs(since: str) -> list[dict]:
    """Heartbeat turns grouped by their due-task set."""
    return store.rows(
        """
        SELECT COALESCE(due_tasks, '(not recorded)') AS due, COUNT(*) AS turns,
               SUM(input_tokens) AS input, SUM(cache_read_tokens) AS cache_read,
               SUM(output_tokens) AS output, SUM(no_action) AS no_action,
               MAX(model) AS model
        FROM turns WHERE scope = 'heartbeat' AND ts >= ? AND ended_at IS NOT NULL
        GROUP BY due ORDER BY input DESC
        """,
        (since,),
    )


def checkpoint_weights() -> dict[str, int]:
    """Latest checkpoint blob size per thread — what every LLM call re-sends."""
    db = os.path.join(config.MEMORY_DIR, "threads.sqlite")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = con.execute(
        # Ids are UUIDv6-ish and sort chronologically; max() picks the latest.
        """
        SELECT thread_id, length(checkpoint) FROM checkpoints
        WHERE (thread_id, checkpoint_id) IN (
            SELECT thread_id, max(checkpoint_id) FROM checkpoints GROUP BY thread_id
        )
        """
    ).fetchall()
    con.close()
    return dict(rows)


def prompt_sections(scope: str) -> list[tuple[str, int]]:
    """(section name, chars) for the assembled prompt, split on '--- x ---'
    markers; the leading unmarked span is the identity/rules block."""
    from agent import build_system_prompt

    text = build_system_prompt(scope, set(), due_tasks=[] if scope == "heartbeat" else None)
    sections: list[tuple[str, int]] = []
    name = "envelope + SOUL/AGENTS/USER + skills"
    start = 0
    for i, line in enumerate(lines := text.split("\n")):
        if line.startswith("--- ") and line.rstrip().endswith("---"):
            span = "\n".join(lines[start:i])
            sections.append((name, len(span)))
            name, start = line.strip("- ").strip(), i
    sections.append((name, len("\n".join(lines[start:]))))
    return [(n, c) for n, c in sections if c]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args()

    since = (datetime.now(timezone.utc) - timedelta(days=args.days)).isoformat()
    print(f"# context report — last {args.days} days, {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC")
    print("\n## turns")
    for s in turn_stats(since):
        input_tokens = s["input"] or 0
        print(
            f"{s['scope']:>10}: {s['turns']:4d} turns | "
            f"input/turn {input_tokens // s['turns']:>7,} | "
            f"cache ratio {(s['cache_read'] or 0) / input_tokens if input_tokens else 0:5.1%} | "
            f"llm calls/turn {(s['llm_calls'] or 0) / s['turns']:.2f} | "
            f"tool calls/turn {(s['tool_calls'] or 0) / s['turns']:.2f} | "
            f"input/day {input_tokens // args.days:,}"
        )

    print("\n## input per model call (avg chars; chars are shares, tokens are cost)")
    for c in composition(since):
        parts = {k: c[k] or 0 for k in ("prompt", "schemas", "history", "turn")}
        total = sum(parts.values()) or 1
        shares = " · ".join(f"{k} {v:,.0f} ({v / total:.0%})" for k, v in parts.items())
        print(
            f"{c['scope']:>10}: {c['calls']:4d} calls | {shares} | "
            f"{c['tools'] or 0:.0f} tools bound | input {c['input'] or 0:,.0f} tok "
            f"(cache {c['cache_read'] or 0:,.0f}) | {c['latency'] or 0:,.0f}ms"
        )

    print("\n## heartbeat cost per due-task set")
    for d in due_task_costs(since):
        usd = estimate_usd(d["input"] or 0, d["cache_read"] or 0, d["output"] or 0, d["model"])
        print(
            f"  {d['turns']:4d} turns | input/turn {(d['input'] or 0) // d['turns']:>7,} | "
            f"${usd:.2f} | {d['no_action'] or 0} no-action | {d['due']}"
        )

    print("\n## checkpoint weight (latest blob per thread; ~bytes/4 = tokens re-sent per call)")
    for thread, size in sorted(checkpoint_weights().items()):
        print(f"{thread:>24}: {size:>8,} bytes  (~{size // 4:,} tok)")

    for scope in ("user", "heartbeat"):
        secs = prompt_sections(scope)
        total = sum(c for _, c in secs)
        print(f"\n## system prompt sections — scope={scope} (total {total:,} chars, ~{total // 4:,} tok)")
        for sec_name, chars in secs:
            print(f"  {chars:>7,}  {sec_name}")


if __name__ == "__main__":
    main()
