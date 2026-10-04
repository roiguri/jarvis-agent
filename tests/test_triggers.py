"""Triggers against a fake Outbox, a scripted fake model and a real scheduler on
a short clock: the legacy migration, the store's row handling, manage_trigger
create/list/cancel, reminders firing, restoring after a restart, failed sends
retrying, scheduled wakes — their turn, delivery, failure notice, the
self-scheduling limits, and waiting for a running tick — and gates: the engine,
the Arbox gate on a faked fetch, a tick whose gated task never reaches the model,
and a task-linked wake seeing its task's block."""

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from langchain_core.messages import AIMessage

import heartbeat
import heartbeat_state
import turn_context
from gateway.outbox import EVENT_HEARTBEAT, EVENT_REMINDER
from tests.fakes import FakeLLM, tool_call
from timeutils import ISRAEL_TZ
from tools.core import manage_trigger
from tools.core.scheduling import MAX_PENDING_JARVIS_WAKES
from tools.fitness import gates as arbox
from triggers import gates, runner, scheduler, store
from triggers.gates import Cancel, GateResult, Upsert
from triggers.model import (
    ORIGIN_CODE, ORIGIN_JARVIS, ORIGIN_OWNER, PARENT_TICK, At, Send, Trigger, Turn,
)

GATED_AND_PLAIN = ("# Heartbeat Tasks\n\n"
                   "- **gated** | every 1h | gate: fake | notes: `heartbeat/gated.md`\n  Gated body.\n\n"
                   "- **plain** | every 1h | notes: `heartbeat/plain.md`\n  Plain body.\n")


def ack(text="", n=90):
    """A heartbeat_respond call, notifying with ``text`` when given."""
    return AIMessage(content="", tool_calls=[tool_call("heartbeat_respond", {
        "acted_tasks": [], "notify": bool(text), "summary": "wake done",
        "notification_text": text}, n)])


def reset_store():
    for p in (store.STORE_PATH, store.LEGACY_PATH, store.LEGACY_PATH + ".migrated",
              store.LEGACY_STAMPS_PATH, store.LEGACY_STAMPS_PATH + ".migrated"):
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


def write_heartbeat_md(text):
    with open(heartbeat_state.HEARTBEAT_PATH, "w", encoding="utf-8") as f:
        f.write(text)
    heartbeat_state._parse_cache = None


@pytest.fixture(autouse=True)
def clean_store():
    reset_store()
    yield
    reset_store()


@pytest_asyncio.fixture
async def sched(monkeypatch, fake_outbox):
    """A started scheduler on this test's loop, a fresh turn lock, and the
    fake Outbox; every armed job goes with the scheduler at the end."""
    monkeypatch.setattr(heartbeat, "TURN_LOCK", asyncio.Lock())
    monkeypatch.setattr(runner, "_RETRY_DELAY", timedelta(seconds=0.3))
    s = scheduler.init_scheduler()
    s.start()
    yield s
    s.shutdown(wait=False)


@pytest.fixture
def fake_gate(monkeypatch):
    """Registers the gate "fake", steered by the returned dict."""
    fake = {"fire": False, "followups": [], "raise_check": False, "raise_handler": False, "seen": []}

    def check(state):
        fake["seen"].append(state)
        if fake["raise_check"]:
            raise RuntimeError("source down")
        return GateResult(fake["fire"], {"n": len(fake["seen"])}, "changed")

    def handler(result):
        if fake["raise_handler"]:
            raise RuntimeError("handler broke")
        return fake["followups"]

    monkeypatch.setitem(gates._CHECKS, "fake", check)
    monkeypatch.setitem(gates._HANDLERS, "fake", handler)
    monkeypatch.setattr(gates, "_failures", {})
    return fake


@pytest.fixture
def heartbeat_md():
    """Write HEARTBEAT.md for this test; removed afterwards."""
    yield write_heartbeat_md
    if os.path.exists(heartbeat_state.HEARTBEAT_PATH):
        os.remove(heartbeat_state.HEARTBEAT_PATH)
    heartbeat_state._parse_cache = None


# --- Scheduler and store -------------------------------------------------------

@pytest.mark.asyncio
async def test_hourly_tick_registered_on_the_hour():
    s = scheduler.init_scheduler()
    scheduler.add_heartbeat(heartbeat.run_heartbeat)
    job = s.get_job("heartbeat")
    assert (job is not None, str(job.trigger) if job else None) == \
        (True, "cron[hour='*/1', minute='0']")


def test_legacy_reminders_migrate():
    write_json(store.LEGACY_PATH, {"events": [
        {"id": "aaa", "type": "reminder", "text": "one", "fire_at": "2030-01-01T09:00:00+00:00"},
        {"id": "bbb", "type": "reminder", "text": "two", "fire_at": "2030-01-02T09:00:00+00:00"},
        {"id": "ccc", "type": "mystery", "fire_at": "2030-01-03T09:00:00+00:00"},
    ]})
    got = store.all_triggers()
    assert sorted(t.id for t in got) == ["aaa", "bbb"], "reminders carried over"
    assert (store.get("aaa").action.text, store.get("aaa").when.instant.isoformat()) == \
        ("one", "2030-01-01T09:00:00+00:00"), "text and instant kept"
    assert os.path.exists(store.LEGACY_PATH + ".migrated"), "legacy file kept as backup"
    assert not os.path.exists(store.LEGACY_PATH), "legacy file moved away"
    write_json(store.LEGACY_PATH, {"events": [
        {"id": "zzz", "type": "reminder", "text": "late", "fire_at": "2030-01-04T09:00:00+00:00"},
    ]})
    assert len(store.all_triggers()) == 2, "runs once — a store on disk wins"


def test_fresh_store_is_empty():
    assert store.all_triggers() == []


def test_legacy_stamps_migrate():
    write_json(store.LEGACY_STAMPS_PATH, {"last_run": {"inbox-check": "2026-10-03T09:00:00+00:00"}})
    assert store.task_stamps() == {"inbox-check": "2026-10-03T09:00:00+00:00"}, "migrated into the store"
    assert os.path.exists(store.LEGACY_STAMPS_PATH + ".migrated"), "old file kept as backup"
    write_json(store.LEGACY_STAMPS_PATH, {"last_run": {"other": "2026-10-03T10:00:00+00:00"}})
    assert sorted(store.task_stamps()) == ["inbox-check"], "migration runs once"

    reset_store()
    write_json(store.LEGACY_PATH, {"events": [
        {"id": "r1", "type": "reminder", "text": "x", "fire_at": "2030-01-01T09:00:00+00:00"}]})
    write_json(store.LEGACY_STAMPS_PATH, {"last_run": {"t": "2026-10-03T09:00:00+00:00"}})
    assert ([t.id for t in store.all_triggers()], store.task_stamps()) == \
        (["r1"], {"t": "2026-10-03T09:00:00+00:00"}), "both legacy files migrate on one read"


def test_unreadable_rows_skipped_never_dropped():
    write_json(store.STORE_PATH, {"version": 1, "triggers": [{"id": "bad", "when": {}}]})
    store.add(Trigger("ok1", At(soon(3600)), Send("fine")))
    assert [t.id for t in store.all_triggers()] == ["ok1"], "unreadable row skipped on read"
    assert [r["id"] for r in stored_rows()] == ["bad", "ok1"], "unreadable row kept on write"
    assert store.remove("nope") is False, "remove reports a miss"
    assert store.remove("ok1") is True, "remove reports a hit"


def test_corrupt_store_moved_aside():
    """A corrupt store is moved aside, never overwritten with an empty one."""
    os.makedirs(os.path.dirname(store.STORE_PATH), exist_ok=True)
    for n in os.listdir(os.path.dirname(store.STORE_PATH)):
        if ".corrupt-" in n:
            os.remove(os.path.join(os.path.dirname(store.STORE_PATH), n))
    with open(store.STORE_PATH, "w", encoding="utf-8") as f:
        f.write("{not json")
    assert store.all_triggers() == [], "reads as empty"
    aside = [n for n in os.listdir(os.path.dirname(store.STORE_PATH)) if ".corrupt-" in n]
    assert len(aside) == 1, "the bad file is kept aside"


# --- manage_trigger ------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_list_cancel(sched):
    at = soon(3600).replace(microsecond=0)
    out = trig("create", at=at.isoformat(), message="water the plants")
    assert out.startswith("Reminder ["), "create reminder: confirms"
    rid = created_id(out)
    assert store.get(rid).action == Send("water the plants"), "create reminder: stored as a send"
    assert (store.get(rid).origin, store.get(rid).parent) == (ORIGIN_OWNER, None), \
        "create reminder: from chat is the owner's"
    assert sched.get_job(f"trigger_{rid}") is not None, "create reminder: armed"
    out = trig("create", at=at.isoformat(), instruction="check the download")
    assert out.startswith("Wake ["), "create wake: confirms"
    wid = created_id(out)
    assert store.get(wid).action == Turn("check the download"), "create wake: stored as a turn"
    listed = trig("list")
    assert (f"[{rid}]" in listed and 'reminder: "water the plants"' in listed
            and f"[{wid}]" in listed and 'wake: "check the download"' in listed
            and "in 0h 59m" in listed), "list: shows both kinds"
    out = trig("cancel", trigger_id=rid)
    assert out.startswith(f"Cancelled reminder [{rid}]"), "cancel: confirms"
    assert store.get(rid) is None, "cancel: removed from store"
    assert sched.get_job(f"trigger_{rid}") is None, "cancel: disarmed"
    trig("cancel", trigger_id=wid)
    assert trig("list") == "Nothing scheduled.", "list: empty"
    assert trig("cancel", trigger_id="nope").startswith("Nothing scheduled with id"), "cancel: unknown id"


@pytest.mark.asyncio
async def test_create_refusals(sched):
    at = soon(3600).replace(microsecond=0)
    assert trig("create", at="2020-01-01T00:00:00Z", message="x") == \
        "Error: `at` must be in the future.", "past instant refused"
    assert "no timezone offset" in trig("create", at="2030-01-01T09:00:00", message="x"), \
        "naive instant refused"
    assert trig("create", at=at.isoformat(), message="a", instruction="b").startswith(
        "Error: create requires"), "needs exactly one of message/instruction"
    assert store.all_triggers() == [], "nothing stored by refusals"


# --- Reminders firing ----------------------------------------------------------

@pytest.mark.asyncio
async def test_reminder_fires_on_time(sched, fake_outbox):
    rid = created_id(trig("create", at=soon(1).isoformat(), message="on time"))
    await asyncio.sleep(2.5)
    assert fake_outbox.sent == [(EVENT_REMINDER, "on time")], "sent once as a reminder event"
    assert store.get(rid) is None, "removed after sending"


@pytest.mark.asyncio
async def test_restore_after_restart(sched, fake_outbox):
    store.add(Trigger("late1", At(soon(-300)), Send("missed while down")))
    store.add(Trigger("next1", At(soon(3600)), Send("later")))
    await asyncio.gather(*scheduler.restore_pending())
    assert len(fake_outbox.sent) == 1, "past-due ran once"
    assert fake_outbox.sent[0][1].startswith("[Originally scheduled for ") \
        and fake_outbox.sent[0][1].endswith("missed while down"), "past-due carries its original time"
    assert store.get("late1") is None, "past-due removed"
    assert sched.get_job("trigger_next1") is not None, "future one armed"
    assert store.get("next1") is not None, "future one kept"


@pytest.mark.asyncio
async def test_failed_send_retries(sched, fake_outbox):
    fake_outbox.fail_next = 1
    store.add(Trigger("flaky", At(soon(-1)), Send("try again")))
    await runner.run(store.get("flaky"))
    assert store.get("flaky") is not None, "kept after a failed send"
    await asyncio.sleep(1.5)
    assert len(fake_outbox.sent) == 2, "second attempt delivered"
    assert store.get("flaky") is None, "removed once delivered"


@pytest.mark.asyncio
async def test_failed_send_gives_up(sched, fake_outbox):
    fake_outbox.fail_next = 99
    store.add(Trigger("dead", At(soon(-1)), Send("never")))
    await runner.run(store.get("dead"))
    await asyncio.sleep(4.5)  # three retries; arm() runs each at least 1s out
    assert len(fake_outbox.sent) == 1 + runner._MAX_RETRIES, "first try plus three retries"
    assert store.get("dead") is None, "dropped from the store"
    assert sched.get_job("trigger_dead") is None, "nothing left armed"


# --- Self-scheduling limits ----------------------------------------------------

@pytest.mark.asyncio
async def test_self_scheduling_limits(sched):
    later = soon(30 * 24 * 3600).isoformat()  # no horizon limit: a month out is fine
    as_background()
    out = trig("create", at=later, instruction="follow up")
    assert out.startswith("Wake ["), "tick: may schedule a wake"
    tick_wake = store.get(created_id(out))
    assert (tick_wake.origin, tick_wake.parent) == (ORIGIN_JARVIS, PARENT_TICK), \
        "tick: wake is Jarvis's, parented to the tick"
    owner_wake = Trigger("ownwake1", At(soon(3600)), Turn("x"))
    as_background(owner_wake)
    out = trig("create", at=later, instruction="one more")
    assert out.startswith("Wake ["), "owner's wake: may schedule one follow-up"
    child = store.get(created_id(out))
    assert (child.origin, child.parent) == (ORIGIN_JARVIS, "ownwake1"), \
        "owner's wake: follow-up parented to it"
    as_background(child)
    assert "can't schedule another wake" in trig("create", at=later, instruction="and again"), \
        "follow-up wake: can't schedule another wake"
    assert trig("create", at=later, message="fixed text").startswith("Reminder ["), \
        "follow-up wake: can still set a plain reminder"
    as_background()
    for _ in range(MAX_PENDING_JARVIS_WAKES - 2):
        trig("create", at=later, instruction="filler")
    assert "already pending" in trig("create", at=later, instruction="one too many"), \
        "cap: refused past the pending limit"
    assert trig("create", at=later, message="still fine").startswith("Reminder ["), \
        "cap: reminders are not capped"
    as_chat()
    assert trig("create", at=later, instruction="from the owner").startswith("Wake ["), \
        "chat: not capped"


# --- Wakes ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_wake_runs_its_turn_and_delivers(sched, fake_outbox, use_llm):
    llm = use_llm(FakeLLM([ack("The download finished."), AIMessage(content="ok")]))
    wid = created_id(trig("create", at=soon(1).isoformat(), instruction="check whether the download finished"))
    await asyncio.sleep(2.5)
    first_input = [m for m in llm.sent[0] if m.type == "human"][-1].content
    assert f"Scheduled wake [{wid}]" in first_input \
        and "check whether the download finished" in first_input, "turn got its instruction"
    assert fake_outbox.sent == [(EVENT_HEARTBEAT, "The download finished.")], "delivered as a heartbeat event"
    assert fake_outbox.meta == [{"trigger": wid}], "delivery names its trigger"
    assert store.get(wid) is None, "removed"


@pytest.mark.asyncio
async def test_quiet_wake_sends_nothing(sched, fake_outbox, use_llm):
    use_llm(FakeLLM([ack(""), AIMessage(content="ok")]))
    trig("create", at=soon(1).isoformat(), instruction="quiet check")
    await asyncio.sleep(2.5)
    assert fake_outbox.sent == [], "nothing sent"
    assert store.all_triggers() == [], "removed"


@pytest.mark.asyncio
async def test_broken_wake_reported_never_rerun(sched, fake_outbox, use_llm):
    use_llm(FakeLLM([RuntimeError("Error calling model: 503 UNAVAILABLE.")]))
    trig("create", at=soon(1).isoformat(), instruction="summarize my inbox")
    await asyncio.sleep(2.5)
    assert len(fake_outbox.sent) == 1, "one failure notice"
    assert fake_outbox.meta[0] == {"tick_failed": True}, "notice marked tick_failed"
    assert "Scheduled wake" in fake_outbox.sent[0][1] and "summarize my inbox" in fake_outbox.sent[0][1], \
        "notice names the wake"
    assert store.all_triggers() == [], "not kept for a re-run"


@pytest.mark.asyncio
async def test_failed_delivery_retries_text_not_turn(sched, fake_outbox, use_llm, monkeypatch):
    monkeypatch.setattr(runner, "_RETRY_DELAY", timedelta(seconds=1.5))
    fake_outbox.fail_next = 1
    llm = use_llm(FakeLLM([ack("Your class starts at 20:00."), AIMessage(content="ok")]))
    wid = created_id(trig("create", at=soon(1).isoformat(), instruction="brief me before class"))
    await asyncio.sleep(1.6)
    kept = store.get(wid)
    assert (kept.action if kept else None) == Send("Your class starts at 20:00."), "text kept as a send"
    await asyncio.sleep(2)
    assert [t for _, t in fake_outbox.sent] == ["Your class starts at 20:00."] * 2, "resent"
    assert len(llm.sent) == 2, "the turn ran once"
    assert store.get(wid) is None, "removed once delivered"


@pytest.mark.asyncio
async def test_wake_waits_for_running_tick(sched, fake_outbox, use_llm):
    llm = use_llm(FakeLLM([ack("done waiting"), AIMessage(content="ok")]))
    await heartbeat.TURN_LOCK.acquire()
    trig("create", at=soon(1).isoformat(), instruction="after the tick")
    await asyncio.sleep(2)
    assert (len(llm.sent), fake_outbox.sent) == (0, []), "wake waits while a tick holds the thread"
    heartbeat.TURN_LOCK.release()
    await asyncio.sleep(1)
    assert fake_outbox.sent == [(EVENT_HEARTBEAT, "done waiting")], "wake runs once the tick is done"


@pytest.mark.asyncio
async def test_cancelled_queued_wake_does_not_run(sched, fake_outbox, use_llm):
    llm = use_llm(FakeLLM([ack("should not be sent"), AIMessage(content="ok")]))
    await heartbeat.TURN_LOCK.acquire()
    wid = created_id(trig("create", at=soon(1).isoformat(), instruction="brief me"))
    await asyncio.sleep(1.8)
    trig("cancel", trigger_id=wid)
    heartbeat.TURN_LOCK.release()
    await asyncio.sleep(0.5)
    assert (llm.sent, fake_outbox.sent) == ([], [])


@pytest.mark.asyncio
async def test_wake_sees_itself_in_turn(sched, use_llm):
    """Inside a real wake turn, the tool sees the running wake."""
    later = soon(7200).isoformat()
    use_llm(FakeLLM([
        AIMessage(content="", tool_calls=[tool_call("manage_trigger", {
            "action": "create", "at": later, "instruction": "check again"}, 91)]),
        ack(""), AIMessage(content="ok"),
    ]))
    wid = created_id(trig("create", at=soon(1).isoformat(), instruction="check, then follow up"))
    await asyncio.sleep(2.5)
    rest = store.all_triggers()
    assert len(rest) == 1, "the wake created one follow-up"
    assert (rest[0].origin, rest[0].parent) == (ORIGIN_JARVIS, wid), \
        "follow-up is Jarvis's, parented to the wake"


# --- The gate engine -----------------------------------------------------------

@pytest.mark.asyncio
async def test_gate_engine(sched, fake_outbox, fake_gate):
    fake = fake_gate
    assert await gates.evaluate("t1", "fake") is True, "quiet check completes"
    assert store.gate_state("t1") == {"n": 1}, "quiet check commits its state"
    assert store.all_triggers() == [], "quiet check schedules nothing"
    at = soon(3600).replace(microsecond=0)
    fake.update(fire=True, followups=[Upsert("k:1:a", at, Turn("brief")), Upsert("k:1:b", at, Send("hi"))])
    assert await gates.evaluate("t1", "fake") is True, "fired check completes"
    made = {t.key: t for t in store.all_triggers()}
    assert sorted(made) == ["k:1:a", "k:1:b"], "follow-ups created"
    assert {(t.origin, t.parent) for t in made.values()} == {(ORIGIN_CODE, "t1")}, \
        "follow-ups are code's, parented to the task"
    assert made["k:1:a"].action == Turn("brief", "t1"), "a gate's wake belongs to its task"
    assert all(sched.get_job(f"trigger_{t.id}") for t in made.values()), "follow-ups armed"
    ids = sorted(t.id for t in made.values())
    await gates.evaluate("t1", "fake")
    assert sorted(t.id for t in store.all_triggers()) == ids, "same follow-ups twice change nothing"
    fake["followups"] = [Upsert("k:1:a", at + timedelta(hours=1), Turn("brief"))]
    await gates.evaluate("t1", "fake")
    assert [t.when.instant for t in store.all_triggers() if t.key == "k:1:a"] == [at + timedelta(hours=1)], \
        "a moved key is replaced, not doubled"
    fake["followups"] = [Cancel("k:1:")]
    await gates.evaluate("t1", "fake")
    assert store.all_triggers() == [], "cancel by key prefix"
    committed = store.gate_state("t1")
    fake.update(raise_handler=True, followups=[])
    assert await gates.evaluate("t1", "fake") is False, "a failing handler doesn't complete"
    assert store.gate_state("t1") == committed, "...and doesn't commit the state"
    fake.update(raise_handler=False, raise_check=True)
    for _ in range(4):
        await gates.evaluate("t1", "fake")
    assert len(fake_outbox.sent) == 1, "one notice after three failures in a row"
    assert fake_outbox.meta[:1] == [{"gate_failed": "t1"}], "notice marked gate_failed"
    fake["raise_check"] = False
    await gates.evaluate("t1", "fake")
    fake["raise_check"] = True
    for _ in range(2):
        await gates.evaluate("t1", "fake")
    assert len(fake_outbox.sent) == 1, "a success resets the streak"
    fake["raise_check"] = False
    assert await gates.evaluate("t2", "nope") is False, "an unknown gate fails, not crashes"


# --- The Arbox gate (fetch and DB sync faked) ----------------------------------

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


def clear_triggers():
    for t in store.all_triggers():
        store.remove(t.id)
        scheduler.disarm(t.id)


@pytest.fixture
def arbox_fake(monkeypatch):
    """Arbox's fetch returns ``registered``; DB syncs are recorded in ``applied``."""
    registered, applied = [], []
    monkeypatch.setattr(arbox, "_fetch_registered", lambda: list(registered))
    monkeypatch.setattr(arbox, "_apply_registered",
                        lambda regs: applied.append([c["id"] for c in regs]) or "synced")
    monkeypatch.setattr(gates, "_failures", {})
    return registered, applied


@pytest.mark.asyncio
async def test_arbox_gate(sched, fake_outbox, arbox_fake):
    registered, applied = arbox_fake
    registered[:] = [cls(101, 30)]
    assert await gates.evaluate(TASK, arbox.GATE) is True, "a new class completes the task"
    k = by_key()
    start = il(30)[2]
    assert applied == [[101]], "DB synced with the fetched set"
    assert k["arbox:101:brief"].when.instant == start - timedelta(hours=2), "briefing 2h before the class"
    assert k["arbox:101:checkin"].when.instant == start + timedelta(minutes=60), "check-in when the class ends"
    assert {t.action.task for t in k.values()} == {TASK}, "wakes are linked to the crossfit task"
    assert [x for x in k if x.startswith("arbox:change:")] == [], "the first run records the set, no change wake"
    applied.clear()
    await gates.evaluate(TASK, arbox.GATE)
    assert (applied, sorted(by_key())) == ([], ["arbox:101:brief", "arbox:101:checkin"]), \
        "unchanged registrations don't sync or schedule"

    registered.append(cls(102, 1))
    await gates.evaluate(TASK, arbox.GATE)
    k = by_key()
    brief = k["arbox:102:brief"].when.instant
    assert abs((brief - datetime.now(timezone.utc)).total_seconds()) < 10, "a class under 2h away is briefed now"
    change = [t for x, t in k.items() if x.startswith("arbox:change:")]
    assert len(change) == 1, "a new booking wakes Jarvis now, once"
    assert change[0].action.instruction.startswith("Workout schedule changed — new: ") \
        and (change[0].when.instant - datetime.now(timezone.utc)).total_seconds() < 10, "...naming the change"
    for t in change:
        store.remove(t.id)
        scheduler.disarm(t.id)

    registered[:] = [cls(102, 1)]
    await gates.evaluate(TASK, arbox.GATE)
    k = by_key()
    assert [x for x in k if x.startswith("arbox:101:")] == [], "a dropped class loses its wakes"
    change = [t for x, t in k.items() if x.startswith("arbox:change:")]
    assert len(change) == 1 and "dropped: " in change[0].action.instruction, "...and wakes Jarvis now about it"
    for t in change:
        store.remove(t.id)
        scheduler.disarm(t.id)

    past = {"classes": {"103": {"date": il(-3)[0], "time": il(-3)[1], "category": "WOD"},
                        "102": store.gate_state(TASK)["classes"]["102"]}}
    store.set_gate_state(TASK, past)
    applied.clear()
    await gates.evaluate(TASK, arbox.GATE)
    assert applied == [], "a class that already happened isn't 'dropped'"
    assert sorted(store.gate_state(TASK)["classes"]) == ["102"], "...and leaves the state"

    # One empty fetch is not believed; a second in a row is.
    clear_triggers()
    registered[:] = []
    applied.clear()
    await gates.evaluate(TASK, arbox.GATE)
    assert (applied, [t.key for t in store.all_triggers()]) == ([], []), "a single empty fetch drops nothing"
    assert sorted(store.gate_state(TASK)["classes"]) == ["102"], "...and keeps the known classes"
    await gates.evaluate(TASK, arbox.GATE)
    assert applied == [[]], "a second empty fetch is believed"
    assert any("dropped: " in t.action.instruction for t in store.all_triggers()), \
        "...and wakes Jarvis about the drop"


@pytest.mark.asyncio
async def test_arbox_class_underway_gets_no_wakes(sched, arbox_fake):
    registered, _ = arbox_fake
    store.set_gate_state(TASK, {"classes": {}})
    registered[:] = [cls(104, -0.5)]
    await gates.evaluate(TASK, arbox.GATE)
    assert [t.key for t in store.all_triggers()] == []


@pytest.mark.asyncio
async def test_arbox_unchanged_tick_writes_nothing(sched, arbox_fake):
    registered, _ = arbox_fake
    registered[:] = [cls(105, 30)]
    await gates.evaluate(TASK, arbox.GATE)
    before = os.stat(store.STORE_PATH).st_mtime_ns
    await asyncio.sleep(0.05)
    await gates.evaluate(TASK, arbox.GATE)
    assert os.stat(store.STORE_PATH).st_mtime_ns == before


# --- Ticks and gated tasks -----------------------------------------------------

@pytest.mark.asyncio
async def test_gated_tasks_never_reach_the_model(sched, use_llm, fake_gate, heartbeat_md, monkeypatch):
    heartbeat_md(GATED_AND_PLAIN)
    monkeypatch.setattr(heartbeat_state, "any_due", lambda now: (True, ["gated", "plain"]))
    llm = use_llm(FakeLLM([AIMessage(content="", tool_calls=[tool_call("heartbeat_respond", {
        "acted_tasks": ["plain"], "notify": False, "summary": "s"}, 92)]), AIMessage(content="ok")]))
    await heartbeat.run_heartbeat()
    system = llm.sent[0][0].content
    assert "Plain body." in system and "Gated body." not in system, "only the ungated task reaches the model"
    assert "gated" not in system.replace("ungated", ""), "the gated task isn't even named"
    stamps = heartbeat_state.load_state().get("last_run", {})
    assert sorted(stamps) == ["gated", "plain"], "both tasks stamped"
    assert sorted(store.task_stamps()) == ["gated", "plain"], "stamps live in the trigger store"
    assert "update today's daily log" not in system, "no per-tick daily-log rule in the prompt"

    monkeypatch.setattr(heartbeat_state, "any_due", lambda now: (True, ["gated"]))
    llm = use_llm(FakeLLM([]))
    await heartbeat.run_heartbeat()
    assert llm.sent == [], "a gated-only tick makes no model call"


@pytest.mark.asyncio
async def test_fail_open_tick_keeps_gated_tasks_away(sched, use_llm, fake_gate, heartbeat_md, monkeypatch):
    """The due-gate failing open still keeps gated tasks away from the model."""
    heartbeat_md(GATED_AND_PLAIN)

    def broken(now):
        raise RuntimeError("gate maths broke")

    monkeypatch.setattr(heartbeat_state, "any_due", broken)
    llm = use_llm(FakeLLM([AIMessage(content="", tool_calls=[tool_call("heartbeat_respond", {
        "acted_tasks": [], "notify": False, "summary": "s"}, 93)]), AIMessage(content="ok")]))
    await heartbeat.run_heartbeat()
    system = llm.sent[0][0].content
    assert "Plain body." in system and "Gated body." not in system, "ungated tasks shown, gated not"


@pytest.mark.asyncio
async def test_unknown_gate_falls_back_to_the_model(sched, use_llm, heartbeat_md, monkeypatch):
    """A typo'd gate name runs the task in the model rather than hiding it."""
    heartbeat_md("# Heartbeat Tasks\n\n"
                 "- **typo** | every 1h | gate: arbx | notes: `heartbeat/typo.md`\n  Typo body.\n")
    monkeypatch.setattr(heartbeat_state, "any_due", lambda now: (True, ["typo"]))
    llm = use_llm(FakeLLM([AIMessage(content="", tool_calls=[tool_call("heartbeat_respond", {
        "acted_tasks": ["typo"], "notify": False, "summary": "s"}, 94)]), AIMessage(content="ok")]))
    await heartbeat.run_heartbeat()
    assert bool(llm.sent) and "Typo body." in llm.sent[0][0].content


@pytest.mark.asyncio
async def test_task_linked_wake_sees_its_block(sched, use_llm, fake_gate, heartbeat_md):
    heartbeat_md(GATED_AND_PLAIN)
    llm = use_llm(FakeLLM([ack(""), AIMessage(content="ok")]))
    wake = Trigger("linked01", At(soon(-1)), Turn("Pre-class briefing: Mon 20:00 WOD.", "gated"), ORIGIN_CODE, "gated")
    store.add(wake)
    await heartbeat.run_wake(wake)
    system = llm.sent[0][0].content
    first_input = [m for m in llm.sent[0] if m.type == "human"][-1].content
    assert "Gated body." in system and "Plain body." not in system, "its task's block is shown"
    assert "task gated" in first_input, "the turn is told which task"
