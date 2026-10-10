#!/usr/bin/env python3
"""Per-turn timeline from the telemetry store: each turn's model calls and
tool calls in order, plus chat_history.jsonl and notifications.jsonl matched
by (thread_id, time window).

Run from the code root:

    venv/bin/python3 scripts/trace.py                # last 5 turns
    venv/bin/python3 scripts/trace.py --last 10
    venv/bin/python3 scripts/trace.py --turn 1e42c3   # prefix-match a turn id

Read-only. Model-call rows carry their own latency and input composition, so
the timeline is measured, not inferred. Turns imported from the old JSONL
streams have no model-call rows and show tool calls only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

# Make repo root importable when invoked directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from observability import store
from timeutils import ISRAEL_TZ as IL_TZ
from tools.core.history import CHAT_LOG, NOTIFICATION_LOG


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


def _k(n) -> str:
    n = int(n or 0)
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def render_turn(turn: dict, calls: list[dict], tools: list[dict],
                chat: list[dict], notifs: list[dict]) -> str:
    tid = turn["turn_id"]
    t0 = _parse(turn["started_at"] or turn["ts"])
    t0_local = t0.astimezone(IL_TZ).strftime("%Y-%m-%d %H:%M:%S")
    budget = store.loads(turn.get("budget")) or {}
    due = store.loads(turn.get("due_tasks"))
    label = turn["scope"] + (f":{turn['job']}" if turn.get("job") else "")
    is_open = turn.get("ended_at") is None

    head = [
        f"━━━ Turn {tid[:12]}…  [{label}]  thread={turn.get('thread_id')}"
        + (f"  channel={turn['channel']}" if turn.get("channel") else ""),
        f"    Started {t0_local} Israel"
        + (f"  trigger={turn['trigger_id']}" if turn.get("trigger_id") else "")
        + (f"  due={','.join(due)}" if due else ""),
        "    OPEN — no end recorded (still running, or the process stopped mid-turn)" if is_open else
        f"    duration={turn['duration_ms']}ms  llm_calls={turn['llm_calls']}  tool_calls={turn['tool_calls']}",
        f"    tokens: in={turn.get('input_tokens') or 0} (cache={turn.get('cache_read_tokens') or 0}) "
        f"out={turn.get('output_tokens') or 0}",
        f"    outcome={turn.get('outcome')}"
        + (f" (by {budget['exhausted_by']})" if budget.get("exhausted_by") else "")
        + f"  no_action={bool(turn.get('no_action'))}  error={turn.get('error')}",
    ]

    events: list[tuple[int, str]] = []
    for c in chat:
        events.append((_ms_since(t0, c["ts"]), f"CHAT[{c.get('role','?')}]  {_short(c.get('content',''))}"))
    for n in notifs:
        events.append((_ms_since(t0, n["ts"]), f"NOTIF[{n.get('event','?')}]  {_short(n.get('message',''))}"))
    for c in calls:
        start_ms = _ms_since(t0, c["started_at"] or c["ts"])
        line = (
            f"LLM#{c['call_index']:<2} {c.get('latency_ms') or 0}ms  in={_k(c.get('input_tokens'))} "
            f"(cache={_k(c.get('cache_read_tokens'))}) out={_k(c.get('output_tokens'))}  "
            f"[prompt {_k(c.get('prompt_chars'))} · schemas {_k(c.get('schema_chars'))} "
            f"({c.get('bound_tool_count') or 0} tools) · history {_k(c.get('history_chars'))} · "
            f"turn {_k(c.get('turn_chars'))} chars]"
            + (f"  finish={c['finish_reason']}" if c.get("finish_reason") not in (None, "", "STOP") else "")
        )
        events.append((start_ms, line))
        if c.get("error"):
            events.append((start_ms, f"    └─ {_short(c['error'])}"))
    for t in tools:
        # A tool row is written when the call returns; its start is ts - duration.
        start_ms = _ms_since(t0, t["ts"]) - int(t.get("duration_ms") or 0)
        flag = "*" if t.get("destructive") else " "
        line = (
            f"TOOL{flag} {t['tool']:<28} {t.get('namespace') or '?':<18} "
            f"{t.get('status') or '?':<10} ({t.get('duration_ms') or 0}ms, args={t.get('args_size') or 0}b"
            + (f", result={t['result_size']}b" if t.get("result_size") is not None else "")
            + (f", from LLM#{t['llm_call_index']}" if t.get("llm_call_index") else "")
            + ")"
        )
        events.append((start_ms, line))
        if t.get("status") == "error" and t.get("error"):
            events.append((start_ms, f"    └─ {_short(t['error'])}"))

    events.sort(key=lambda e: e[0])
    lines = head + ["", f"    [+{0:>6}ms] START"]
    for offset_ms, text in events:
        lines.append(f"    [+{offset_ms:>6}ms] {text}")
    if not is_open:
        lines.append(f"    [+{turn['duration_ms']:>6}ms] END")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Per-turn timeline.")
    parser.add_argument("--last", type=int, default=5,
                        help="Show the last N turns (default 5).")
    parser.add_argument("--turn", type=str, default=None,
                        help="Show a single turn matching this id prefix.")
    args = parser.parse_args(argv)

    if args.turn:
        selected = store.rows("SELECT * FROM turns WHERE turn_id LIKE ? ORDER BY ts",
                              (args.turn + "%",))
        if not selected:
            print(f"No turn matches prefix {args.turn!r}", file=sys.stderr)
            return 2
    else:
        selected = list(reversed(store.rows(
            "SELECT * FROM turns ORDER BY ts DESC LIMIT ?", (args.last,))))
        if not selected:
            print(f"No turns found in {store.STORE_PATH}", file=sys.stderr)
            return 1

    all_chat = _read_jsonl(CHAT_LOG)
    all_notifs = _read_jsonl(NOTIFICATION_LOG)

    for turn in selected:
        tid = turn["turn_id"]
        thread_id = turn.get("thread_id")
        t0 = _parse(turn["started_at"] or turn["ts"])
        t1 = _parse(turn["ended_at"]) if turn.get("ended_at") else t0
        # Slack on each side so the model's outbound reply (logged just after
        # the agent returns) still falls inside the window for chat lookup.
        window_start = t0 - timedelta(seconds=2)
        window_end = t1 + timedelta(seconds=2)
        calls = store.rows("SELECT * FROM llm_calls WHERE turn_id = ? ORDER BY call_index", (tid,))
        tools = store.rows("SELECT * FROM tool_calls WHERE turn_id = ? ORDER BY ts", (tid,))
        my_chat = [
            c for c in all_chat
            if c.get("thread_id") == thread_id
            and window_start <= _parse(c["ts"]) <= window_end
        ]
        my_notifs = [
            n for n in all_notifs
            if window_start <= _parse(n["ts"]) <= window_end
        ]
        print(render_turn(turn, calls, tools, my_chat, my_notifs))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
