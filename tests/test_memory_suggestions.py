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
from gateway.outbox import EVENT_MEDIA
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
    for path in made:
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
                     tool_name="read_memory", content='{"filename": "USER.md"}', **common)
        store.insert(con, ts="2026-10-15T09:00:02+00:00", turn_id="t", role="tool_result",
                     tool_name="read_memory", content="SECRET TOOL OUTPUT", **common)
        store.insert(con, ts="2026-10-15T09:00:03+00:00", turn_id="t", role="jarvis",
                     content="Got it, two lines.", **common)
        store.insert(con, ts="2026-10-15T10:00:00+00:00", turn_id="h", thread_id="heartbeat",
                     scope="heartbeat", role="jarvis", source="heartbeat", content="HEARTBEAT INNER WORK")
        store.insert(con, ts="2026-10-16T04:00:00+00:00", turn_id="n", role="owner",
                     content="a message from the next day", **common)


def test_the_turn_reads_the_conversation_the_memory_files_and_past_notes(turn, memory_files, fake_outbox):
    seed_day()
    calls = turn("Facts\n1. USER.md: add ...")
    assert asyncio.run(ms.send(DAY)) == "sent"
    (call,) = calls
    assert call["thread_id"] == "dream" and call["scope"] == "dream"
    text = call["input"]
    assert text.startswith("Morning memory review.")
    assert "Today: Friday 2026-10-16, Israel time." in text
    assert "[2026-10-15 12:00 owner] from now on keep briefings to two lines" in text
    assert "[2026-10-15 12:00 jarvis] Got it, two lines." in text
    assert "[2026-10-15 08:00 jarvis-sent] Morning readiness: slept 7h." in text
    for absent in ("SECRET TOOL OUTPUT", "read_memory", "HEARTBEAT INNER WORK", "next day"):
        assert absent not in text
    assert "--- fitness/plan.md (changed " in text and "Runs on Tuesdays." in text
    for absent in ("notes of a task", "an old daily log", "the task list", "--- SOUL.md", "USER.md body"):
        assert absent not in text, "core files are in the system prompt; the heartbeat's own files are not shown"
    assert "SOUL.md (read-only)" in text and "(not shown: HEARTBEAT.md" in text
    notes = text[text.index("Your notes from the past 7 days:"):text.index("Conversation on")]
    assert "--- sent 2026-10-14 ---" in notes and "make a list file" in notes
    assert "YESTERDAYS NOTE" in notes and "A WEEK-OLD NOTE" not in text
    assert text.count("YESTERDAYS NOTE") == 1, "a past note is shown once, not again in the transcript"


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
