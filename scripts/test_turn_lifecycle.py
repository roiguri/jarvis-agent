#!/usr/bin/env python3
"""Offline harness for the turn lifecycle — no model, no hub.

Runs against a scratch JARVIS_ROOT with a scripted fake LLM, so every turn
outcome can be planted on demand: an upstream error after a committed tool
call, an abnormal finish reason, a normal completion, a failed heartbeat tick.

    ./venv/bin/python scripts/test_turn_lifecycle.py
"""

import asyncio
import json
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
    """Plays a script: each entry is an AIMessage to return or an exception to raise."""

    model = "fake-model"

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def bind_tools(self, tools):
        return self

    def invoke(self, messages, **kwargs):
        self.calls += 1
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


UPSTREAM_503 = RuntimeError(
    "Error calling model 'gemini': 503 UNAVAILABLE. This model is currently experiencing high demand."
)

# --- 1. Upstream failure after a committed tool call -----------------------
agent.llm = FakeLLM([
    AIMessage(content="", tool_calls=[
        tool_call("list_memory", {}, 1), tool_call("list_memory", {}, 2),
    ]),
    UPSTREAM_503,
])
out = agent.ask_jarvis("tidy my memory", "t_fail")
check("failed: kind", out.kind, turn_budget.FAILED)
check("failed: never raised, has text", bool(out.text), True)
check("failed: cause names upstream", "unavailable" in (out.cause or ""), True)
check("failed: committed calls counted", out.committed_calls, (("list_memory", 2),))
check("failed: reply names committed calls", "list_memory ×2" in out.text, True)
msgs = thread_messages("t_fail")
check("failed: note is last message", isinstance(msgs[-1], AIMessage), True)
check("failed: note says do not re-run", "do not re-run" in msgs[-1].content, True)
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
check("internal: says nothing changed", "Nothing was changed" in out.text, True)

# --- 3. Abnormal finish reason --------------------------------------------
agent.llm = FakeLLM([AIMessage(
    content=[{"type": "text", "text": ""}],
    response_metadata={"finish_reason": "MALFORMED_FUNCTION_CALL"},
)])
out = agent.ask_jarvis("do the thing", "t_finish")
check("finish_reason: kind", out.kind, turn_budget.FAILED)
check("finish_reason: reason recorded", out.reason, "finish_reason: MALFORMED_FUNCTION_CALL")
check("finish_reason: reply names it", "MALFORMED_FUNCTION_CALL" in out.text, True)

# --- 4. Mirror cursor advances on a failed turn (no duplicate mirror) ------
with open(os.path.join(LOG_DIR, "notifications.jsonl"), "w", encoding="utf-8") as f:
    f.write(json.dumps({
        "ts": datetime.now(timezone.utc).isoformat(), "event": "heartbeat",
        "message": "morning briefing",
    }) + "\n")
agent.llm = FakeLLM([UPSTREAM_503])
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
        sent.append((event, text))
        return SendOutcome(ok=True)


factory.default_outbox = lambda: FakeOutbox()
heartbeat_state.any_due = lambda now: (True, ["inbox-check"])
heartbeat._last_tick_start = None
agent.llm = FakeLLM([
    AIMessage(content="", tool_calls=[tool_call("list_memory", {}, 10)]),
    UPSTREAM_503,
])
asyncio.run(heartbeat.run_heartbeat())
check("heartbeat failed: one notice sent", len(sent), 1)
if sent:
    event, text = sent[0]
    check("heartbeat failed: event", event, "heartbeat_failed")
    check("heartbeat failed: names task", "inbox-check" in text, True)
    check("heartbeat failed: names committed calls", "list_memory" in text, True)
    check("heartbeat failed: plain cause", "unavailable" in text, True)
check("heartbeat failed: mirror prefix registered",
      pending_mirrors.PREFIX.get("heartbeat_failed"), "[Heartbeat failed]")

# A tick that finishes with an ack sends nothing extra.
sent.clear()
heartbeat._last_tick_start = None
agent.llm = FakeLLM([
    AIMessage(content="", tool_calls=[tool_call("heartbeat_respond", {
        "acted_tasks": [], "notify": False, "summary": "nothing to do",
    }, 11)]),
    AIMessage(content="tick done"),
])
asyncio.run(heartbeat.run_heartbeat())
check("heartbeat ok: no failure notice", [e for e, _ in sent if e == "heartbeat_failed"], [])

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("ALL PASS")
