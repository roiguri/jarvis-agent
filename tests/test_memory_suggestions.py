"""Morning memory suggestions: what the model reads, when it is not called,
and the note."""

import asyncio
import json
import os
from datetime import date, datetime, timezone

import pytest
from langchain_core.messages import AIMessage

import config
import memory_suggestions as ms
import pending_mirrors
from episodes import store
from gateway.base import OWNER_THREAD_ID
from gateway.outbox import EVENT_MEDIA
from tests.fakes import FakeLLM

DAY = date(2026, 10, 15)
NOW = datetime(2026, 10, 16, 5, 30, tzinfo=timezone.utc)  # 08:30 Israel


@pytest.fixture
def model(monkeypatch):
    def install(*replies):
        llm = FakeLLM([AIMessage(content=r) for r in replies])
        monkeypatch.setattr(ms, "_model", lambda: llm)
        return llm
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
        store.insert(con, ts="2026-10-15T05:00:00+00:00", role="jarvis_sent", source="outbox",
                     content="Morning readiness: slept 7h.")
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


def test_the_model_reads_the_conversation_and_the_memory_files(model, memory_files):
    seed_day()
    llm = model("Corrections & preferences\n1. USER.md: add ...")
    note = ms.compose(DAY, now=NOW)
    system, user = (m.content for m in llm.sent[0])
    assert system.startswith("You help maintain the long-term memory of Jarvis")
    assert user.startswith("Today: Friday 2026-10-16, Israel time.")
    assert "[2026-10-15 12:00 owner] from now on keep briefings to two lines" in user
    assert "[2026-10-15 12:00 jarvis] Got it, two lines." in user
    assert "[2026-10-15 08:00 jarvis-sent] Morning readiness: slept 7h." in user
    for absent in ("SECRET TOOL OUTPUT", "read_memory", "HEARTBEAT INNER WORK", "next day"):
        assert absent not in user
    assert "--- fitness/plan.md (changed " in user and "Runs on Tuesdays." in user
    for absent in ("notes of a task", "an old daily log", "the task list"):
        assert absent not in user
    assert "SOUL.md (read-only)" in user and "(not shown: HEARTBEAT.md" in user
    assert user.index("--- SOUL.md") < user.index("--- USER.md") < user.index("--- MEMORY.md") \
        < user.index("--- fitness/plan.md")
    assert note == "🧠 Memory suggestions — 2026-10-15\n\nCorrections & preferences\n1. USER.md: add ..."


def test_a_day_without_an_owner_message_makes_no_call(model):
    with store.write() as con:
        store.insert(con, ts="2026-10-15T05:00:00+00:00", role="jarvis_sent", source="outbox",
                     content="Morning readiness: slept 7h.")
    llm = model()
    assert ms.compose(DAY, now=NOW) is None
    assert llm.sent == []


def test_nothing_to_suggest_sends_nothing(model, fake_outbox):
    seed_day()
    model("Nothing to suggest.")
    assert asyncio.run(ms.send(DAY)) == "nothing"
    assert fake_outbox.sent == []


def test_the_note_goes_through_the_outbox(model, fake_outbox):
    seed_day()
    model("Facts\n1. ...")
    assert asyncio.run(ms.send(DAY)) == "sent"
    assert fake_outbox.sent == [(EVENT_MEDIA, "🧠 Memory suggestions — 2026-10-15\n\nFacts\n1. ...")]
    assert fake_outbox.meta == [{"job": "memory_suggestions"}]


def test_a_failed_call_is_reported_not_raised(model, fake_outbox):
    seed_day()
    llm = model()
    llm.script.append(RuntimeError("504"))
    assert asyncio.run(ms.send(DAY)) == "failed"
    assert fake_outbox.sent == []


def test_the_mirror_carries_the_whole_note():
    note = "🧠 Memory suggestions\n" + "x" * 5000
    with open(pending_mirrors._NOTIF_LOG, "w", encoding="utf-8") as f:
        f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "event": EVENT_MEDIA,
                            "message": note, "job": "memory_suggestions"}) + "\n")
    block, _ = pending_mirrors.drain_pending()
    assert f"[Notification] {note}" in block
