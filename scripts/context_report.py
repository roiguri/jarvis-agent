#!/usr/bin/env python3
"""The context instrument — one script, ~six numbers, run before and after.

Prints the readings the context plan (docs/plans/archive/CONTEXT_PLAN.md phase 0)
brackets every phase with: per-scope turn costs and cache ratio from
turns.jsonl, checkpoint weight per thread from threads.sqlite, and the
assembled system prompt's section sizes from build_system_prompt itself.
Everything is read-only; the measurement recipes are context/RESEARCH.md §2.

    JARVIS_ROOT=/app/jarvis_staging ./venv/bin/python scripts/context_report.py [--days 7]

Parse turns.jsonl tolerantly and say how many lines were skipped — the file is
known to contain at least one unparseable line, and a silent skip would bias
the averages invisibly (PROBLEMS.md E-cluster).
"""

import argparse
import json
import os
import pathlib
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import config


def turn_stats(days: int) -> tuple[dict, int]:
    """Per-scope rollup over the last `days` of turns.jsonl."""
    path = os.path.join(config.DATA_DIR, "logs", "turns.jsonl")
    since = datetime.now(timezone.utc) - timedelta(days=days)
    per_scope: dict[str, dict] = defaultdict(
        lambda: {"turns": 0, "input": 0, "cache_read": 0, "llm_calls": 0, "tool_calls": 0}
    )
    skipped = 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if datetime.fromisoformat(rec["ts"]) < since:
                    continue
                s = per_scope[rec.get("scope", "?")]
                s["turns"] += 1
                s["input"] += rec.get("input_tokens", 0)
                s["cache_read"] += rec.get("cache_read_tokens", 0)
                s["llm_calls"] += rec.get("llm_calls", 0)
                s["tool_calls"] += rec.get("tool_calls", 0)
            except (json.JSONDecodeError, KeyError, ValueError):
                skipped += 1
    return dict(per_scope), skipped


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

    stats, skipped = turn_stats(args.days)
    print(f"# context report — last {args.days} days, {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC")
    print(f"\n## turns.jsonl ({skipped} unparseable line(s) skipped)")
    for scope, s in sorted(stats.items()):
        if not s["turns"]:
            continue
        print(
            f"{scope:>10}: {s['turns']:4d} turns | "
            f"input/turn {s['input'] // s['turns']:>7,} | "
            f"cache ratio {s['cache_read'] / s['input']:5.1%} | "
            f"llm calls/turn {s['llm_calls'] / s['turns']:.2f} | "
            f"tool calls/turn {s['tool_calls'] / s['turns']:.2f} | "
            f"input/day {s['input'] // args.days:,}"
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
