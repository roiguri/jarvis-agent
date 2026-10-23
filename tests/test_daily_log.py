"""The daily log and the morning job: what the model reads, the file it
writes, when it is not written, /logs, and the once-a-day schedule."""

import asyncio
import os
from datetime import date, datetime, timezone

import pytest
from langchain_core.messages import AIMessage

import agent
import daily_log
import dream
import memory_suggestions
import turn_budget
from episodes import store
from gateway.base import OWNER_THREAD_ID
from tests.fakes import FakeLLM

DAY = date(2026, 10, 15)
NOW = datetime(2026, 10, 16, 5, 30, tzinfo=timezone.utc)  # 08:30 Israel


@pytest.fixture
def model(monkeypatch):
    def install(*replies, module=daily_log):
        llm = FakeLLM([r if isinstance(r, (AIMessage, BaseException)) else AIMessage(content=r) for r in replies])
        monkeypatch.setattr(module, "_model", lambda: llm)
        return llm
    return install


@pytest.fixture(autouse=True)
def no_logs():
    yield
    for name in os.listdir(daily_log.DAILY_DIR) if os.path.isdir(daily_log.DAILY_DIR) else []:
        os.remove(os.path.join(daily_log.DAILY_DIR, name))


def seed_day(owner=True):
    with store.write() as con:
        common = dict(thread_id=OWNER_THREAD_ID, scope="user", source="chat")
        store.insert(con, ts="2026-10-15T06:00:00+00:00", turn_id="hb", thread_id="heartbeat", scope="heartbeat",
                     role="system", source="heartbeat", content="tick")
        store.insert(con, ts="2026-10-15T06:00:00+00:00", turn_id="hb", thread_id="heartbeat", scope="heartbeat",
                     role="tool_call", source="heartbeat", tool_name="heartbeat_respond",
                     content='{"acted_tasks": ["step-check"], "notify": true, "summary": "sent a step nudge"}')
        store.insert(con, ts="2026-10-15T06:00:01+00:00", role="jarvis_sent", source="outbox",
                     content="You're at 3,000 steps.")
        if owner:
            store.insert(con, ts="2026-10-15T09:00:00+00:00", turn_id="t", role="owner",
                         content="how many steps did I do?", **common)
            store.insert(con, ts="2026-10-15T09:00:01+00:00", turn_id="t", role="tool_result",
                         tool_name="steps", content="SECRET TOOL OUTPUT", **common)
            store.insert(con, ts="2026-10-15T09:00:02+00:00", turn_id="t", role="jarvis",
                         content="4,956 steps so far.", **common)
        store.insert(con, ts="2026-10-16T04:00:00+00:00", turn_id="n", role="owner",
                     content="a message from the next day", **common)


def test_the_log_reads_the_day_and_is_written_with_a_header(model):
    seed_day()
    llm = model("## Conversations\n- Steps: 4,956.")
    assert daily_log._write(DAY, now=NOW) == "written"
    system, user = (m.content for m in llm.sent[0])
    assert system.startswith("You write the daily log of Jarvis")
    assert user.startswith("Day: Thursday 2026-10-15, Israel time.")
    assert "- 09:00 step-check: sent a step nudge [messaged you]" in user
    assert "[2026-10-15 09:00 jarvis-sent] You're at 3,000 steps." in user
    assert "[2026-10-15 12:00 owner] how many steps did I do?" in user
    assert "[2026-10-15 12:00 jarvis] 4,956 steps so far." in user
    for absent in ("SECRET TOOL OUTPUT", "next day"):
        assert absent not in user
    assert daily_log.read(DAY) == "# Daily Log - Thursday, October 15, 2026\n\n## Conversations\n- Steps: 4,956."


def test_a_day_with_only_background_activity_still_gets_a_log(model):
    seed_day(owner=False)
    model("## Heartbeat Activity\n- 09:00 step nudge.")
    assert daily_log._write(DAY, now=NOW) == "written"


def test_a_day_where_nothing_happened_writes_nothing(model):
    llm = model()
    assert asyncio.run(daily_log.write(DAY)) == "nothing"
    assert llm.sent == [] and daily_log.read(DAY) is None


def test_a_cut_off_answer_is_not_written(model):
    seed_day()
    model(AIMessage(content="## Conversations\n- Ste", response_metadata={"finish_reason": "MAX_TOKENS"}))
    assert asyncio.run(daily_log.write(DAY)) == "failed"
    assert daily_log.read(DAY) is None


def test_the_morning_job_runs_once_a_day_and_catches_up(model, fake_outbox, monkeypatch):
    seed_day()
    model("## Conversations\n- Steps.")
    monkeypatch.setattr(agent, "ask_jarvis", lambda *a, **k: turn_budget.TurnOutcome(
        kind=turn_budget.COMPLETED, text="Facts\n1. ..."))
    frozen = type("D", (datetime,), {"now": classmethod(lambda c, tz=None: NOW if tz is None else NOW.astimezone(tz))})
    for module in (dream, daily_log, memory_suggestions):
        monkeypatch.setattr(module, "datetime", frozen)
    assert dream.catch_up_due(NOW)
    asyncio.run(dream.run_scheduled())
    asyncio.run(dream.run_scheduled())
    assert daily_log.read(DAY).startswith("# Daily Log - Thursday")
    assert len(fake_outbox.sent) == 1, "the second run the same day does nothing"
    assert not dream.catch_up_due(NOW)
    assert not dream.catch_up_due(datetime(2026, 10, 17, 4, 0, tzinfo=timezone.utc)), "before 08:30"
    assert dream.catch_up_due(datetime(2026, 10, 17, 6, 0, tzinfo=timezone.utc)), "a new day"
    assert not dream.catch_up_due(datetime(2026, 10, 17, 9, 30, tzinfo=timezone.utc)), "past noon"


def test_logs_shows_a_finished_days_log_and_today_so_far(model):
    import main
    from gateway.base import InboundMessage

    def run(text):
        return asyncio.run(main.process_inbound_message(
            InboundMessage(user_id=1, chat_id=1, thread_id=OWNER_THREAD_ID, user_text=text)))

    assert run("/logs 15.10.2026").startswith("No daily log for Thursday 2026-10-15")
    seed_day()
    model("## Conversations\n- Steps: 4,956.")
    daily_log._write(DAY, now=NOW)
    assert run("/logs 15.10.2026") == "# Daily Log - Thursday, October 15, 2026\n\n## Conversations\n- Steps: 4,956."
    assert "**Conversation**" in run("/logs today")
