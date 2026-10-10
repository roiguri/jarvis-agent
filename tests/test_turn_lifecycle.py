"""The turn lifecycle against a scripted fake model: every turn outcome can be
planted on demand — an upstream error after a committed tool call, an abnormal
finish reason, a normal completion, a failed heartbeat tick, an exhausted budget."""

import asyncio
import dataclasses
import json
import os
import sqlite3
import time
from datetime import datetime, timezone

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import agent
import heartbeat
import heartbeat_state
import pending_mirrors
import turn_budget
from gateway import factory
from gateway.base import OWNER_THREAD_ID
from gateway.outbox import Outbox
from observability import format_usage_table, load_turns, store, summarize_usage
from tests.conftest import LOG_DIR
from tests.fakes import FakeChannel, FakeLLM, tool_call
from tools.core.history import async_append_notification_log

DEFAULT_POLICIES = dict(turn_budget.POLICIES)


def thread_messages(thread_id):
    snap = agent.agent_executor.get_state({"configurable": {"thread_id": thread_id}})
    return (snap.values or {}).get("messages", [])


def last_turn_row():
    return load_turns()[-1]


def upstream_503():
    # A fresh exception per use: re-raising one instance chains every earlier
    # traceback onto the next.
    return RuntimeError("Error calling model 'gemini': 503 UNAVAILABLE. High demand.")


def notices(outbox):
    """What the owner was sent; a tick-failure notice shows as "<event>+tick_failed"."""
    return [(f"{e}+tick_failed" if (m or {}).get("tick_failed") else e, t)
            for (e, t), m in zip(outbox.sent, outbox.meta)]


def ack_call(n, acted=(), notify=False, **extra):
    return AIMessage(content="", tool_calls=[tool_call("heartbeat_respond", {
        "acted_tasks": list(acted), "notify": notify, **extra}, n)])


@pytest.fixture
def run_tick(monkeypatch):
    def run():
        # A fresh lock per run: each asyncio.run is its own event loop.
        monkeypatch.setattr(heartbeat, "TURN_LOCK", asyncio.Lock())
        asyncio.run(heartbeat.run_heartbeat())
    return run


@pytest.fixture
def due(monkeypatch):
    """Make the given tasks due on the next tick: ``due("a", "b")``."""
    def make_due(*tasks):
        monkeypatch.setattr(heartbeat_state, "any_due", lambda now: (True, list(tasks)))
    make_due("inbox-check")
    return make_due


@pytest.fixture
def stamped(monkeypatch):
    """Records the task names a tick stamps, instead of stamping them."""
    names = []
    monkeypatch.setattr(heartbeat_state, "stamp", lambda tasks, when: names.extend(tasks) or tasks)
    return names


@pytest.fixture
def set_budget(monkeypatch):
    """Override one scope's limits for this test."""
    def override(scope, **budget):
        pol = DEFAULT_POLICIES[scope]
        monkeypatch.setitem(turn_budget.POLICIES, scope, dataclasses.replace(
            pol, budget=dataclasses.replace(pol.budget, **budget)))
    return override


class LoopingLLM:
    """Always wants another tool call; records whether tools were bound, the
    request it was sent, and the per-call kwargs."""

    model = "fake-model"

    def __init__(self, answer_at=None, sleep=0.0, input_tokens=0):
        self.answer_at, self.sleep, self.input_tokens = answer_at, sleep, input_tokens
        self.log = []  # (tools_bound, messages, kwargs)

    def bind_tools(self, tools):
        outer = self

        class Bound:
            def invoke(self, messages, **kwargs):
                return outer._call(True, messages, kwargs)
        return Bound()

    def invoke(self, messages, **kwargs):
        return self._call(False, messages, kwargs)

    def _call(self, bound, messages, kwargs):
        self.log.append((bound, list(messages), kwargs))
        time.sleep(self.sleep)
        usage = {"input_tokens": self.input_tokens, "output_tokens": 1, "total_tokens": self.input_tokens + 1}
        n = len(self.log)
        if not bound or n == self.answer_at:
            return AIMessage(content=f"answer after {n} calls", usage_metadata=usage)
        return AIMessage(content="", tool_calls=[tool_call("list_memory", {}, 5000 + n)],
                         usage_metadata=usage)


def has_notice(messages, policy_scope="user"):
    notice = DEFAULT_POLICIES[policy_scope].wrap_up_notice
    return any(notice in str(m.content) for m in messages)


# --- Turn failures ------------------------------------------------------------

def test_upstream_failure_after_committed_call(use_llm):
    use_llm(FakeLLM([
        AIMessage(content="", tool_calls=[
            tool_call("list_memory", {}, 1), tool_call("list_memory", {}, 2),
        ]),
        upstream_503(),
    ]))
    out = agent.ask_jarvis("tidy my memory", "t_fail")
    assert out.kind == turn_budget.FAILED
    assert "unavailable" in (out.cause or ""), "cause names upstream"
    assert out.committed_calls == (("list_memory", 2),), "committed calls counted"
    assert "list_memory ×2" in out.text, "reply names committed calls"
    msgs = thread_messages("t_fail")
    assert isinstance(msgs[-1], AIMessage), "note is last message"
    assert "Any changes they made are saved" in msgs[-1].content, "note says changes are saved"
    assert isinstance(msgs[-2], ToolMessage), "note keeps tool pairs valid"
    row = last_turn_row()
    assert row.get("outcome") == "failed", "turn row outcome"
    assert bool(row.get("error")), "turn row error set"

    # The next turn on that thread runs cleanly from START.
    use_llm(FakeLLM([AIMessage(content="all good now")]))
    out = agent.ask_jarvis("try again", "t_fail")
    assert out.kind == turn_budget.COMPLETED, "next turn after failure: completed"
    assert out.text == "all good now"
    assert out.committed_calls == (), "committed counter reset"
    assert last_turn_row().get("outcome") == "completed", "completed: turn row outcome"


def test_failure_before_any_tool_call(use_llm):
    use_llm(FakeLLM([RuntimeError("boom")]))
    out = agent.ask_jarvis("hello", "t_internal")
    assert out.kind == turn_budget.FAILED
    assert "internal error" in (out.cause or ""), "cause is internal"
    assert "nothing was changed" in out.text, "says nothing changed"


def test_abnormal_finish_reason(use_llm):
    use_llm(FakeLLM([AIMessage(
        content=[{"type": "text", "text": ""}],
        response_metadata={"finish_reason": "MALFORMED_FUNCTION_CALL"},
    )]))
    out = agent.ask_jarvis("do the thing", "t_finish")
    assert out.kind == turn_budget.FAILED
    assert last_turn_row().get("error") == "finish_reason: MALFORMED_FUNCTION_CALL", \
        "recorded in the turn row"
    assert "MALFORMED_FUNCTION_CALL" in out.text, "reply names it"


def test_mirror_cursor_advances_on_failed_turn(use_llm):
    """A failed turn still consumes its mirror block, so it isn't mirrored twice."""
    with open(os.path.join(LOG_DIR, "notifications.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(), "event": "heartbeat",
            "message": "morning briefing",
        }) + "\n")
    use_llm(FakeLLM([upstream_503()]))
    agent.ask_jarvis("reply to briefing", OWNER_THREAD_ID)
    block, _ = pending_mirrors.drain_pending()
    assert block is None, "drained block not re-delivered after failure"
    mirrors = [m for m in thread_messages(OWNER_THREAD_ID)
               if isinstance(m, HumanMessage) and "morning briefing" in str(m.content)]
    assert len(mirrors) == 1, "block present once in the thread"


# --- Heartbeat tick failures ----------------------------------------------------

def test_failed_tick_notifies_owner(use_llm, fake_outbox, due, run_tick):
    use_llm(FakeLLM([
        AIMessage(content="", tool_calls=[tool_call("list_memory", {}, 10)]),
        upstream_503(),
    ]))
    run_tick()
    sent = notices(fake_outbox)
    assert len(sent) == 1, "one notice sent"
    event, text = sent[0]
    assert event == "heartbeat+tick_failed", "heartbeat event marked tick_failed"
    assert "inbox-check" in text, "names task"
    assert "list_memory" in text, "names committed calls"
    assert "unavailable" in text, "plain cause"


def test_acked_tick_sends_no_failure_notice(use_llm, fake_outbox, due, run_tick):
    use_llm(FakeLLM([ack_call(11, summary="nothing to do"), AIMessage(content="tick done")]))
    run_tick()
    assert [e for e, _ in notices(fake_outbox) if e == "heartbeat+tick_failed"] == []


def test_unsaved_tick_ignores_previous_ack(use_llm, fake_outbox, due, stamped, run_tick, monkeypatch):
    """A tick that fails before its input is checkpointed must not pick up the
    previous tick's ack: no stale briefing, no stale stamp, a failure notice."""
    use_llm(FakeLLM([
        ack_call(12, acted=["inbox-check"], notify=True, notification_text="OLD BRIEFING", summary="sent"),
        AIMessage(content="tick done"),
    ]))
    run_tick()
    assert [t for _, t in fake_outbox.sent] == ["OLD BRIEFING"], "setup: briefing sent"
    fake_outbox.clear()
    stamped.clear()

    def locked_stream(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    with monkeypatch.context() as m:
        m.setattr(agent.agent_executor, "stream", locked_stream)
        run_tick()
    assert "OLD BRIEFING" not in [t for _, t in fake_outbox.sent], "no stale briefing re-sent"
    assert stamped == [], "no stale stamp"
    assert [e for e, _ in notices(fake_outbox)] == ["heartbeat+tick_failed"], "failure notice sent"
    assert isinstance(thread_messages("heartbeat")[-2], HumanMessage), \
        "thread has this tick's input before the note"


def test_failed_tick_notice_through_real_outbox(use_llm, due, run_tick, monkeypatch):
    """A broken tick's notice is logged as a heartbeat row marked tick_failed,
    and the pending-mirror drain carries it into the owner thread."""
    channel = FakeChannel()
    monkeypatch.setattr(factory, "default_outbox",
                        lambda: Outbox(channel, log_sink=async_append_notification_log))
    use_llm(FakeLLM([upstream_503()]))
    run_tick()
    with open(os.path.join(LOG_DIR, "notifications.jsonl"), encoding="utf-8") as f:
        row = json.loads(f.readlines()[-1])
    assert len(channel.texts) == 1, "notice delivered"
    assert (row.get("event"), row.get("tick_failed")) == ("heartbeat", True), \
        "logged as a heartbeat row marked tick_failed"
    block, _ = pending_mirrors.drain_pending()
    assert "[Heartbeat] Heartbeat check at" in (block or ""), \
        "mirrored into the owner thread as [Heartbeat]"


# --- Error classification, and telemetry that cannot fail a turn ----------------

class FakeAPIError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


def test_describe_error():
    wrapped = RuntimeError("Error calling model")
    wrapped.__cause__ = FakeAPIError(503, "UNAVAILABLE")
    assert turn_budget.describe_error(wrapped) == \
        "the model service was unavailable (overloaded upstream)", "status code through the cause chain"
    assert "internal error" in turn_budget.describe_error(FakeAPIError(400, "bad arg near 503")), \
        "a 400 mentioning 503 is not upstream"


def test_telemetry_failure_does_not_fail_turn(use_llm, monkeypatch):
    def full_disk():
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(store, "write", full_disk)
    use_llm(FakeLLM([
        AIMessage(content="", tool_calls=[tool_call("list_memory", {}, 20)]),
        AIMessage(content="still fine"),
    ]))
    out = agent.ask_jarvis("disk is full", "t_disk")
    assert (out.kind, out.text) == (turn_budget.COMPLETED, "still fine")


# --- Trim at turn boundaries ----------------------------------------------------

def alternating(n):
    return [HumanMessage(f"h{i}") if i % 2 == 0 else AIMessage(f"a{i}") for i in range(n)]


def _said(m, text):
    """A HumanMessage carrying ``text`` as a turn's input, behind its time stamp."""
    return isinstance(m, HumanMessage) and m.content.endswith(f"] {text}")


def test_reducer_trims_only_at_turn_start():
    assert len(agent._add_and_trim(
        alternating(50), [ToolMessage(content="r", tool_call_id="x")])) == 51, \
        "non-turn write never trims"
    assert len(agent._add_and_trim(alternating(80), [HumanMessage("next")])) <= agent.MAX_MESSAGES, \
        "turn start trims to the cap"
    assert [m.content for m in agent._add_and_trim(
        alternating(80), [HumanMessage("[mirror]"), HumanMessage("reply")])][-2:] == ["[mirror]", "reply"], \
        "a mirror block and its message stay together"


def test_fanout_turn_keeps_its_input(use_llm):
    # Prior history so the window is already near the cap when the big turn starts.
    for i in range(24):
        use_llm(FakeLLM([AIMessage(content=f"reply {i}")]))
        agent.ask_jarvis(f"chat {i}", "t_fanout")
    # The incidents' shape: several moderate fan-outs adding up past the cap.
    steps, per_step = 3, 20
    rec = use_llm(FakeLLM([
        AIMessage(content="", tool_calls=[
            tool_call("list_memory", {}, 1000 * step + n) for n in range(per_step)
        ])
        for step in range(steps)
    ] + [AIMessage(content="done fanning out")]))
    out = agent.ask_jarvis("FANOUT REQUEST", "t_fanout")
    assert out.kind == turn_budget.COMPLETED, "turn completes"
    last = rec.sent[-1][1:]  # drop the system prompt
    assert any(_said(m, "FANOUT REQUEST") for m in last), "last call still carries the turn's input"
    assert isinstance(last[0], HumanMessage), "last call starts on a valid boundary"
    msgs = thread_messages("t_fanout")
    assert any(_said(m, "chat 23") for m in msgs), "checkpoint keeps prior history"
    assert len(msgs) > agent.MAX_MESSAGES, "window grew past the cap within the turn"

    rec = use_llm(FakeLLM([AIMessage(content="ok")]))
    agent.ask_jarvis("continue", "t_fanout")
    sent_next = rec.sent[0][1:]
    assert any(isinstance(m, AIMessage) and m.content == "done fanning out" for m in sent_next), \
        "next turn still sees the long turn's answer"
    assert _said(sent_next[0], "FANOUT REQUEST"), "next turn starts on the long turn's input"

    use_llm(FakeLLM([AIMessage(content="ok again")]))
    agent.ask_jarvis("one more", "t_fanout")
    msgs = thread_messages("t_fanout")
    assert len(msgs) <= agent.MAX_MESSAGES, "long turn dropped once it is two turns back"
    assert isinstance(msgs[0], HumanMessage), "trimmed window starts on a HumanMessage"


# --- Turn budget enforced in the graph ------------------------------------------

def test_step_budget(use_llm, set_budget):
    """The call that reaches the limit is the last, made without tools."""
    set_budget("user", max_llm_calls=10)
    loop = use_llm(LoopingLLM())
    out = agent.ask_jarvis("plan everything", "t_steps")
    assert out.kind == turn_budget.BUDGET_EXHAUSTED
    assert len(loop.log) == 10, "never exceeds the call budget"
    assert [b for b, _, _ in loop.log] == [True] * 9 + [False], "only the last call is tool-free"
    assert not has_notice(loop.log[6][1]), "no wrap-up before 80%"
    assert has_notice(loop.log[7][1]), "wrap-up notice from 80%"
    assert "Stopped early: it ran out of steps" in out.text, "reply flags it stopped early"
    assert out.cause == "it ran out of steps"
    assert last_turn_row().get("outcome") == "budget_exhausted", "turn row outcome"
    msgs = thread_messages("t_steps")
    assert not has_notice(msgs), "notice never persisted"
    assert isinstance(msgs[-1], AIMessage) and not msgs[-1].tool_calls, \
        "thread ends on the tool-free answer"


def test_wrap_up(use_llm, set_budget):
    """The model finishes after the notice."""
    set_budget("user", max_llm_calls=5)
    loop = use_llm(LoopingLLM(answer_at=4))
    out = agent.ask_jarvis("finish up", "t_wrap")
    assert has_notice(loop.log[3][1]), "notice reached the model"
    assert out.kind == turn_budget.WRAPPED_UP, "answering after the notice is wrapped_up"
    assert out.finished, "counts as finished"
    assert "Wrapped up early" in out.text, "code flags the reply as wrapped up early"
    assert "not from the owner" in turn_budget.POLICIES["user"].wrap_up_notice, \
        "notice says it is not from the owner"


def test_deadline(use_llm, set_budget):
    """The deadline ends the turn and caps each call's timeout."""
    set_budget("user", max_llm_calls=50, deadline_s=1.0)
    loop = use_llm(LoopingLLM(sleep=0.25))
    t0 = time.monotonic()
    out = agent.ask_jarvis("slow work", "t_time")
    assert out.kind == turn_budget.BUDGET_EXHAUSTED
    assert out.cause == "it ran out of time"
    assert time.monotonic() - t0 < 2.5, "ended near the deadline"
    timeouts = [kw.get("timeout") for _, _, kw in loop.log]
    assert all(t is not None and agent.MIN_CALL_TIMEOUT_S <= t <= agent.LLM_CALL_TIMEOUT_S
               for t in timeouts), "every call got a capped timeout"


def test_token_budget(use_llm, set_budget):
    """Cumulative input past the ceiling ends the turn."""
    set_budget("user", max_llm_calls=50, max_input_tokens=100_000)
    loop = use_llm(LoopingLLM(input_tokens=30_000))
    out = agent.ask_jarvis("token heavy", "t_tokens")
    assert out.kind == turn_budget.BUDGET_EXHAUSTED
    assert out.cause == "it reached its token budget"
    assert len(loop.log) == 5, "stopped after the ceiling"


def test_exhausted_tick_notifies_owner(use_llm, set_budget, fake_outbox, due, run_tick):
    """A tick that runs out of budget without acking notifies the owner."""
    set_budget("heartbeat", max_llm_calls=4)
    use_llm(LoopingLLM())
    run_tick()
    sent = notices(fake_outbox)
    assert [e for e, _ in sent] == ["heartbeat+tick_failed"], "failure notice sent"
    assert "ran out of steps" in sent[0][1], "cause in notice"


def test_failing_last_call_keeps_budget_outcome(use_llm, set_budget):
    """The tool-free last call itself failing keeps the budget outcome."""
    set_budget("user", max_llm_calls=3)
    use_llm(FakeLLM([
        AIMessage(content="", tool_calls=[tool_call("list_memory", {}, 7001)]),
        AIMessage(content="", tool_calls=[tool_call("list_memory", {}, 7002)]),
        upstream_503(),
    ]))
    out = agent.ask_jarvis("long job", "t_exhaust_fail")
    assert out.kind == turn_budget.BUDGET_EXHAUSTED, "still budget_exhausted"
    assert "Stopped early" in out.text, "keeps the Stopped early line"


def test_tick_wrap_up_stamps_only_finished(use_llm, set_budget, fake_outbox, due, stamped, run_tick):
    """The tick acks only what it finished; only that stamps."""
    set_budget("heartbeat", max_llm_calls=5)
    due("inbox-check", "weekly-review")
    use_llm(FakeLLM([
        AIMessage(content="", tool_calls=[tool_call("list_memory", {}, 7100 + n)])
        for n in range(3)
    ] + [
        ack_call(7200, acted=["inbox-check"], summary="partial"),
        AIMessage(content="did inbox-check; weekly-review not done"),
    ]))
    run_tick()
    assert stamped == ["inbox-check"], "only the finished task stamps"
    assert [e for e, _ in notices(fake_outbox) if e == "heartbeat+tick_failed"] == [], \
        "acked tick sends no failure notice"


# --- Budget telemetry -----------------------------------------------------------

def test_budget_telemetry(use_llm, set_budget):
    set_budget("user", max_llm_calls=10)
    use_llm(LoopingLLM())
    agent.ask_jarvis("run out", "t_tele")
    row = last_turn_row()
    assert row.get("budget", {}).get("limits", {}).get("max_llm_calls") == 10, \
        "budget block records the limits in force"
    assert row["budget"]["exhausted_by"] == "steps", "budget block records which limit ended it"
    assert row["budget"]["wrapped_up"], "budget block records the wrap-up notice"

    # 25% → 50% → 75% → 100%: a small budget can skip the notice, and the row says so.
    set_budget("user", max_llm_calls=4)
    use_llm(LoopingLLM())
    agent.ask_jarvis("run out fast", "t_tele2")
    budget = last_turn_row()["budget"]
    assert (budget["exhausted_by"], budget["wrapped_up"]) == ("steps", False), \
        "a stop that skipped the notice is visible"

    use_llm(FakeLLM([AIMessage(content="quick")]))
    agent.ask_jarvis("quick", "t_tele")
    budget = last_turn_row()["budget"]
    assert (budget["exhausted_by"], budget["wrapped_up"]) == (None, False), \
        "a normal turn records no exhaustion"


def test_usage_report_counts_budget_stops(use_llm, set_budget):
    set_budget("user", max_llm_calls=10)
    use_llm(LoopingLLM())
    agent.ask_jarvis("run out", "t_usage")
    set_budget("user", max_llm_calls=5)
    use_llm(LoopingLLM(answer_at=4))
    agent.ask_jarvis("finish up", "t_usage")
    use_llm(FakeLLM([upstream_503()]))
    agent.ask_jarvis("fail", "t_usage")

    rows = summarize_usage(group_by="scope")
    user = next(r for r in rows if r["group"] == "user")
    failed = sum(1 for r in load_turns()
                 if r.get("scope") == "user" and r.get("outcome") == "failed")
    assert user["errors"] == failed == 1, "budget stops are not counted as errors"
    assert user["exhausted_by"].get("steps", 0) >= 1, "stopped-early turns counted by limit"
    report = format_usage_table(rows, title="**Usage**")
    assert "stopped early (" in report and "steps" in report, "report shows stopped early by limit"
    assert "wrapped up" in report, "report shows wrapped up"
