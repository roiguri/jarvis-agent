#!/usr/bin/env python3
"""Offline harness for the pending-mirror drain (context 1c) — no model, no hub.

Runs against a scratch JARVIS_ROOT so cursor writes and log seeds touch nothing
real. Drives the real drain/cursor helpers and the real message reducer.

    ./venv/bin/python scripts/test_context_mirror.py
"""

import json
import os
import pathlib
import sys
import tempfile
from datetime import datetime, timedelta, timezone

_scratch = tempfile.mkdtemp(prefix="jarvis_mirror_test_")
os.environ["JARVIS_ROOT"] = _scratch
os.environ.setdefault("GOOGLE_API_KEY", "offline-harness-dummy")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import config  # noqa: E402  (must import after JARVIS_ROOT is set)

assert config.DATA_DIR.startswith(_scratch), "scratch root not honored"
LOG_DIR = os.path.join(config.DATA_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

import agent  # noqa: E402
import pending_mirrors as mirror_mod  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402

FAILS: list[str] = []


def check(name, got, want=True):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f": {got!r} != {want!r}"))
    if not ok:
        FAILS.append(name)


def seed(rows):
    with open(os.path.join(LOG_DIR, "notifications.jsonl"), "w", encoding="utf-8") as f:
        for ts, event, msg in rows:
            f.write(json.dumps({"ts": ts, "event": event, "message": msg}) + "\n")


def ts(minutes_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


# ── drain semantics ────────────────────────────────────────────────────────
yesterday = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
seed([
    (yesterday, "heartbeat", "old briefing — must not drain"),
    (ts(30), "heartbeat", "Morning readiness: HRV good"),
    (ts(20), "reminder", "Register for Thursday's WOD"),
    (ts(15), "heartbeat_outcome", "silent sync — must not drain"),
    (ts(10), "llm_notification", "New episode of Silo"),
    (ts(5), "someday_new_event", "unknown kind"),
])

block, cursor = mirror_mod.drain_pending()
check("drains pending rows", block is not None and cursor is not None)
lines = block.split("\n")
check("header first", lines[0], mirror_mod.HEADER)
check("prefixes + order + exclusions", lines[1:], [
    "[Heartbeat] Morning readiness: HRV good",
    "[Reminder] Register for Thursday's WOD",
    "[Notification] New episode of Silo",
    "[Notification] unknown kind",
])
check("cursor = last drained row's ts", cursor > ts(6) and cursor <= ts(4))

# ── cursor advance → nothing pending ──
mirror_mod.advance_cursor(cursor)
check("cursor file written", os.path.exists(mirror_mod.CURSOR_PATH))
check("second drain empty", mirror_mod.drain_pending(), (None, None))

# a new row after the cursor drains alone
with open(os.path.join(LOG_DIR, "notifications.jsonl"), "a", encoding="utf-8") as f:
    f.write(json.dumps({"ts": ts(0.1), "event": "reminder", "message": "stretch"}) + "\n")
block2, cursor2 = mirror_mod.drain_pending()
check("only the new row drains", block2, mirror_mod.HEADER + "\n[Reminder] stretch")
check("cursor moves forward", cursor2 > cursor)

# un-advanced cursor (failed turn) re-delivers
block3, _ = mirror_mod.drain_pending()
check("failed turn re-delivers", block3, block2)

# ── bounds ──
seed([(ts(50 - i), "reminder", f"r{i}") for i in range(30)] )
pathlib.Path(mirror_mod.CURSOR_PATH).unlink(missing_ok=True)
block4, _ = mirror_mod.drain_pending()
check("entry cap keeps newest N", len(block4.split("\n")) - 1, mirror_mod.MAX_ENTRIES)
check("newest survives the cap", block4.endswith("r29"))

seed([(ts(1), "heartbeat", "x" * 5000)])
pathlib.Path(mirror_mod.CURSOR_PATH).unlink(missing_ok=True)
block5, _ = mirror_mod.drain_pending()
check("per-entry length cap", len(block5.split("\n")[1]), len("[Heartbeat] ") + mirror_mod.ENTRY_CAP)

# ── reducer safety: the user-role block survives every window state ──
mirror_msg = HumanMessage("[Messages Jarvis sent you since the last turn:]\n[Reminder] stretch")
user_msg = HumanMessage("what did you just remind me about?")

merged = agent._add_and_trim([], [mirror_msg, user_msg])
check("fresh thread keeps both (the B2 case)",
      [m.content for m in merged], [mirror_msg.content, user_msg.content])

full = [HumanMessage(f"m{i}") if i % 2 == 0 else AIMessage(f"a{i}") for i in range(60)]
merged = agent._add_and_trim(full, [mirror_msg, user_msg])
check("full-window trim keeps the drain tail",
      [m.content for m in merged[-2:]], [mirror_msg.content, user_msg.content])
check("window starts user-role after trim", merged[0].type, "human")

# an assistant-role mirror WOULD be dropped on a fresh thread — the reason for user-role
dropped = agent._add_and_trim([], [AIMessage("[Reminder] stretch"), user_msg])
check("assistant-role mirror is dropped by the reducer (documented hazard)",
      [m.content for m in dropped], [user_msg.content])

# ── slice really gone ──
check("prompt slice loader deleted", not hasattr(agent, "_load_recent_heartbeat_notifications"))
check("user prompt has no notification slice",
      "Heartbeat activity today" not in agent.build_system_prompt("user", set()))

print()
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILURES: {FAILS}")
sys.exit(1 if FAILS else 0)
