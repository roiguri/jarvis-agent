"""The pending-mirror drain: proactive sends reach the owner thread as one
user-role block, cursor-tracked, bounded, and safe under the message reducer."""

import json
import os
from datetime import datetime, timedelta, timezone

from langchain_core.messages import AIMessage, HumanMessage

import agent
import pending_mirrors as mirror_mod
from tests.conftest import LOG_DIR


def seed(rows):
    with open(os.path.join(LOG_DIR, "notifications.jsonl"), "w", encoding="utf-8") as f:
        for ts, event, msg in rows:
            f.write(json.dumps({"ts": ts, "event": event, "message": msg}) + "\n")


def ts(minutes_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def test_drain_and_cursor():
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
    assert block is not None and cursor is not None, "drains pending rows"
    lines = block.split("\n")
    assert lines[0] == mirror_mod.HEADER, "header first"
    assert lines[1:] == [
        "[Heartbeat] Morning readiness: HRV good",
        "[Reminder] Register for Thursday's WOD",
        "[Notification] New episode of Silo",
        "[Notification] unknown kind",
    ], "prefixes + order + exclusions"
    assert cursor > ts(6) and cursor <= ts(4), "cursor = last drained row's ts"

    # Advancing the cursor leaves nothing pending.
    mirror_mod.advance_cursor(cursor)
    assert os.path.exists(mirror_mod.CURSOR_PATH), "cursor file written"
    assert mirror_mod.drain_pending() == (None, None), "second drain empty"

    # A new row after the cursor drains alone.
    with open(os.path.join(LOG_DIR, "notifications.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": ts(0.1), "event": "reminder", "message": "stretch"}) + "\n")
    block2, cursor2 = mirror_mod.drain_pending()
    assert block2 == mirror_mod.HEADER + "\n[Reminder] stretch", "only the new row drains"
    assert cursor2 > cursor, "cursor moves forward"

    # An un-advanced cursor (a failed turn) re-delivers.
    block3, _ = mirror_mod.drain_pending()
    assert block3 == block2, "failed turn re-delivers"


def test_entry_cap_keeps_newest():
    seed([(ts(50 - i), "reminder", f"r{i}") for i in range(30)])
    block, _ = mirror_mod.drain_pending()
    assert len(block.split("\n")) - 1 == mirror_mod.MAX_ENTRIES, "entry cap keeps newest N"
    assert block.endswith("r29"), "newest survives the cap"


def test_entry_length_cap():
    seed([(ts(1), "heartbeat", "x" * 5000)])
    block, _ = mirror_mod.drain_pending()
    assert len(block.split("\n")[1]) == len("[Heartbeat] ") + mirror_mod.ENTRY_CAP, "per-entry length cap"


def test_reducer_keeps_user_role_block():
    mirror_msg = HumanMessage("[Messages Jarvis sent you since the last turn:]\n[Reminder] stretch")
    user_msg = HumanMessage("what did you just remind me about?")

    merged = agent._add_and_trim([], [mirror_msg, user_msg])
    assert [m.content for m in merged] == [mirror_msg.content, user_msg.content], \
        "fresh thread keeps both"

    full = [HumanMessage(f"m{i}") if i % 2 == 0 else AIMessage(f"a{i}") for i in range(60)]
    merged = agent._add_and_trim(full, [mirror_msg, user_msg])
    assert [m.content for m in merged[-2:]] == [mirror_msg.content, user_msg.content], \
        "full-window trim keeps the drain tail"
    assert merged[0].type == "human", "window starts user-role after trim"

    # An assistant-role mirror WOULD be dropped on a fresh thread — the reason
    # the block is user-role.
    dropped = agent._add_and_trim([], [AIMessage("[Reminder] stretch"), user_msg])
    assert [m.content for m in dropped] == [user_msg.content], \
        "assistant-role mirror is dropped by the reducer (documented hazard)"


def test_user_prompt_has_no_notification_slice():
    assert not hasattr(agent, "_load_recent_heartbeat_notifications"), "prompt slice loader deleted"
    assert "Heartbeat activity today" not in agent.build_system_prompt("user", set()), \
        "user prompt has no notification slice"
