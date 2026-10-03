# Triggers — The One Scheduler for Timed Work

`triggers/` owns every piece of timed work that isn't the hourly heartbeat tick:
the store, the APScheduler instance, and the code that runs a trigger when it
fires. Today the only kind is a reminder: a one-shot instant that sends fixed
text. The design it grows into (gates, model turns, heartbeat tasks folded in)
is [../plans/TRIGGERS_PLAN.md](../plans/TRIGGERS_PLAN.md).

---

## Shape

```
trigger = WHEN          + ACTION
          at <instant>    send <text>     (no model)
```

`triggers/model.py` holds the record. On disk, `when` and `action` are
single-key tagged objects, so a new kind is a new tag rather than a migration:

```json
{"version": 1, "triggers": [
  {"id": "274c0427", "when": {"at": "2026-10-03T13:40:00+00:00"},
   "action": {"send": {"text": "Stretch"}}}
]}
```

## Modules

| Module | Owns |
|---|---|
| `model.py` | `Trigger`, `At`, `Send`, and the dict round-trip |
| `store.py` | `DATA_DIR/triggers/triggers.json`: one lock over every read-modify-write, atomic temp+replace writes, the legacy migration |
| `scheduler.py` | The process's one `AsyncIOScheduler` (`init_scheduler`/`get_scheduler`); `arm`/`disarm` a trigger as a `DateTrigger` job (id `trigger_<id>`); `restore_pending()` on startup |
| `runner.py` | `run(trigger, attempt)`: what happens when a trigger fires |

The heartbeat's hourly `CronTrigger` is registered on the same scheduler by
`main.py`. `manage_reminder` (`tools/core/scheduling.py`) is the agent's path
in: create adds and arms, delete removes and disarms, list reads the store.

## Lifecycle

```
manage_reminder(create) ── store.add ── scheduler.arm ──► DateTrigger job
                                                              │ fires
                                                              ▼
runner.run ── late by >60s? prefix "[Originally scheduled for HH:MM Israel time]"
           ── outbox.notify_owner(text, event="reminder")
              ├─ ok    → store.remove
              └─ fail  → re-arm in 5 min (attempt+1); past 3 retries → store.remove
```

- **The store is the durable record.** APScheduler's job store is in-memory;
  `restore_pending()` re-arms every stored trigger at startup. A trigger whose
  instant passed while the process was down runs once, immediately, with the
  late-send prefix. `main.py` skips the restore when `JARVIS_REMINDERS_ENABLED`
  is off, so staging never re-fires the owner's real reminders.
- **Removed only after delivery.** A failed send leaves the trigger stored, so a
  restart mid-retry still delivers it. The retry count is not stored, so a
  restart resets it.
- **Rows are parsed on read.** A row the code can't read is skipped with a
  warning, but a later write keeps it in the file rather than dropping it.
- **Migration.** An instance that still has the pre-triggers
  `DATA_DIR/scheduling/scheduled_events.json` converts its reminders on the first
  store read and renames the old file to `scheduled_events.json.migrated`.
  With no store and no legacy file, the store starts empty.

## Boundaries

- Delivery goes through the Outbox (`gateway/factory.default_outbox()`); this
  package never imports a channel.
- [HEARTBEAT.md](HEARTBEAT.md) owns the tick itself: gate, prompt, ack, stamps.
- [GATEWAY.md](GATEWAY.md) owns the Outbox and the frozen `event` strings
  (`EVENT_REMINDER`).
- Offline harness: `scripts/test_triggers.py`.
