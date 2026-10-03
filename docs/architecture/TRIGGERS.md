# Triggers — The One Scheduler for Timed Work

`triggers/` owns every piece of timed work that isn't the hourly heartbeat tick:
the store, the APScheduler instance, and the code that runs a trigger when it
fires. Two kinds exist today, both one-shot: a **reminder** sends fixed text, and
a **wake** runs a background turn. The design it grows into (gates, code
actions, heartbeat tasks folded in) is
[../plans/TRIGGERS_PLAN.md](../plans/TRIGGERS_PLAN.md).

---

## Shape

```
trigger = WHEN          + ACTION
          at <instant>    send <text>           reminder: no model
                          turn <instruction>    wake: a heartbeat-scope turn
```

`triggers/model.py` holds the record. On disk, `when` and `action` are
single-key tagged objects, so a new kind is a new tag rather than a migration:

```json
{"version": 1, "triggers": [
  {"id": "274c0427", "when": {"at": "2026-10-03T13:40:00+00:00"},
   "action": {"send": {"text": "Stretch"}}, "origin": "owner"},
  {"id": "9f2c11ab", "when": {"at": "2026-10-03T18:00:00+00:00"},
   "action": {"turn": {"instruction": "Check the download"}},
   "origin": "jarvis", "parent": "heartbeat"}
]}
```

`origin` is who created it: `owner` (a chat turn, the owner present) or
`jarvis` (a background turn). `parent` is the trigger whose run created it, or
`heartbeat` for an hourly tick; it's absent for chat. Rows written before these
fields existed read as `owner`.

## Modules

| Module | Owns |
|---|---|
| `model.py` | `Trigger`, `At`, `Send`, `Turn`, origins, and the dict round-trip |
| `store.py` | `DATA_DIR/triggers/triggers.json`: one lock over every read-modify-write, atomic temp+replace writes, the legacy migration |
| `scheduler.py` | The process's one `AsyncIOScheduler` (`init_scheduler`/`get_scheduler`); `arm`/`disarm` a trigger as a `DateTrigger` job (id `trigger_<id>`); `restore_pending()` on startup |
| `runner.py` | `run(trigger, attempt)`: a send directly; a turn via `heartbeat.run_wake`. `retry()` re-arms a failed send |

The heartbeat's hourly `CronTrigger` is registered on the same scheduler by
`main.py`. Ways in:
- **`manage_trigger`** (`tools/core/scheduling.py`), the agent's tool: create
  (`at` + exactly one of `message` / `instruction`), list, cancel.
- **`/triggers`**, the owner's slash command: list, and `cancel <id>`. No model.

## Lifecycle

```
manage_trigger(create) ── store.add ── scheduler.arm ──► DateTrigger job fires
                                                              │
                     ┌────────────────────────────────────────┴──────────────┐
                 send (reminder)                                       turn (wake)
                     │                                                       │
 late by >60s? prefix "[Originally scheduled for …]"        heartbeat.run_wake, under TURN_LOCK:
 outbox.notify_owner(event="reminder")                       store.remove, then a heartbeat-scope turn
   ├─ ok   → store.remove                                     ├─ no ack + broken → failure notice
   └─ fail → re-arm in 5 min; past 3 retries → remove         └─ ack.notify → deliver (event="heartbeat")
                                                                   └─ fail → stored as a send, retried
```

- **The store is the durable record.** APScheduler's job store is in-memory;
  `restore_pending()` re-arms every stored trigger at startup. One whose instant
  passed while the process was down runs once, immediately. `main.py` skips the
  restore when `JARVIS_REMINDERS_ENABLED` is off, so staging never re-fires the
  owner's real reminders.
- **A reminder is removed only after delivery.** A failed send stays stored, so
  a restart mid-retry still delivers it. The retry count isn't stored, so a
  restart resets it.
- **A wake runs at most once.** It's removed before its turn starts, and a
  broken one is reported rather than re-run. Only a failed delivery is retried,
  as a plain send of the text the turn wrote. Details: [HEARTBEAT.md](HEARTBEAT.md),
  "Scheduled wakes".
- **Rows are parsed on read.** A row the code can't read is skipped with a
  warning, but a later write keeps it in the file rather than dropping it.
- **Migration.** An instance that still has the pre-triggers
  `DATA_DIR/scheduling/scheduled_events.json` converts its reminders on the first
  store read and renames the old file to `scheduled_events.json.migrated`.

## Self-scheduling limits

Only triggers that run the model can create more triggers, so only **wakes**
are limited; reminders never are. `manage_trigger` reads the creator from
`turn_context` (scope and `CURRENT_TRIGGER`), never from model arguments:

| Created in | Origin | Wakes allowed |
|---|---|---|
| A chat turn | `owner` | Yes, no limit |
| An hourly tick | `jarvis`, parent `heartbeat` | Yes, while fewer than 10 `jarvis` wakes are pending |
| A wake created in chat | `jarvis`, parent = that wake | Yes, same cap |
| A wake created by a background turn | — | No: one level deep, so no loop is possible |

There is no time-horizon limit: depth and the pending cap are what bound it.

## Boundaries

- Delivery goes through the Outbox (`gateway/factory.default_outbox()`); this
  package never imports a channel.
- [HEARTBEAT.md](HEARTBEAT.md) owns the heartbeat thread: ticks, wakes' turns,
  the ack, delivery and the failure notice.
- [GATEWAY.md](GATEWAY.md) owns the Outbox and the frozen `event` strings
  (`EVENT_REMINDER`, `EVENT_HEARTBEAT`).
- Offline harness: `scripts/test_triggers.py`.
