#!/usr/bin/env python3
"""Offline harness for triggers — no model, no channel.

Runs against a scratch JARVIS_ROOT with a fake Outbox and a real scheduler on
a short clock: the legacy migration, the store's row handling, manage_reminder
create/list/delete, firing on time, restoring after a restart, and failed
sends retrying.

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
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import config  # noqa: E402  (must import after JARVIS_ROOT is set)

assert config.DATA_DIR.startswith(_scratch), "scratch root not honored"

from gateway import factory  # noqa: E402
from gateway.outbox import EVENT_REMINDER, SendOutcome  # noqa: E402
from tools.core import manage_reminder  # noqa: E402
from triggers import runner, scheduler, store  # noqa: E402
from triggers.model import At, Send, Trigger  # noqa: E402

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
        self.fail_next = 0

    async def notify_owner(self, text, *, event=None, metadata=None):
        self.sent.append((event, text))
        if self.fail_next:
            self.fail_next -= 1
            return SendOutcome(ok=False, error="offline")
        return SendOutcome(ok=True)


outbox = FakeOutbox()
factory.default_outbox = lambda: outbox


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


def reminder(action, **kw):
    return manage_reminder.invoke({"action": action, **kw})


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

    # --- 3. manage_reminder create / list / delete ---------------------------
    reset_store()
    at = soon(3600).replace(microsecond=0)
    out = reminder("create", text="water the plants", fire_at=at.isoformat())
    check("create: confirms", out.startswith("Reminder ["), True)
    rid = out.split("[", 1)[1].split("]", 1)[0]
    check("create: stored", store.get(rid).action.text if store.get(rid) else None, "water the plants")
    check("create: armed", sched.get_job(f"trigger_{rid}") is not None)
    listed = reminder("list")
    check("list: shows it", f"[{rid}]" in listed and "water the plants" in listed and "in 0h 59m" in listed)
    out = reminder("delete", reminder_id=rid)
    check("delete: confirms", out.startswith(f"Deleted reminder [{rid}]"), True)
    check("delete: removed from store", store.get(rid), None)
    check("delete: disarmed", sched.get_job(f"trigger_{rid}"), None)
    check("list: empty", reminder("list"), "No pending reminders.")
    check("delete: unknown id", reminder("delete", reminder_id="nope").startswith("No reminder found"), True)
    check("create: past instant refused",
          reminder("create", text="x", fire_at="2020-01-01T00:00:00Z"), "Error: fire_at must be in the future.")
    check("create: naive instant refused",
          "no timezone offset" in reminder("create", text="x", fire_at="2030-01-01T09:00:00"))
    check("create: nothing stored by refusals", store.all_triggers(), [])

    # --- 4. Fires on time ----------------------------------------------------
    reset_store()
    outbox.sent.clear()
    out = reminder("create", text="on time", fire_at=soon(1).isoformat())
    rid = out.split("[", 1)[1].split("]", 1)[0]
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
    await asyncio.sleep(2.5)
    check("give up: first try plus three retries", len(outbox.sent), 1 + runner._MAX_RETRIES)
    check("give up: dropped from the store", store.get("dead"), None)
    check("give up: nothing left armed", sched.get_job("trigger_dead"), None)
    outbox.fail_next = 0

    sched.shutdown(wait=False)


asyncio.run(main())
print("\nALL PASS" if not FAILS else f"\n{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
