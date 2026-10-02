#!/usr/bin/env python3
"""Offline harness for the turn lifecycle — no model, no hub.

Runs against a scratch JARVIS_ROOT with a scripted fake LLM, so every turn
outcome can be planted on demand: an upstream error after a committed tool
call, an abnormal finish reason, a normal completion, a failed heartbeat tick.

    ./venv/bin/python scripts/test_turn_lifecycle.py
"""

import asyncio
import json
import sqlite3
import os
import pathlib
import sys
import tempfile
from datetime import datetime, timezone

_scratch = tempfile.mkdtemp(prefix="jarvis_turn_test_")
os.environ["JARVIS_ROOT"] = _scratch
os.environ.setdefault("GOOGLE_API_KEY", "offline-harness-dummy")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import config  # noqa: E402  (must import after JARVIS_ROOT is set)

assert config.DATA_DIR.startswith(_scratch), "scratch root not honored"
LOG_DIR = os.path.join(config.DATA_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

import agent  # noqa: E402
import heartbeat  # noqa: E402
import heartbeat_state  # noqa: E402
import pending_mirrors  # noqa: E402
import turn_budget  # noqa: E402
from gateway import factory  # noqa: E402
from gateway.base import OWNER_THREAD_ID  # noqa: E402
from gateway.outbox import SendOutcome  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402

FAILS: list[str] = []


def check(name, got, want=True):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f": {got!r} != {want!r}"))
    if not ok:
        FAILS.append(name)


class FakeLLM:
    """Plays a script: each entry is an AIMessage to return or an exception to
    raise. Records every request it was sent."""

    model = "fake-model"

    def __init__(self, script):
        self.script = list(script)
        self.sent = []

    def bind_tools(self, tools):
        return self

    def invoke(self, messages, **kwargs):
        self.sent.append(list(messages))
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def tool_call(name, args, n):
    return {"name": name, "args": args, "id": f"call_{n}", "type": "tool_call"}


def thread_messages(thread_id):
    snap = agent.agent_executor.get_state({"configurable": {"thread_id": thread_id}})
    return (snap.values or {}).get("messages", [])


def last_turn_row():
    with open(os.path.join(LOG_DIR, "turns.jsonl"), encoding="utf-8") as f:
        return json.loads(f.readlines()[-1])


def upstream_503():
    # A fresh exception per use: re-raising one instance chains every earlier
    # traceback onto the next.
    return RuntimeError("Error calling model 'gemini': 503 UNAVAILABLE. High demand.")


def run_tick():
    heartbeat._last_tick_start = None
    asyncio.run(heartbeat.run_heartbeat())

# --- 1. Upstream failure after a committed tool call -----------------------
agent.llm = FakeLLM([
    AIMessage(content="", tool_calls=[
        tool_call("list_memory", {}, 1), tool_call("list_memory", {}, 2),
    ]),
    upstream_503(),
])
out = agent.ask_jarvis("tidy my memory", "t_fail")
check("failed: kind", out.kind, turn_budget.FAILED)
check("failed: cause names upstream", "unavailable" in (out.cause or ""), True)
check("failed: committed calls counted", out.committed_calls, (("list_memory", 2),))
check("failed: reply names committed calls", "list_memory ×2" in out.text, True)
msgs = thread_messages("t_fail")
check("failed: note is last message", isinstance(msgs[-1], AIMessage), True)
check("failed: note says changes are saved", "Any changes they made are saved" in msgs[-1].content, True)
check("failed: note keeps tool pairs valid", isinstance(msgs[-2], ToolMessage), True)
row = last_turn_row()
check("failed: turns.jsonl outcome", row.get("outcome"), "failed")
check("failed: turns.jsonl error set", bool(row.get("error")), True)

# The next turn on that thread must run cleanly from START.
agent.llm = FakeLLM([AIMessage(content="all good now")])
out = agent.ask_jarvis("try again", "t_fail")
check("next turn after failure: completed", out.kind, turn_budget.COMPLETED)
check("next turn after failure: text", out.text, "all good now")
check("next turn: committed counter reset", out.committed_calls, ())
check("completed: turns.jsonl outcome", last_turn_row().get("outcome"), "completed")

# --- 2. Failure before any tool call --------------------------------------
agent.llm = FakeLLM([RuntimeError("boom")])
out = agent.ask_jarvis("hello", "t_internal")
check("internal: kind", out.kind, turn_budget.FAILED)
check("internal: cause is internal", "internal error" in (out.cause or ""), True)
check("internal: says nothing changed", "nothing was changed" in out.text, True)

# --- 3. Abnormal finish reason --------------------------------------------
agent.llm = FakeLLM([AIMessage(
    content=[{"type": "text", "text": ""}],
    response_metadata={"finish_reason": "MALFORMED_FUNCTION_CALL"},
)])
out = agent.ask_jarvis("do the thing", "t_finish")
check("finish_reason: kind", out.kind, turn_budget.FAILED)
check("finish_reason: recorded in turns.jsonl",
      last_turn_row().get("error"), "finish_reason: MALFORMED_FUNCTION_CALL")
check("finish_reason: reply names it", "MALFORMED_FUNCTION_CALL" in out.text, True)

# --- 4. Mirror cursor advances on a failed turn (no duplicate mirror) ------
with open(os.path.join(LOG_DIR, "notifications.jsonl"), "w", encoding="utf-8") as f:
    f.write(json.dumps({
        "ts": datetime.now(timezone.utc).isoformat(), "event": "heartbeat",
        "message": "morning briefing",
    }) + "\n")
agent.llm = FakeLLM([upstream_503()])
agent.ask_jarvis("reply to briefing", OWNER_THREAD_ID)
block, _ = pending_mirrors.drain_pending()
check("mirror: drained block not re-delivered after failure", block, None)
mirrors = [m for m in thread_messages(OWNER_THREAD_ID)
           if isinstance(m, HumanMessage) and "morning briefing" in str(m.content)]
check("mirror: block present once in the thread", len(mirrors), 1)

# --- 5. Heartbeat: failed tick notifies the owner -------------------------
sent: list[tuple[str, str]] = []


class FakeOutbox:
    async def notify_owner(self, text, *, event=None, metadata=None):
        # A tick-failure notice shows as "<event>+tick_failed".
        sent.append((f"{event}+tick_failed" if (metadata or {}).get("tick_failed") else event, text))
        return SendOutcome(ok=True)


factory.default_outbox = lambda: FakeOutbox()
heartbeat_state.any_due = lambda now: (True, ["inbox-check"])
agent.llm = FakeLLM([
    AIMessage(content="", tool_calls=[tool_call("list_memory", {}, 10)]),
    upstream_503(),
])
run_tick()
check("heartbeat failed: one notice sent", len(sent), 1)
if sent:
    event, text = sent[0]
    check("heartbeat failed: heartbeat event marked tick_failed", event, "heartbeat+tick_failed")
    check("heartbeat failed: names task", "inbox-check" in text, True)
    check("heartbeat failed: names committed calls", "list_memory" in text, True)
    check("heartbeat failed: plain cause", "unavailable" in text, True)

# A tick that finishes with an ack sends nothing extra.
sent.clear()
agent.llm = FakeLLM([
    AIMessage(content="", tool_calls=[tool_call("heartbeat_respond", {
        "acted_tasks": [], "notify": False, "summary": "nothing to do",
    }, 11)]),
    AIMessage(content="tick done"),
])
run_tick()
check("heartbeat ok: no failure notice", [e for e, _ in sent if e == "heartbeat+tick_failed"], [])

# A tick that fails before its input is checkpointed must not pick up the
# previous tick's ack: no stale briefing, no stale stamp, a failure notice.
sent.clear()
agent.llm = FakeLLM([
    AIMessage(content="", tool_calls=[tool_call("heartbeat_respond", {
        "acted_tasks": ["inbox-check"], "notify": True,
        "notification_text": "OLD BRIEFING", "summary": "sent",
    }, 12)]),
    AIMessage(content="tick done"),
])
run_tick()
check("stale ack setup: briefing sent", [t for _, t in sent], ["OLD BRIEFING"])
sent.clear()
stamped = []
heartbeat_state.stamp = lambda names, when: stamped.extend(names) or names
real_stream = agent.agent_executor.stream


def locked_stream(*args, **kwargs):
    raise sqlite3.OperationalError("database is locked")


agent.agent_executor.stream = locked_stream
run_tick()
agent.agent_executor.stream = real_stream
check("unsaved input: no stale briefing re-sent", "OLD BRIEFING" not in [t for _, t in sent], True)
check("unsaved input: no stale stamp", stamped, [])
check("unsaved input: failure notice sent", [e for e, _ in sent], ["heartbeat+tick_failed"])
check("unsaved input: thread has this tick's input before the note",
      isinstance(thread_messages("heartbeat")[-2], HumanMessage), True)

# The real Outbox: a broken tick's notice is logged as a heartbeat row marked
# tick_failed, and the pending-mirror drain carries it into the owner thread.
from gateway.outbox import Outbox  # noqa: E402
from tools.core.history import async_append_notification_log  # noqa: E402


class FakeChannel:
    def __init__(self):
        self.texts = []

    async def send_to_owner(self, text):
        self.texts.append(text)


channel = FakeChannel()
factory.default_outbox = lambda: Outbox(channel, log_sink=async_append_notification_log)
agent.llm = FakeLLM([upstream_503()])
run_tick()
row = json.loads(open(os.path.join(LOG_DIR, "notifications.jsonl")).readlines()[-1])
check("real outbox: notice delivered", len(channel.texts), 1)
check("real outbox: logged as a heartbeat row marked tick_failed",
      (row.get("event"), row.get("tick_failed")), ("heartbeat", True))
block, _ = pending_mirrors.drain_pending()
check("real outbox: mirrored into the owner thread as [Heartbeat]",
      "[Heartbeat] Heartbeat check at" in (block or ""), True)
factory.default_outbox = lambda: FakeOutbox()

# --- 5b. Error classification and telemetry that cannot fail a turn --------


class FakeAPIError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


wrapped = RuntimeError("Error calling model")
wrapped.__cause__ = FakeAPIError(503, "UNAVAILABLE")
check("describe_error: status code through the cause chain",
      turn_budget.describe_error(wrapped), "the model service was unavailable (overloaded upstream)")
check("describe_error: a 400 mentioning 503 is not upstream",
      "internal error" in turn_budget.describe_error(FakeAPIError(400, "bad arg near 503")), True)

from observability import telemetry  # noqa: E402

real_append = telemetry._append_line


def full_disk(path, record):
    raise OSError(28, "No space left on device")


telemetry._append_line = full_disk
agent.llm = FakeLLM([
    AIMessage(content="", tool_calls=[tool_call("list_memory", {}, 20)]),
    AIMessage(content="still fine"),
])
out = agent.ask_jarvis("disk is full", "t_disk")
telemetry._append_line = real_append
check("telemetry failure: turn still completes", (out.kind, out.text), (turn_budget.COMPLETED, "still fine"))

# --- 6. Trim at turn boundaries -------------------------------------------


def alternating(n):
    return [HumanMessage(f"h{i}") if i % 2 == 0 else AIMessage(f"a{i}") for i in range(n)]


check("reducer: non-turn write never trims",
      len(agent._add_and_trim(
          alternating(50),
          [ToolMessage(content="r", tool_call_id="x")],
      )), 51)
check("reducer: turn start trims to the cap",
      len(agent._add_and_trim(
          alternating(80),
          [HumanMessage("next")],
      )) <= agent.MAX_MESSAGES, True)


# Prior history so the window is already near the cap when the big turn starts.
for i in range(24):
    agent.llm = FakeLLM([AIMessage(content=f"reply {i}")])
    agent.ask_jarvis(f"chat {i}", "t_fanout")
# The incidents' shape: several moderate fan-outs adding up past the cap.
STEPS, PER_STEP = 3, 20
rec = FakeLLM([
    AIMessage(content="", tool_calls=[
        tool_call("list_memory", {}, 1000 * step + n) for n in range(PER_STEP)
    ])
    for step in range(STEPS)
] + [AIMessage(content="done fanning out")])
agent.llm = rec
out = agent.ask_jarvis("FANOUT REQUEST", "t_fanout")
check("fan-out: turn completes", out.kind, turn_budget.COMPLETED)
last = rec.sent[-1][1:]  # drop the system prompt
check("fan-out: last call still carries the turn's input",
      any(isinstance(m, HumanMessage) and m.content == "FANOUT REQUEST" for m in last), True)
check("fan-out: last call starts on a valid boundary", isinstance(last[0], HumanMessage), True)
msgs = thread_messages("t_fanout")
check("fan-out: checkpoint keeps prior history",
      any(isinstance(m, HumanMessage) and m.content == "chat 23" for m in msgs), True)
check("fan-out: window grew past the cap within the turn", len(msgs) > agent.MAX_MESSAGES, True)
rec = FakeLLM([AIMessage(content="ok")])
agent.llm = rec
agent.ask_jarvis("continue", "t_fanout")
sent_next = rec.sent[0][1:]
check("long turn: next turn still sees its answer",
      any(isinstance(m, AIMessage) and m.content == "done fanning out" for m in sent_next), True)
check("long turn: next turn starts on the long turn's input",
      isinstance(sent_next[0], HumanMessage) and sent_next[0].content == "FANOUT REQUEST", True)
agent.llm = FakeLLM([AIMessage(content="ok again")])
agent.ask_jarvis("one more", "t_fanout")
msgs = thread_messages("t_fanout")
check("long turn: dropped once it is two turns back", len(msgs) <= agent.MAX_MESSAGES, True)
check("long turn: trimmed window starts on a HumanMessage", isinstance(msgs[0], HumanMessage), True)
check("reducer: a mirror block and its message stay together",
      [m.content for m in agent._add_and_trim(
          alternating(80),
          [HumanMessage("[mirror]"), HumanMessage("reply")],
      )][-2:], ["[mirror]", "reply"])

# --- 7. Turn budget enforced in the graph --------------------------------
import dataclasses  # noqa: E402
import time  # noqa: E402

DEFAULT_POLICIES = dict(turn_budget.POLICIES)


def set_budget(scope, **budget):
    """Override one scope's limits; every other policy back to its default."""
    turn_budget.POLICIES.update(DEFAULT_POLICIES)
    pol = DEFAULT_POLICIES[scope]
    turn_budget.POLICIES[scope] = dataclasses.replace(
        pol, budget=dataclasses.replace(pol.budget, **budget))


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


# Steps: the call that reaches the limit is the last, made without tools.
set_budget("user", max_llm_calls=10)
loop = LoopingLLM()
agent.llm = loop
out = agent.ask_jarvis("plan everything", "t_steps")
check("steps: kind", out.kind, turn_budget.BUDGET_EXHAUSTED)
check("steps: never exceeds the call budget", len(loop.log), 10)
check("steps: only the last call is tool-free", [b for b, _, _ in loop.log], [True] * 9 + [False])
check("steps: no wrap-up before 80%", has_notice(loop.log[6][1]), False)
check("steps: wrap-up notice from 80%", has_notice(loop.log[7][1]), True)
check("steps: reply flags it stopped early", "Stopped early: it ran out of steps" in out.text, True)
check("steps: cause", out.cause, "it ran out of steps")
check("steps: turns.jsonl outcome", last_turn_row().get("outcome"), "budget_exhausted")
msgs = thread_messages("t_steps")
check("steps: notice never persisted", has_notice(msgs), False)
check("steps: thread ends on the tool-free answer",
      isinstance(msgs[-1], AIMessage) and not msgs[-1].tool_calls, True)

# Wrapped up: the model finishes after the notice.
set_budget("user", max_llm_calls=5)
loop = LoopingLLM(answer_at=4)
agent.llm = loop
out = agent.ask_jarvis("finish up", "t_wrap")
check("wrap-up: notice reached the model", has_notice(loop.log[3][1]), True)
check("wrap-up: answering after the notice is wrapped_up", out.kind, turn_budget.WRAPPED_UP)
check("wrap-up: counts as finished", out.finished, True)

# Time: the deadline ends the turn and caps each call's timeout.
set_budget("user", max_llm_calls=50, deadline_s=1.0)
loop = LoopingLLM(sleep=0.25)
agent.llm = loop
t0 = time.monotonic()
out = agent.ask_jarvis("slow work", "t_time")
check("time: kind", out.kind, turn_budget.BUDGET_EXHAUSTED)
check("time: cause", out.cause, "it ran out of time")
check("time: ended near the deadline", time.monotonic() - t0 < 2.5, True)
timeouts = [kw.get("timeout") for _, _, kw in loop.log]
check("time: every call got a capped timeout",
      all(t is not None and agent.MIN_CALL_TIMEOUT_S <= t <= agent.LLM_CALL_TIMEOUT_S for t in timeouts), True)

# Tokens: cumulative input past the ceiling ends the turn.
set_budget("user", max_llm_calls=50, max_input_tokens=100_000)
loop = LoopingLLM(input_tokens=30_000)
agent.llm = loop
out = agent.ask_jarvis("token heavy", "t_tokens")
check("tokens: kind", out.kind, turn_budget.BUDGET_EXHAUSTED)
check("tokens: cause", out.cause, "it reached its token budget")
check("tokens: stopped after the ceiling", len(loop.log), 5)

# Heartbeat: a tick that runs out of budget without acking notifies the owner.
set_budget("heartbeat", max_llm_calls=4)
sent.clear()
agent.llm = LoopingLLM()
run_tick()
check("heartbeat exhausted: failure notice sent",
      [e for e, _ in sent], ["heartbeat+tick_failed"])
check("heartbeat exhausted: cause in notice", "ran out of steps" in sent[0][1] if sent else False, True)

# The tool-free last call itself failing keeps the budget outcome.
set_budget("user", max_llm_calls=3)
agent.llm = FakeLLM([
    AIMessage(content="", tool_calls=[tool_call("list_memory", {}, 7001)]),
    AIMessage(content="", tool_calls=[tool_call("list_memory", {}, 7002)]),
    upstream_503(),
])
out = agent.ask_jarvis("long job", "t_exhaust_fail")
check("exhaustion call fails: still budget_exhausted", out.kind, turn_budget.BUDGET_EXHAUSTED)
check("exhaustion call fails: keeps the Stopped early line", "Stopped early" in out.text, True)

# Heartbeat wrap-up: the tick acks only what it finished; only that stamps.
set_budget("heartbeat", max_llm_calls=5)
heartbeat_state.any_due = lambda now: (True, ["inbox-check", "weekly-review"])
sent.clear()
stamped.clear()
agent.llm = FakeLLM([
    AIMessage(content="", tool_calls=[tool_call("list_memory", {}, 7100 + n)])
    for n in range(3)
] + [
    AIMessage(content="", tool_calls=[tool_call("heartbeat_respond", {
        "acted_tasks": ["inbox-check"], "notify": False, "summary": "partial",
    }, 7200)]),
    AIMessage(content="did inbox-check; weekly-review not done"),
])
run_tick()
check("heartbeat wrap-up: only the finished task stamps", stamped, ["inbox-check"])
check("heartbeat wrap-up: acked tick sends no failure notice",
      [e for e, _ in sent if e == "heartbeat+tick_failed"], [])

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("ALL PASS")
