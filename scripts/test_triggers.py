#!/usr/bin/env python3
"""Offline harness for triggers — no model, no channel.

Runs against a scratch JARVIS_ROOT with a fake Outbox, a scripted fake LLM and
a real scheduler on a short clock: the legacy migration, the store's row
handling, manage_trigger create/list/cancel, reminders firing, restoring after
a restart, failed sends retrying, scheduled wakes — their turn, delivery,
failure notice, the self-scheduling limits, and waiting for a running tick —
and gates: the engine, the Arbox gate on a faked fetch, a tick whose gated task
never reaches the model, and a task-linked wake seeing its task's block.

    ./venv/bin/python scripts/test_triggers.py
"""

import asyncio
import json
import os
import pathlib
import sys
import tempfile
from datetime import datetime, timedelta, timezone

_scratch = tempfile.mkdtemp(prefix="jarvis_triggers_test_")
os.environ["JARVIS_ROOT"] = _scratch
os.environ.setdefault("GOOGLE_API_KEY", "offline-harness-dummy")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import config  # noqa: E402  (must import after JARVIS_ROOT is set)

assert config.DATA_DIR.startswith(_scratch), "scratch root not honored"

import agent  # noqa: E402
import heartbeat  # noqa: E402
import heartbeat_state  # noqa: E402
import turn_context  # noqa: E402
from gateway import factory  # noqa: E402
from gateway.outbox import EVENT_HEARTBEAT, EVENT_REMINDER, SendOutcome  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402
from tools.core import manage_trigger  # noqa: E402
from tools.core.scheduling import MAX_PENDING_JARVIS_WAKES  # noqa: E402
from tools.fitness import gates as arbox  # noqa: E402
from triggers import gates, runner, scheduler, store  # noqa: E402
from triggers.gates import Cancel, GateResult, Upsert  # noqa: E402
from triggers.model import (  # noqa: E402
    ORIGIN_CODE, ORIGIN_JARVIS, ORIGIN_OWNER, PARENT_TICK, At, Send, Trigger, Turn,
)
from timeutils import ISRAEL_TZ  # noqa: E402

FAILS: list[str] = []


def check(name, got, want=True):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f": {got!r} != {want!r}"))
    if not ok:
        FAILS.append(name)


class FakeOutbox:
    """Records sends. ``fail_next`` makes that many sends fail first."""

    def __init__(self):
        self.sent: list[tuple[str, str]] = []
        self.meta: list[dict | None] = []
        self.fail_next = 0

    async def notify_owner(self, text, *, event=None, metadata=None):
        self.sent.append((event, text))
        self.meta.append(metadata)
        if self.fail_next:
            self.fail_next -= 1
            return SendOutcome(ok=False, error="offline")
        return SendOutcome(ok=True)


outbox = FakeOutbox()
factory.default_outbox = lambda: outbox


class FakeLLM:
    """Plays a script of AIMessages (or exceptions to raise); records requests."""

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


def call(name, args, n):
    return {"name": name, "args": args, "id": f"call_{n}", "type": "tool_call"}


def ack(text="", n=90):
    """A heartbeat_respond call, notifying with ``text`` when given."""
    return AIMessage(content="", tool_calls=[call("heartbeat_respond", {
        "acted_tasks": [], "notify": bool(text), "summary": "wake done",
        "notification_text": text}, n)])


def reset_store():
    for p in (store.STORE_PATH, store.LEGACY_PATH, store.LEGACY_PATH + ".migrated"):
        if os.path.exists(p):
            os.remove(p)


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def stored_rows():
    with open(store.STORE_PATH, encoding="utf-8") as f:
        return json.load(f)["triggers"]


def soon(seconds):
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def trig(action, **kw):
    return manage_trigger.invoke({"action": action, **kw})


def created_id(out):
    return out.split("[", 1)[1].split("]", 1)[0]


def as_background(running=None):
    """Make the next tool calls look like they run inside a heartbeat-scope
    turn — an hourly tick, or the wake ``running``."""
    turn_context.CURRENT_SCOPE.set("heartbeat")
    turn_context.CURRENT_TRIGGER.set(running)


def as_chat():
    turn_context.CURRENT_SCOPE.set(None)
    turn_context.CURRENT_TRIGGER.set(None)


async def main():
    sched = scheduler.init_scheduler()
    sched.start()

    # --- 1. Legacy migration -------------------------------------------------
    reset_store()
    write_json(store.LEGACY_PATH, {"events": [
        {"id": "aaa", "type": "reminder", "text": "one", "fire_at": "2030-01-01T09:00:00+00:00"},
        {"id": "bbb", "type": "reminder", "text": "two", "fire_at": "2030-01-02T09:00:00+00:00"},
        {"id": "ccc", "type": "mystery", "fire_at": "2030-01-03T09:00:00+00:00"},
    ]})
    got = store.all_triggers()
    check("migration: reminders carried over", sorted(t.id for t in got), ["aaa", "bbb"])
    check("migration: text and instant kept",
          (store.get("aaa").action.text, store.get("aaa").when.instant.isoformat()),
          ("one", "2030-01-01T09:00:00+00:00"))
    check("migration: legacy file kept as backup", os.path.exists(store.LEGACY_PATH + ".migrated"))
    check("migration: legacy file moved away", os.path.exists(store.LEGACY_PATH), False)
    write_json(store.LEGACY_PATH, {"events": [
        {"id": "zzz", "type": "reminder", "text": "late", "fire_at": "2030-01-04T09:00:00+00:00"},
    ]})
    check("migration: runs once — a store on disk wins", len(store.all_triggers()), 2)

    reset_store()
    check("fresh instance: empty store", store.all_triggers(), [])

    # --- 2. Unreadable rows are skipped, never dropped -----------------------
    reset_store()
    write_json(store.STORE_PATH, {"version": 1, "triggers": [{"id": "bad", "when": {}}]})
    store.add(Trigger("ok1", At(soon(3600)), Send("fine")))
    check("store: unreadable row skipped on read", [t.id for t in store.all_triggers()], ["ok1"])
    check("store: unreadable row kept on write", [r["id"] for r in stored_rows()], ["bad", "ok1"])
    check("store: remove reports a miss", store.remove("nope"), False)
    check("store: remove reports a hit", store.remove("ok1"), True)

    # --- 3. manage_trigger create / list / cancel -----------------------------
    reset_store()
    at = soon(3600).replace(microsecond=0)
    out = trig("create", at=at.isoformat(), message="water the plants")
    check("create reminder: confirms", out.startswith("Reminder ["), True)
    rid = created_id(out)
    check("create reminder: stored as a send", store.get(rid).action, Send("water the plants"))
    check("create reminder: from chat is the owner's", (store.get(rid).origin, store.get(rid).parent), (ORIGIN_OWNER, None))
    check("create reminder: armed", sched.get_job(f"trigger_{rid}") is not None)
    out = trig("create", at=at.isoformat(), instruction="check the download")
    check("create wake: confirms", out.startswith("Wake ["), True)
    wid = created_id(out)
    check("create wake: stored as a turn", store.get(wid).action, Turn("check the download"))
    listed = trig("list")
    check("list: shows both kinds", f"[{rid}]" in listed and 'reminder: "water the plants"' in listed
          and f"[{wid}]" in listed and 'wake: "check the download"' in listed and "in 0h 59m" in listed)
    out = trig("cancel", trigger_id=rid)
    check("cancel: confirms", out.startswith(f"Cancelled reminder [{rid}]"), True)
    check("cancel: removed from store", store.get(rid), None)
    check("cancel: disarmed", sched.get_job(f"trigger_{rid}"), None)
    trig("cancel", trigger_id=wid)
    check("list: empty", trig("list"), "Nothing scheduled.")
    check("cancel: unknown id", trig("cancel", trigger_id="nope").startswith("Nothing scheduled with id"), True)
    check("create: past instant refused",
          trig("create", at="2020-01-01T00:00:00Z", message="x"), "Error: `at` must be in the future.")
    check("create: naive instant refused",
          "no timezone offset" in trig("create", at="2030-01-01T09:00:00", message="x"))
    check("create: needs exactly one of message/instruction",
          trig("create", at=at.isoformat(), message="a", instruction="b").startswith("Error: create requires"))
    check("create: nothing stored by refusals", store.all_triggers(), [])

    # --- 4. Fires on time ----------------------------------------------------
    reset_store()
    outbox.sent.clear()
    rid = created_id(trig("create", at=soon(1).isoformat(), message="on time"))
    await asyncio.sleep(2.5)
    check("fire: sent once as a reminder event", outbox.sent, [(EVENT_REMINDER, "on time")])
    check("fire: removed after sending", store.get(rid), None)

    # --- 5. Restore after a restart ------------------------------------------
    reset_store()
    outbox.sent.clear()
    store.add(Trigger("late1", At(soon(-300)), Send("missed while down")))
    store.add(Trigger("next1", At(soon(3600)), Send("later")))
    tasks = scheduler.restore_pending()
    await asyncio.gather(*tasks)
    check("restore: past-due ran once", len(outbox.sent), 1)
    check("restore: past-due carries its original time",
          outbox.sent[0][1].startswith("[Originally scheduled for ") and outbox.sent[0][1].endswith("missed while down"))
    check("restore: past-due removed", store.get("late1"), None)
    check("restore: future one armed", sched.get_job("trigger_next1") is not None)
    check("restore: future one kept", store.get("next1") is not None)
    scheduler.disarm("next1")

    # --- 6. Failed sends retry, then give up ---------------------------------
    runner._RETRY_DELAY = timedelta(seconds=0.3)
    reset_store()
    outbox.sent.clear()
    outbox.fail_next = 1
    store.add(Trigger("flaky", At(soon(-1)), Send("try again")))
    await runner.run(store.get("flaky"))
    check("retry: kept after a failed send", store.get("flaky") is not None)
    await asyncio.sleep(1.5)
    check("retry: second attempt delivered", len(outbox.sent), 2)
    check("retry: removed once delivered", store.get("flaky"), None)

    reset_store()
    outbox.sent.clear()
    outbox.fail_next = 99
    store.add(Trigger("dead", At(soon(-1)), Send("never")))
    await runner.run(store.get("dead"))
    await asyncio.sleep(4.5)  # three retries; arm() runs each at least 1s out
    check("give up: first try plus three retries", len(outbox.sent), 1 + runner._MAX_RETRIES)
    check("give up: dropped from the store", store.get("dead"), None)
    check("give up: nothing left armed", sched.get_job("trigger_dead"), None)
    outbox.fail_next = 0
    runner._RETRY_DELAY = timedelta(seconds=0.3)

    # --- 7. Self-scheduling limits ------------------------------------------
    reset_store()
    later = soon(30 * 24 * 3600).isoformat()  # no horizon limit: a month out is fine
    as_background()
    out = trig("create", at=later, instruction="follow up")
    check("tick: may schedule a wake", out.startswith("Wake ["), True)
    tick_wake = store.get(created_id(out))
    check("tick: wake is Jarvis's, parented to the tick", (tick_wake.origin, tick_wake.parent), (ORIGIN_JARVIS, PARENT_TICK))
    owner_wake = Trigger("ownwake1", At(soon(3600)), Turn("x"))
    as_background(owner_wake)
    out = trig("create", at=later, instruction="one more")
    check("owner's wake: may schedule one follow-up", out.startswith("Wake ["), True)
    child = store.get(created_id(out))
    check("owner's wake: follow-up parented to it", (child.origin, child.parent), (ORIGIN_JARVIS, "ownwake1"))
    as_background(child)
    check("follow-up wake: can't schedule another wake",
          "can't schedule another wake" in trig("create", at=later, instruction="and again"))
    check("follow-up wake: can still set a plain reminder",
          trig("create", at=later, message="fixed text").startswith("Reminder ["), True)
    as_background()
    for _ in range(MAX_PENDING_JARVIS_WAKES - 2):
        trig("create", at=later, instruction="filler")
    check("cap: refused past the pending limit",
          "already pending" in trig("create", at=later, instruction="one too many"))
    check("cap: reminders are not capped",
          trig("create", at=later, message="still fine").startswith("Reminder ["), True)
    as_chat()
    check("chat: not capped",
          trig("create", at=later, instruction="from the owner").startswith("Wake ["), True)
    for t in store.all_triggers():
        scheduler.disarm(t.id)

    # --- 8. A wake runs its turn and delivers -------------------------------
    reset_store()
    outbox.sent.clear()
    outbox.meta.clear()
    llm = FakeLLM([ack("The download finished."), AIMessage(content="ok")])
    agent.llm = llm
    wid = created_id(trig("create", at=soon(1).isoformat(), instruction="check whether the download finished"))
    await asyncio.sleep(2.5)
    first_input = [m for m in llm.sent[0] if m.type == "human"][-1].content
    check("wake: turn got its instruction", f"Scheduled wake [{wid}]" in first_input
          and "check whether the download finished" in first_input)
    check("wake: delivered as a heartbeat event", outbox.sent, [(EVENT_HEARTBEAT, "The download finished.")])
    check("wake: delivery names its trigger", outbox.meta, [{"trigger": wid}])
    check("wake: removed", store.get(wid), None)

    # A wake with nothing to say sends nothing.
    outbox.sent.clear()
    agent.llm = FakeLLM([ack(""), AIMessage(content="ok")])
    created_id(trig("create", at=soon(1).isoformat(), instruction="quiet check"))
    await asyncio.sleep(2.5)
    check("quiet wake: nothing sent", outbox.sent, [])
    check("quiet wake: removed", store.all_triggers(), [])

    # --- 9. A broken wake is reported, never re-run --------------------------
    outbox.sent.clear()
    outbox.meta.clear()
    agent.llm = FakeLLM([RuntimeError("Error calling model: 503 UNAVAILABLE.")])
    created_id(trig("create", at=soon(1).isoformat(), instruction="summarize my inbox"))
    await asyncio.sleep(2.5)
    check("broken wake: one failure notice", len(outbox.sent), 1)
    if outbox.sent:
        check("broken wake: notice marked tick_failed", outbox.meta[0], {"tick_failed": True})
        check("broken wake: notice names the wake", "Scheduled wake" in outbox.sent[0][1]
              and "summarize my inbox" in outbox.sent[0][1])
    check("broken wake: not kept for a re-run", store.all_triggers(), [])

    # --- 10. A failed delivery retries the text, not the turn ----------------
    runner._RETRY_DELAY = timedelta(seconds=1.5)
    outbox.sent.clear()
    outbox.fail_next = 1
    llm = FakeLLM([ack("Your class starts at 20:00."), AIMessage(content="ok")])
    agent.llm = llm
    wid = created_id(trig("create", at=soon(1).isoformat(), instruction="brief me before class"))
    await asyncio.sleep(1.6)
    kept = store.get(wid)
    check("delivery failed: text kept as a send", kept.action if kept else None, Send("Your class starts at 20:00."))
    await asyncio.sleep(2)
    check("delivery failed: resent", [t for _, t in outbox.sent], ["Your class starts at 20:00."] * 2)
    check("delivery failed: the turn ran once", len(llm.sent), 2)
    check("delivery failed: removed once delivered", store.get(wid), None)

    # --- 11. A wake waits for a running tick ---------------------------------
    outbox.sent.clear()
    llm = FakeLLM([ack("done waiting"), AIMessage(content="ok")])
    agent.llm = llm
    await heartbeat.TURN_LOCK.acquire()
    created_id(trig("create", at=soon(1).isoformat(), instruction="after the tick"))
    await asyncio.sleep(2)
    check("lock: wake waits while a tick holds the thread", (len(llm.sent), outbox.sent), (0, []))
    heartbeat.TURN_LOCK.release()
    await asyncio.sleep(1)
    check("lock: wake runs once the tick is done", outbox.sent, [(EVENT_HEARTBEAT, "done waiting")])

    # --- 12. Inside a real wake turn, the tool sees the running wake --------
    reset_store()
    later = soon(7200).isoformat()
    agent.llm = FakeLLM([
        AIMessage(content="", tool_calls=[call("manage_trigger", {
            "action": "create", "at": later, "instruction": "check again"}, 91)]),
        ack(""), AIMessage(content="ok"),
    ])
    wid = created_id(trig("create", at=soon(1).isoformat(), instruction="check, then follow up"))
    await asyncio.sleep(2.5)
    rest = store.all_triggers()
    check("in-turn: the wake created one follow-up", len(rest), 1)
    if rest:
        check("in-turn: follow-up is Jarvis's, parented to the wake", (rest[0].origin, rest[0].parent), (ORIGIN_JARVIS, wid))
        scheduler.disarm(rest[0].id)

    # --- 13. The gate engine -------------------------------------------------
    reset_store()
    outbox.sent.clear()
    outbox.meta.clear()
    fake = {"fire": False, "followups": [], "raise_check": False, "raise_handler": False, "seen": []}

    @gates.gate("fake")
    def fake_check(state):
        fake["seen"].append(state)
        if fake["raise_check"]:
            raise RuntimeError("source down")
        return GateResult(fake["fire"], {"n": len(fake["seen"])}, "changed")

    @gates.gate_handler("fake")
    def fake_handler(result):
        if fake["raise_handler"]:
            raise RuntimeError("handler broke")
        return fake["followups"]

    check("engine: quiet check completes", await gates.evaluate("t1", "fake"), True)
    check("engine: quiet check commits its state", store.gate_state("t1"), {"n": 1})
    check("engine: quiet check schedules nothing", store.all_triggers(), [])
    at = soon(3600).replace(microsecond=0)
    fake.update(fire=True, followups=[Upsert("k:1:a", at, Turn("brief")), Upsert("k:1:b", at, Send("hi"))])
    check("engine: fired check completes", await gates.evaluate("t1", "fake"), True)
    made = {t.key: t for t in store.all_triggers()}
    check("engine: follow-ups created", sorted(made), ["k:1:a", "k:1:b"])
    check("engine: follow-ups are code's, parented to the task",
          {(t.origin, t.parent) for t in made.values()}, {(ORIGIN_CODE, "t1")})
    check("engine: a gate's wake belongs to its task", made["k:1:a"].action, Turn("brief", "t1"))
    check("engine: follow-ups armed", all(sched.get_job(f"trigger_{t.id}") for t in made.values()))
    ids = sorted(t.id for t in made.values())
    await gates.evaluate("t1", "fake")
    check("engine: same follow-ups twice change nothing", sorted(t.id for t in store.all_triggers()), ids)
    fake["followups"] = [Upsert("k:1:a", at + timedelta(hours=1), Turn("brief"))]
    await gates.evaluate("t1", "fake")
    check("engine: a moved key is replaced, not doubled",
          [t.when.instant for t in store.all_triggers() if t.key == "k:1:a"], [at + timedelta(hours=1)])
    fake["followups"] = [Cancel("k:1:")]
    await gates.evaluate("t1", "fake")
    check("engine: cancel by key prefix", store.all_triggers(), [])
    committed = store.gate_state("t1")
    fake.update(raise_handler=True, followups=[])
    check("engine: a failing handler doesn't complete", await gates.evaluate("t1", "fake"), False)
    check("engine: ...and doesn't commit the state", store.gate_state("t1"), committed)
    fake.update(raise_handler=False, raise_check=True)
    for _ in range(4):
        await gates.evaluate("t1", "fake")
    check("engine: one notice after three failures in a row", len(outbox.sent), 1)
    check("engine: notice marked gate_failed", outbox.meta[:1], [{"gate_failed": "t1"}])
    fake["raise_check"] = False
    await gates.evaluate("t1", "fake")
    fake["raise_check"] = True
    for _ in range(2):
        await gates.evaluate("t1", "fake")
    check("engine: a success resets the streak", len(outbox.sent), 1)
    fake["raise_check"] = False
    check("engine: an unknown gate fails, not crashes", await gates.evaluate("t2", "nope"), False)

    # --- 14. The Arbox gate (fetch and DB sync faked) ------------------------
    reset_store()
    applied = []
    registered = []
    arbox._fetch_registered = lambda: list(registered)
    arbox._apply_registered = lambda regs: applied.append([c["id"] for c in regs]) or "synced"
    TASK = "crossfit-sync-and-remind"

    def il(hours):
        """A class ``hours`` from now, on the half hour, Israel-local strings."""
        t = (datetime.now(ISRAEL_TZ) + timedelta(hours=hours)).replace(second=0, microsecond=0)
        t = t.replace(minute=30 if t.minute >= 30 else 0)
        return t.strftime("%Y-%m-%d"), t.strftime("%H:%M"), t

    def cls(cid, hours):
        d, t, _ = il(hours)
        return {"id": cid, "date": d, "time": t, "user_booked": 1, "box_categories": {"name": "WOD"}}

    def by_key():
        return {t.key: t for t in store.all_triggers()}

    registered[:] = [cls(101, 30)]
    check("arbox: a new class completes the task", await gates.evaluate(TASK, arbox.GATE), True)
    k = by_key()
    start = il(30)[2]
    check("arbox: DB synced with the fetched set", applied, [[101]])
    check("arbox: briefing 2h before the class",
          k["arbox:101:brief"].when.instant == start - timedelta(hours=2))
    check("arbox: check-in when the class ends",
          k["arbox:101:checkin"].when.instant == start + timedelta(minutes=60))
    check("arbox: wakes are linked to the crossfit task",
          {t.action.task for t in k.values()}, {TASK})
    check("arbox: the first run records the set, no change wake",
          [x for x in k if x.startswith("arbox:change:")], [])
    applied.clear()
    await gates.evaluate(TASK, arbox.GATE)
    check("arbox: unchanged registrations don't sync or schedule", (applied, sorted(by_key())),
          ([], ["arbox:101:brief", "arbox:101:checkin"]))
    registered.append(cls(102, 1))
    await gates.evaluate(TASK, arbox.GATE)
    k = by_key()
    brief = k["arbox:102:brief"].when.instant
    check("arbox: a class under 2h away is briefed now",
          abs((brief - datetime.now(timezone.utc)).total_seconds()) < 10)
    change = [t for x, t in k.items() if x.startswith("arbox:change:")]
    check("arbox: a new booking wakes Jarvis now, once", len(change), 1)
    check("arbox: ...naming the change", change[0].action.instruction.startswith("Workout schedule changed — new: ")
          and (change[0].when.instant - datetime.now(timezone.utc)).total_seconds() < 10)
    for t in change:
        store.remove(t.id)
        scheduler.disarm(t.id)
    registered[:] = [cls(102, 1)]
    await gates.evaluate(TASK, arbox.GATE)
    k = by_key()
    check("arbox: a dropped class loses its wakes", [x for x in k if x.startswith("arbox:101:")], [])
    change = [t for x, t in k.items() if x.startswith("arbox:change:")]
    check("arbox: ...and wakes Jarvis now about it", len(change) == 1 and "dropped: " in change[0].action.instruction)
    for t in change:
        store.remove(t.id)
        scheduler.disarm(t.id)
    past = {"classes": {"103": {"date": il(-3)[0], "time": il(-3)[1], "category": "WOD"},
                        "102": store.gate_state(TASK)["classes"]["102"]}}
    store.set_gate_state(TASK, past)
    applied.clear()
    await gates.evaluate(TASK, arbox.GATE)
    check("arbox: a class that already happened isn't 'dropped'", applied, [])
    check("arbox: ...and leaves the state", sorted(store.gate_state(TASK)["classes"]), ["102"])

    # One empty fetch is not believed; a second in a row is.
    for t in store.all_triggers():
        store.remove(t.id)
        scheduler.disarm(t.id)
    registered[:] = []
    applied.clear()
    await gates.evaluate(TASK, arbox.GATE)
    check("arbox: a single empty fetch drops nothing", (applied, [t.key for t in store.all_triggers()]), ([], []))
    check("arbox: ...and keeps the known classes", sorted(store.gate_state(TASK)["classes"]), ["102"])
    await gates.evaluate(TASK, arbox.GATE)
    check("arbox: a second empty fetch is believed", applied, [[]])
    check("arbox: ...and wakes Jarvis about the drop",
          any("dropped: " in t.action.instruction for t in store.all_triggers()))
    for t in store.all_triggers():
        store.remove(t.id)
        scheduler.disarm(t.id)

    # A class that has already started (e.g. earlier today) gets no wakes.
    store.set_gate_state(TASK, {"classes": {}})
    registered[:] = [cls(104, -0.5)]
    await gates.evaluate(TASK, arbox.GATE)
    check("arbox: a class already underway gets no wakes", [t.key for t in store.all_triggers()], [])

    # An unchanged tick doesn't rewrite the store.
    registered[:] = [cls(105, 30)]
    await gates.evaluate(TASK, arbox.GATE)
    before = os.stat(store.STORE_PATH).st_mtime_ns
    await asyncio.sleep(0.05)
    await gates.evaluate(TASK, arbox.GATE)
    check("arbox: an unchanged tick writes nothing", os.stat(store.STORE_PATH).st_mtime_ns, before)
    for t in store.all_triggers():
        scheduler.disarm(t.id)

    # --- 15. A tick: gated tasks never reach the model -----------------------
    reset_store()
    with open(heartbeat_state.HEARTBEAT_PATH, "w", encoding="utf-8") as f:
        f.write("# Heartbeat Tasks\n\n"
                "- **gated** | every 1h | gate: fake | notes: `heartbeat/gated.md`\n  Gated body.\n\n"
                "- **plain** | every 1h | notes: `heartbeat/plain.md`\n  Plain body.\n")
    heartbeat_state._parse_cache = None
    real_any_due = heartbeat_state.any_due
    heartbeat_state.any_due = lambda now: (True, ["gated", "plain"])
    fake.update(fire=False, raise_check=False)
    llm = FakeLLM([AIMessage(content="", tool_calls=[call("heartbeat_respond", {
        "acted_tasks": ["plain"], "notify": False, "summary": "s"}, 92)]), AIMessage(content="ok")])
    agent.llm = llm
    await heartbeat.run_heartbeat()
    system = llm.sent[0][0].content
    check("tick: only the ungated task reaches the model", "Plain body." in system and "Gated body." not in system)
    check("tick: the gated task isn't even named", "gated" not in system.replace("ungated", ""))
    stamps = heartbeat_state.load_state().get("last_run", {})
    check("tick: both tasks stamped", sorted(stamps), ["gated", "plain"])
    heartbeat_state.any_due = lambda now: (True, ["gated"])
    llm = FakeLLM([])
    agent.llm = llm
    await heartbeat.run_heartbeat()
    check("tick: a gated-only tick makes no model call", llm.sent, [])
    heartbeat_state.any_due = real_any_due

    # The due-gate failing open still keeps gated tasks away from the model.
    heartbeat_state.any_due = lambda now: (_ for _ in ()).throw(RuntimeError("gate maths broke"))
    llm = FakeLLM([AIMessage(content="", tool_calls=[call("heartbeat_respond", {
        "acted_tasks": [], "notify": False, "summary": "s"}, 93)]), AIMessage(content="ok")])
    agent.llm = llm
    await heartbeat.run_heartbeat()
    system = llm.sent[0][0].content
    check("fail-open tick: ungated tasks shown, gated not", "Plain body." in system and "Gated body." not in system)

    # A typo'd gate name falls back to the model rather than hiding the task.
    with open(heartbeat_state.HEARTBEAT_PATH, "w", encoding="utf-8") as f:
        f.write("# Heartbeat Tasks\n\n"
                "- **typo** | every 1h | gate: arbx | notes: `heartbeat/typo.md`\n  Typo body.\n")
    heartbeat_state._parse_cache = None
    heartbeat_state.any_due = lambda now: (True, ["typo"])
    llm = FakeLLM([AIMessage(content="", tool_calls=[call("heartbeat_respond", {
        "acted_tasks": ["typo"], "notify": False, "summary": "s"}, 94)]), AIMessage(content="ok")])
    agent.llm = llm
    await heartbeat.run_heartbeat()
    check("unknown gate: the task runs in the model instead", bool(llm.sent) and "Typo body." in llm.sent[0][0].content)
    heartbeat_state.any_due = real_any_due
    with open(heartbeat_state.HEARTBEAT_PATH, "w", encoding="utf-8") as f:
        f.write("# Heartbeat Tasks\n\n"
                "- **gated** | every 1h | gate: fake | notes: `heartbeat/gated.md`\n  Gated body.\n\n"
                "- **plain** | every 1h | notes: `heartbeat/plain.md`\n  Plain body.\n")
    heartbeat_state._parse_cache = None

    # A wake cancelled while it waits for the lock doesn't run.
    reset_store()
    llm = FakeLLM([ack("should not be sent"), AIMessage(content="ok")])
    agent.llm = llm
    outbox.sent.clear()
    await heartbeat.TURN_LOCK.acquire()
    wid = created_id(trig("create", at=soon(1).isoformat(), instruction="brief me"))
    await asyncio.sleep(1.8)
    trig("cancel", trigger_id=wid)
    heartbeat.TURN_LOCK.release()
    await asyncio.sleep(0.5)
    check("queued wake: cancelled while waiting, so it doesn't run", (llm.sent, outbox.sent), ([], []))

    # A corrupt store is moved aside, never overwritten with an empty one.
    with open(store.STORE_PATH, "w", encoding="utf-8") as f:
        f.write("{not json")
    check("corrupt store: reads as empty", store.all_triggers(), [])
    aside = [n for n in os.listdir(os.path.dirname(store.STORE_PATH)) if ".corrupt-" in n]
    check("corrupt store: the bad file is kept aside", len(aside), 1)

    # --- 16. A task-linked wake sees its task's block ------------------------
    llm = FakeLLM([ack(""), AIMessage(content="ok")])
    agent.llm = llm
    wake = Trigger("linked01", At(soon(-1)), Turn("Pre-class briefing: Mon 20:00 WOD.", "gated"), ORIGIN_CODE, "gated")
    store.add(wake)
    await heartbeat.run_wake(wake)
    system = llm.sent[0][0].content
    first_input = [m for m in llm.sent[0] if m.type == "human"][-1].content
    check("linked wake: its task's block is shown", "Gated body." in system and "Plain body." not in system)
    check("linked wake: the turn is told which task", "task gated" in first_input)

    sched.shutdown(wait=False)


asyncio.run(main())
print("\nALL PASS" if not FAILS else f"\n{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
