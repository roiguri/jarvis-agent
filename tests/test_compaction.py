"""Compaction: the window's older turns become one validated record at its head;
the last turns stay, with old tool results stubbed; skills not used in them go
off; the record is pinned, respects /clear, survives a stale apply, and falls
back to a code-only record when the model fails."""

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import agent
import compaction
from episodes import store as episode_store
from tests.fakes import FakeLLM, tool_call

T = "t_compact"


@pytest.fixture(autouse=True)
def fresh_thread():
    """Saved conversations outlive a test; each one starts with an empty thread."""
    agent.memory.delete_thread(T)


def window(thread=T):
    return agent.agent_executor.get_state({"configurable": {"thread_id": thread}}).values.get("messages", [])


def turn(use_llm, text, reply, tool=None, n=[0]):
    n[0] += 1
    script = []
    if tool:
        script.append(AIMessage(content="", tool_calls=[tool_call(tool, {}, n[0])]))
    script.append(AIMessage(content=reply))
    use_llm(FakeLLM(script))
    agent.ask_jarvis(text, T)


@pytest.fixture
def model(monkeypatch):
    """The summarizer's answers, in order; each is a dict or an exception."""
    answers = []

    def fake(system, user):
        fake.prompts.append(user)
        a = answers.pop(0)
        if isinstance(a, BaseException):
            raise a
        return a, AIMessage(content="", usage_metadata={"input_tokens": 1000, "output_tokens": 100,
                                                        "total_tokens": 1100})
    fake.prompts = []
    monkeypatch.setattr(compaction, "_call_model", fake)
    return answers, fake


def today():
    return datetime.now(timezone.utc).astimezone(compaction.ISRAEL_TZ).strftime("%Y-%m-%d")


def good_state(**over):
    state = {"open_requests": [], "done": [{"text": f"Added a book on {today()}", "date": today()}],
             "owner_rules": [{"quote": "always answer in English", "date": today()}],
             "learned": [], "decisions": [], "lookup_hints": []}
    state.update(over)
    return state


def day(use_llm):
    turn(use_llm, "from now on, always answer in English", "Will do.")
    turn(use_llm, "my sister lands at 18:40 on LY318", "Noted.")
    turn(use_llm, "what's in my memory?", "Here's the list.", tool="list_memory")
    turn(use_llm, "show it again", "Same list.", tool="list_memory")
    turn(use_llm, "activate fitness please", "Done.", tool="activate_skill")
    turn(use_llm, "and once more", "Here it is.", tool="list_memory")


def test_under_the_limit_nothing_happens(use_llm):
    day(use_llm)
    assert compaction.prepare(T) is None


def test_forced_compaction_keeps_three_turns_and_heads_with_the_record(use_llm, model):
    answers, _ = model
    day(use_llm)
    answers.append(good_state())
    plan = compaction.compact_thread(T, force=True)
    assert plan.outcome == "ok"
    msgs = window()
    assert msgs[0].id.startswith(compaction.SUMMARY_ID)
    record = msgs[0].content
    assert record.startswith("[Earlier conversation") and record.endswith(compaction.FRAMING_END)
    assert '"always answer in English"' in record, "model section rendered"
    assert "my sister lands at 18:40 on LY318" in record, "owner messages quoted verbatim by code"
    assert "LY318" in record.split("Identifiers:")[1], "identifiers harvested"
    humans = [m.content for m in msgs[1:] if isinstance(m, HumanMessage)]
    assert len(humans) == 3 and humans[-1].endswith("and once more")
    assert not any("my sister lands" in str(m.content) for m in msgs[1:]), "older turns gone"


def test_old_tool_results_are_stubbed_except_the_newest_turn(use_llm, model):
    answers, _ = model
    day(use_llm)
    answers.append(good_state())
    compaction.compact_thread(T, force=True)
    results = [m for m in window() if isinstance(m, ToolMessage)]
    assert results[0].content.startswith("[result cleared at compaction"), "an older kept turn's result"
    assert not results[-1].content.startswith("[result cleared"), "the newest turn keeps its result"


def test_skills_without_a_call_in_the_kept_turns_go_off(use_llm, model):
    answers, _ = model
    day(use_llm)
    agent.agent_executor.update_state({"configurable": {"thread_id": T}},
                                      {"active_skills": {"fitness", "travel"}}, as_node="llm")
    answers.append(good_state())
    plan = compaction.compact_thread(T, force=True)
    assert plan.active_skills == set()
    state = agent.agent_executor.get_state({"configurable": {"thread_id": T}}).values
    assert state["active_skills"] == set()


def test_validation_drops_bad_items(use_llm, model):
    answers, _ = model
    day(use_llm)
    future = (datetime.now(timezone.utc) + timedelta(days=3)).strftime("%Y-%m-%d")
    answers.append(good_state(
        owner_rules=[{"quote": "always answer in English", "date": today()},
                     {"quote": "never book flights", "date": today()}],
        done=[{"text": f"Added a book on {today()}", "date": today()},
              {"text": "Will remind him tomorrow", "date": today()},
              {"text": "Planned trip", "date": future}],
        learned=[{"text": "His sister arrives on LY318", "date": today(), "supersedes": None, "certainty": "stated"},
                 {"text": "Prefers English replies", "date": today(), "supersedes": None, "certainty": "stated"},
                 {"text": "Checks memory often", "date": today(), "supersedes": None, "certainty": "unverified"}],
    ))
    compaction.compact_thread(T, force=True)
    record = window()[0].content
    assert "never book flights" not in record, "a quote the owner never wrote"
    assert "remind him tomorrow" not in record, "a relative date word"
    assert "Planned trip" not in record, "a future date"
    assert "Added a book" in record


def test_a_mostly_bad_answer_gets_one_retry(use_llm, model):
    answers, fake = model
    day(use_llm)
    answers.append(good_state(owner_rules=[{"quote": "invented rule", "date": today()}], done=[]))
    answers.append(good_state())
    plan = compaction.compact_thread(T, force=True)
    assert plan.outcome == "retried"
    assert "was rejected" in fake.prompts[1] and "invented rule" in fake.prompts[1]


def test_model_failure_falls_back_to_a_code_only_record_and_guards_against_thrash(use_llm, model):
    answers, fake = model
    day(use_llm)
    answers.append(RuntimeError("503 UNAVAILABLE"))
    plan = compaction.compact_thread(T, force=True)
    assert plan.outcome == "fallback"
    record = window()[0].content
    assert "without an AI summary" in record and "my sister lands at 18:40" in record
    turn(use_llm, "one more", "ok")
    turn(use_llm, "and another", "ok")
    plan = compaction.compact_thread(T, force=True)
    assert plan.outcome == "fallback" and len(fake.prompts) == 1, "no model call right after a fallback"


def test_a_turn_between_prepare_and_apply_skips_it(use_llm, model):
    answers, _ = model
    day(use_llm)
    answers.append(good_state())
    plan = compaction.prepare(T, force=True)
    turn(use_llm, "a message in between", "ok")
    assert compaction.apply(plan) is False
    assert not window()[0].id.startswith(compaction.SUMMARY_ID)


def test_nothing_from_before_a_clear_comes_back(use_llm, model):
    answers, fake = model
    turn(use_llm, "my secret plan is Lisbon", "ok")
    import main
    from gateway.base import InboundMessage
    asyncio.run(main.process_inbound_message(InboundMessage(user_id=1, chat_id=1, thread_id=T, user_text="/clear")))
    day(use_llm)
    answers.append(good_state())
    compaction.compact_thread(T, force=True)
    assert "Lisbon" not in fake.prompts[0] and "Lisbon" not in window()[0].content


def test_open_requests_are_carried_then_expire(use_llm, model):
    answers, fake = model
    day(use_llm)
    old = (datetime.now(timezone.utc) - timedelta(days=5)).astimezone(compaction.ISRAEL_TZ)
    recent = datetime.now(timezone.utc).astimezone(compaction.ISRAEL_TZ)
    compaction._save_state({T: {"open_requests": [
        {"quote": "find a restaurant", "asked_at": f"{recent:%Y-%m-%d %H:%M}", "status": "no choice yet"},
        {"quote": "an ancient ask", "asked_at": f"{old:%Y-%m-%d %H:%M}", "status": "?"}]}})
    answers.append(good_state())
    compaction.compact_thread(T, force=True)
    assert "find a restaurant" in fake.prompts[0] and "an ancient ask" not in fake.prompts[0]


def test_the_record_is_pinned_through_the_trim(use_llm, model):
    answers, _ = model
    day(use_llm)
    answers.append(good_state())
    compaction.compact_thread(T, force=True)
    for i in range(30):
        turn(use_llm, f"lookup {i}", "ok", tool="list_memory")
    msgs = window()
    assert len(msgs) <= agent.MAX_MESSAGES + 4
    assert msgs[0].id.startswith(compaction.SUMMARY_ID), "never the first thing evicted"


def test_the_next_turn_sees_the_record_first(use_llm, model):
    answers, _ = model
    day(use_llm)
    answers.append(good_state())
    compaction.compact_thread(T, force=True)
    llm = FakeLLM([AIMessage(content="Your sister lands at 18:40.")])
    use_llm(llm)
    agent.ask_jarvis("when does she land?", T)
    sent = llm.sent[0]
    assert sent[1].content.startswith("[Earlier conversation"), "the record follows the system prompt"


def test_context_and_compact_commands(use_llm, model):
    import main
    from gateway.base import InboundMessage

    def run(text):
        return asyncio.run(main.process_inbound_message(
            InboundMessage(user_id=1, chat_id=1, thread_id=T, user_text=text)))

    assert "No compacted record yet" in run("/context")
    assert "Nothing to compact" in run("/compact")
    answers, _ = model
    day(use_llm)
    answers.append(good_state())
    assert "Messages replaced" in run("/compact")
    assert "always answer in English" in run("/context")


def test_record_layout(golden):
    now = datetime(2026, 3, 10, 15, 0, tzinfo=timezone.utc)
    rows = [{"ts": "2026-03-10T07:10:00+00:00", "role": "owner", "content": "book LY318 to Rome https://example.com/x",
             "tool_name": None},
            {"ts": "2026-03-10T07:11:00+00:00", "role": "jarvis", "content": "Booked, code K7X2PQ, ₪1,200.",
             "tool_name": None}]
    state = {"open_requests": [{"quote": "book LY318 to Rome", "asked_at": "2026-03-10 09:10", "status": "awaiting seat"}],
             "done": [{"text": "Booked LY318 on 2026-03-10", "date": "2026-03-10"}],
             "owner_rules": [], "learned": [{"text": "Prefers aisle seats", "date": "2026-03-10", "supersedes": "window",
                                            "certainty": "stated"}],
             "decisions": [], "lookup_hints": [{"topic": "Rome trip", "terms": ["LY318", "Rome"], "when": "2026-03-10"}]}
    record = compaction.render(state, rows[:1], rows, now - timedelta(hours=36), now - timedelta(hours=1), now,
                               fallback=False, covered=12)
    golden("compaction_record.md", record)
