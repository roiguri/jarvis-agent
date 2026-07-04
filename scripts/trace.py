#!/usr/bin/env python3
"""Per-turn timeline: join turns.jsonl ↔ tool_calls.jsonl by turn_id, plus
chat_history.jsonl and notifications.jsonl by (thread_id, time window).

Run from /app/jarvis_code:

    venv/bin/python3 scripts/trace.py                # last 5 turns
    venv/bin/python3 scripts/trace.py --last 10
    venv/bin/python3 scripts/trace.py --turn 1e42c3   # prefix-match a turn id

Pure read-only. No imports from agent.py — only the log paths via
tools/core/telemetry to keep this script light.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# Make repo root importable when invoked directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from observability import TURNS_LOG, TOOL_CALLS_LOG
from tools.core.history import CHAT_LOG, NOTIFICATION_LOG

IL_TZ = ZoneInfo("Asia/Jerusalem")


def _read_jsonl(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _parse(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _ms_since(t0: datetime, ts: str) -> int:
    return int((_parse(ts) - t0).total_seconds() * 1000)


def _short(s: str, n: int = 80) -> str:
    s = s.replace("\n", " ").strip()
    return s if len(s) <= n else s[:n - 1] + "…"


def render_turn(turn: dict, tools: list[dict], chat: list[dict], notifs: list[dict]) -> str:
    tid = turn["turn_id"]
    t0 = _parse(turn["started_at"])
    t0_local = t0.astimezone(IL_TZ).strftime("%Y-%m-%d %H:%M:%S")

    head = [
        f"━━━ Turn {tid[:12]}…  [{turn['scope']}]  thread={turn['thread_id']}",
        f"    Started {t0_local} Israel",
        f"    duration={turn['duration_ms']}ms  llm_calls={turn['llm_calls']}  tool_calls={turn['tool_calls']}",
        f"    tokens: in={turn.get('input_tokens',0)} (cache={turn.get('cache_read_tokens',0)}) out={turn.get('output_tokens',0)}",
        f"    no_action={turn.get('no_action')}  error={turn.get('error')}",
    ]

    # Build a single timeline of events ordered by ms-offset.
    events: list[tuple[int, str]] = []
    for c in chat:
        events.append((_ms_since(t0, c["ts"]), f"CHAT[{c.get('role','?')}]  {_short(c.get('content',''))}"))
    for n in notifs:
        events.append((_ms_since(t0, n["ts"]), f"NOTIF[{n.get('event','?')}]  {_short(n.get('message',''))}"))
    prev_end = 0
    for t in tools:
        start_ms = _ms_since(t0, t["ts"])
        if start_ms - prev_end > 0:
            events.append((prev_end, f"LLM   (~{start_ms - prev_end}ms)"))
        flag = "*" if t.get("destructive") else " "
        line = (
            f"TOOL{flag} {t['tool']:<28} {t.get('namespace','?'):<18} "
            f"{t.get('status','?'):<10} ({t['duration_ms']}ms, args={t.get('args_size',0)}b)"
        )
        events.append((start_ms, line))
        if t.get("status") == "error" and t.get("error"):
            events.append((start_ms, f"    └─ {_short(t['error'])}"))
        prev_end = start_ms + int(t.get("duration_ms", 0) or 0)
    tail = turn["duration_ms"] - prev_end
    if tail > 0:
        events.append((prev_end, f"LLM   (~{tail}ms)"))

    events.sort(key=lambda e: e[0])
    lines = head + [""]
    lines.append(f"    [+{0:>5}ms] START")
    for offset_ms, text in events:
        lines.append(f"    [+{offset_ms:>5}ms] {text}")
    lines.append(f"    [+{turn['duration_ms']:>5}ms] END")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Per-turn timeline.")
    parser.add_argument("--last", type=int, default=5,
                        help="Show the last N turns (default 5).")
    parser.add_argument("--turn", type=str, default=None,
                        help="Show a single turn matching this id prefix.")
    args = parser.parse_args(argv)

    turns = _read_jsonl(TURNS_LOG)
    if not turns:
        print(f"No turns found in {TURNS_LOG}", file=sys.stderr)
        return 1

    if args.turn:
        match = [t for t in turns if t["turn_id"].startswith(args.turn)]
        if not match:
            print(f"No turn matches prefix {args.turn!r}", file=sys.stderr)
            return 2
        selected = match
    else:
        selected = turns[-args.last:]

    all_tools = _read_jsonl(TOOL_CALLS_LOG)
    all_chat = _read_jsonl(CHAT_LOG)
    all_notifs = _read_jsonl(NOTIFICATION_LOG)

    for turn in selected:
        tid = turn["turn_id"]
        thread_id = turn["thread_id"]
        t0 = _parse(turn["started_at"])
        t1 = _parse(turn["ended_at"]) if turn.get("ended_at") else t0
        # Slack on each side so the model's outbound reply (logged just after
        # the agent returns) still falls inside the window for chat lookup.
        from datetime import timedelta
        window_start = t0 - timedelta(seconds=2)
        window_end = t1 + timedelta(seconds=2)
        my_tools = [t for t in all_tools if t.get("turn_id") == tid]
        my_chat = [
            c for c in all_chat
            if c.get("thread_id") == thread_id
            and window_start <= _parse(c["ts"]) <= window_end
        ]
        my_notifs = [
            n for n in all_notifs
            if window_start <= _parse(n["ts"]) <= window_end
        ]
        print(render_turn(turn, my_tools, my_chat, my_notifs))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
