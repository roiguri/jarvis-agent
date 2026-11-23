"""Morning memory suggestions: what the turn reads, when it does not run, and
the note."""

import asyncio
import json
import os
from datetime import date, datetime, timezone

import pytest

import agent
import config
import memory_suggestions as ms
import pending_mirrors
import turn_budget
from episodes import store
from gateway.base import OWNER_THREAD_ID
from gateway.outbox import EVENT_HEARTBEAT, EVENT_LLM_MEDIA, EVENT_MEDIA, EVENT_REMINDER
from tests.fakes import frozen_datetime

DAY = date(2026, 10, 15)
NOW = datetime(2026, 10, 16, 5, 30, tzinfo=timezone.utc)  # 08:30 Israel


@pytest.fixture
def turn(monkeypatch):
    """Stands in for the review turn: records its call and answers ``text``."""
    calls = []

    def install(text, kind=turn_budget.COMPLETED):
        def fake(user_input, thread_id, **kwargs):
            calls.append({"input": user_input, "thread_id": thread_id, **kwargs})
            return turn_budget.TurnOutcome(kind=kind, text=text)
        monkeypatch.setattr(agent, "ask_jarvis", fake)
        monkeypatch.setattr(ms, "datetime", frozen_datetime(NOW))
        return calls
    return install


@pytest.fixture
def memory_files():
    made = []

    def write(rel, text):
        path = os.path.join(config.MEMORY_DIR, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        made.append(path)

    for core in ("SOUL.md", "USER.md", "MEMORY.md"):
        write(core, f"{core} body")
    write("HEARTBEAT.md", "the task list")
    write("fitness/plan.md", "Runs on Tuesdays.")
    write("heartbeat/step_check.md", "notes of a task")
    write("daily/daily_2026-10-14.md", "an old daily log")
    yield write
    for path in dict.fromkeys(made):
        if os.path.exists(path):
            os.remove(path)


def seed_day():
    with store.write() as con:
        common = dict(thread_id=OWNER_THREAD_ID, scope="user", source="chat")
        store.insert(con, ts="2026-10-08T05:30:00+00:00", role="jarvis_sent", source="outbox",
                     meta={"job": "memory_suggestions"}, content="🧠 Memory suggestions — 2026-10-07\n\nA WEEK-OLD NOTE")
        store.insert(con, ts="2026-10-14T05:30:00+00:00", role="jarvis_sent", source="outbox",
                     meta={"job": "memory_suggestions"}, content="🧠 Memory suggestions — 2026-10-13\n\n1. make a list file")
        store.insert(con, ts="2026-10-15T05:00:00+00:00", role="jarvis_sent", source="outbox",
                     content="Morning readiness: slept 7h.")
        store.insert(con, ts="2026-10-15T05:30:00+00:00", role="jarvis_sent", source="outbox",
                     meta={"job": "memory_suggestions"}, content="🧠 Memory suggestions — 2026-10-14\n\n1. YESTERDAYS NOTE")
        store.insert(con, ts="2026-10-15T09:00:00+00:00", turn_id="t", role="owner",
                     content="from now on keep briefings to two lines", **common)
        store.insert(con, ts="2026-10-15T09:00:01+00:00", turn_id="t", role="tool_call",
                     tool_name="read_memory", meta={"call_id": "c1"}, content='{"filename": "USER.md"}', **common)
        store.insert(con, ts="2026-10-15T09:00:02+00:00", turn_id="t", role="tool_result",
                     tool_name="read_memory", meta={"call_id": "c1"}, content="SECRET TOOL OUTPUT", **common)
        store.insert(con, ts="2026-10-15T09:00:03+00:00", turn_id="t", role="jarvis",
                     content="Got it, two lines.", **common)
        store.insert(con, ts="2026-10-15T10:00:00+00:00", turn_id="h", thread_id="heartbeat",
                     scope="heartbeat", role="jarvis", source="heartbeat", content="HEARTBEAT INNER WORK")
        store.insert(con, ts="2026-10-16T04:00:00+00:00", turn_id="n", role="owner",
                     content="a message from the next day", **common)


def test_the_turn_reads_the_conversation_memory_files_and_homes(turn, memory_files, fake_outbox):
    seed_day()
    calls = turn("Facts\n1. USER.md: add ...")
    assert asyncio.run(ms.send(DAY)) == "sent"
    (call,) = calls
    assert call["thread_id"] == "dream" and call["scope"] == "dream"
    text = call["input"]
    assert text.startswith("Morning memory review.")
    assert "Today: Friday 2026-10-16, Israel time." in text
    assert "[2026-10-15 12:00 owner] from now on keep briefings to two lines" in text
    assert '[2026-10-15 12:00 tool] read_memory(filename="USER.md") -> ok' in text
    assert "[2026-10-15 12:00 jarvis] Got it, two lines." in text
    assert "[2026-10-15 08:00 jarvis-sent] Morning readiness: slept 7h." in text
    for absent in (
        "SECRET TOOL OUTPUT", "HEARTBEAT INNER WORK", "next day",
        "YESTERDAYS NOTE", "make a list file", "A WEEK-OLD NOTE",
        "Your notes from the past", "Owner preferences for memory suggestions",
    ):
        assert absent not in text
    assert "Other homes (already tracked outside memory):" in text
    assert "--- fitness/plan.md (changed " in text and "Runs on Tuesdays." in text
    for absent in ("notes of a task", "an old daily log", "the task list", "--- SOUL.md", "USER.md body"):
        assert absent not in text, "core files are in the system prompt; the heartbeat's own files are not shown"
    assert "SOUL.md (read-only)" in text and "(not shown: HEARTBEAT.md" in text


def test_dream_preferences_injected_only_when_non_empty(turn, memory_files, fake_outbox):
    seed_day()
    memory_files("dream_preferences.md", "   \n\n  ")
    calls = turn("Nothing to suggest.")
    assert asyncio.run(ms.send(DAY)) == "nothing"
    assert "Owner preferences for memory suggestions" not in calls[-1]["input"]
    assert "dream_preferences.md" not in [rel for rel, _, _ in ms._memory_files()]

    memory_files("dream_preferences.md", "- Never suggest household appliances.\n")
    assert asyncio.run(ms.send(DAY)) == "nothing"
    prompt = calls[-1]["input"]
    assert "Owner preferences for memory suggestions" in prompt
    assert "- Never suggest household appliances." in prompt
    assert "dream_preferences.md" not in [rel for rel, _, _ in ms._memory_files()]


def test_a_day_without_an_owner_message_runs_no_turn(turn, fake_outbox):
    with store.write() as con:
        store.insert(con, ts="2026-10-15T05:00:00+00:00", role="jarvis_sent", source="outbox",
                     content="Morning readiness: slept 7h.")
    calls = turn("x")
    assert asyncio.run(ms.send(DAY)) == "nothing"
    assert calls == [] and fake_outbox.sent == []


def test_nothing_to_suggest_sends_nothing(turn, fake_outbox):
    seed_day()
    turn("Nothing to suggest.")
    assert asyncio.run(ms.send(DAY)) == "nothing"
    assert fake_outbox.sent == []


def test_the_note_goes_through_the_outbox(turn, fake_outbox):
    seed_day()
    turn("Facts\n1. ...")
    assert asyncio.run(ms.send(DAY)) == "sent"
    assert fake_outbox.sent == [(EVENT_MEDIA, "🧠 Memory suggestions — 2026-10-15\n\nFacts\n1. ...\n\n"
                                              "Suggestions only — apply one only when Roi asks.")]
    assert fake_outbox.meta == [{"job": "memory_suggestions"}]


def test_an_unfinished_turn_is_reported_not_sent(turn, fake_outbox):
    seed_day()
    turn("[This turn did not finish: out of time.]", kind=turn_budget.FAILED)
    assert asyncio.run(ms.send(DAY)) == "failed"
    assert fake_outbox.sent == []


def test_the_mirror_carries_the_whole_note():
    note = "🧠 Memory suggestions\n" + "x" * 5000
    with open(pending_mirrors._NOTIF_LOG, "w", encoding="utf-8") as f:
        f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "event": EVENT_MEDIA,
                            "message": note, "job": "memory_suggestions"}) + "\n")
    block, _ = pending_mirrors.drain_pending()
    assert f"[Notification] {note}" in block


def test_media_alerts_dropped_while_conversational_sends_kept():
    with store.write() as con:
        common = dict(thread_id=OWNER_THREAD_ID, scope="user", source="chat")
        store.insert(con, ts="2026-10-15T06:00:00+00:00", role="jarvis_sent", source="outbox",
                     meta={"event": EVENT_MEDIA}, content="**Solaris** upgraded to 4K.")
        store.insert(con, ts="2026-10-15T06:10:00+00:00", role="jarvis_sent", source="outbox",
                     meta={"event": EVENT_LLM_MEDIA}, content="New episode ready.")
        store.insert(con, ts="2026-10-15T06:20:00+00:00", role="jarvis_sent", source="outbox",
                     meta={"event": EVENT_HEARTBEAT}, content="Daily readiness: 7h sleep.")
        store.insert(con, ts="2026-10-15T06:30:00+00:00", role="jarvis_sent", source="outbox",
                     meta={"event": EVENT_REMINDER}, content="Reminder: dentist at 16:00.")
        store.insert(con, ts="2026-10-15T09:00:00+00:00", turn_id="t", role="owner",
                     content="thanks", **common)
    transcript = ms.conversation(DAY, until=NOW)
    assert transcript is not None
    assert "Solaris" not in transcript
    assert "New episode ready" not in transcript
    assert "Daily readiness: 7h sleep." in transcript
    assert "Reminder: dentist at 16:00." in transcript


def test_tool_trail_truncates_args_and_marks_errors_without_bodies():
    long_val = "A" * 200
    with store.write() as con:
        common = dict(thread_id=OWNER_THREAD_ID, scope="user", source="chat", turn_id="t")
        store.insert(con, ts="2026-10-15T09:00:00+00:00", role="owner",
                     content="update the heartbeat task", **common)
        store.insert(con, ts="2026-10-15T09:00:01+00:00", role="tool_call",
                     tool_name="manage_heartbeat_task", meta={"call_id": "c-ok"},
                     content=json.dumps({"action": "update", "instruction": long_val}), **common)
        store.insert(con, ts="2026-10-15T09:00:02+00:00", role="tool_result",
                     tool_name="manage_heartbeat_task", meta={"call_id": "c-ok"},
                     content="OK_RESULT_BODY_SHOULD_NOT_LEAK", **common)
        store.insert(con, ts="2026-10-15T09:00:03+00:00", role="tool_call",
                     tool_name="save_recipe", meta={"call_id": "c-err"},
                     content=json.dumps({"name": "Lentil Soup"}), **common)
        store.insert(con, ts="2026-10-15T09:00:04+00:00", role="tool_result",
                     tool_name="save_recipe", meta={"call_id": "c-err", "status": "error"},
                     content="ERR_RESULT_BODY_SHOULD_NOT_LEAK", **common)
    transcript = ms.conversation(DAY, until=NOW)
    assert transcript is not None
    assert "manage_heartbeat_task(" in transcript and "-> ok" in transcript
    assert 'save_recipe(name="Lentil Soup") -> error' in transcript
    assert long_val not in transcript and "…" in transcript
    assert "OK_RESULT_BODY_SHOULD_NOT_LEAK" not in transcript
    assert "ERR_RESULT_BODY_SHOULD_NOT_LEAK" not in transcript


def test_wrapped_up_nothing_to_suggest_sends_nothing(turn, fake_outbox):
    seed_day()
    turn(f"Nothing to suggest.\n\n{turn_budget.WRAPPED_UP_LINE}", kind=turn_budget.WRAPPED_UP)
    assert asyncio.run(ms.send(DAY)) == "nothing"
    assert fake_outbox.sent == []


def test_wrapped_up_with_suggestions_still_sends(turn, fake_outbox):
    seed_day()
    turn(f"Facts\n1. USER.md: add x\n\n{turn_budget.WRAPPED_UP_LINE}", kind=turn_budget.WRAPPED_UP)
    assert asyncio.run(ms.send(DAY)) == "sent"
    assert len(fake_outbox.sent) == 1
    assert "USER.md: add x" in fake_outbox.sent[0][1]
