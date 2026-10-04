# Triggers — The One Scheduler for Timed Work

`triggers/` owns every piece of timed work that isn't the hourly heartbeat tick:
the store, the APScheduler instance, and the code that runs a trigger when it
fires — plus **gates**, the code checks that decide whether a gated heartbeat
task has any work. Two trigger kinds exist, both one-shot: a **reminder** sends
fixed text, and a **wake** runs a background turn. What it grows into (heartbeat
tasks folded in) is [../plans/TRIGGERS_PLAN.md](../plans/TRIGGERS_PLAN.md).

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

`origin` is who created it: `owner` (a chat turn, the owner present),
`jarvis` (a background turn) or `code` (a gate's handler). `parent` is the
trigger whose run created it, `heartbeat` for an hourly tick, or the gated
task's name for `code`; it's absent for chat. A wake's `turn` may name a `task`,
whose HEARTBEAT.md block its turn is shown. `key` is set by code that may later
replace or cancel what it created; at most one stored trigger has a given key.
Rows written before these fields existed read as `owner`.

## Modules

| Module | Owns |
|---|---|
| `model.py` | `Trigger`, `At`, `Send`, `Turn`, origins, and the dict round-trip |
| `store.py` | `DATA_DIR/triggers/triggers.json`: one lock over every read-modify-write, atomic temp+replace writes, the legacy migration |
| `scheduler.py` | The process's one `AsyncIOScheduler` (`init_scheduler`/`get_scheduler`); `arm`/`disarm` a trigger as a `DateTrigger` job (id `trigger_<id>`); `restore_pending()` on startup |
| `runner.py` | `run(trigger, attempt)`: a send directly; a turn via `heartbeat.run_wake`. `retry()` re-arms a failed send |
| `gates.py` | The gate and handler registries (`@gate`, `@gate_handler`), `evaluate()` for one gated task, and the one-write commit of its state and keyed create/cancel |

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
  warning, but a later write keeps it in the file rather than dropping it. A
  file that isn't valid JSON at all is moved aside to `triggers.json.corrupt-<ts>`
  and the store starts empty, so no write can turn it into an empty file.
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
Wakes created by a gate's handler (`code`) are not limited: they're
deterministic and keyed, and a turn they start counts like a chat-created wake's.

## Gates

A HEARTBEAT.md task with `| gate: <name>` never runs in a tick's model turn.
When it's due, `heartbeat._run_gated` calls `gates.evaluate(task, gate)`:

```
check(state) ──► GateResult(fire, state, message, data)        reads only, ≤60s
   ├─ no fire ─► commit state ─► stamp the task                 no model, nothing scheduled
   └─ fire ───► handler(result) ─► [Upsert(key, at, action) | Cancel(key_prefix)]
                commit state + trigger changes (ONE store write) ─► arm/disarm ─► stamp
error anywhere ─► nothing committed or stamped; retried next tick; one owner notice
                  after 3 failures in a row (metadata gate_failed = the task)
```

- **Checks only read; handlers act.** A handler may do deterministic work (a
  DB sync) and returns follow-ups instead of running turns, so it can never
  wait on the heartbeat thread's lock. Model work happens in the wakes it
  creates, which belong to the gated task: their turn sees the task's block.
- **Idempotent by key, atomic by write.** An unchanged `Upsert` is a no-op
  (re-armed, in case an earlier arming failed), a changed one replaces the old
  trigger, `Cancel` removes a key prefix. The gate's new state and all its
  trigger changes land in one store write, and wakes are armed only after it,
  so a crash or failure before that write leaves nothing applied and the next
  tick redoes the same change; after it, the change is already recorded. A
  handler's own work (the DB sync) must be safe to repeat for that reason.
- **State lives in the store** under `gates.<task>`. An unchanged tick writes
  nothing.
- **A wake cancelled after it fired but before it ran** (waiting for the lock,
  or past-due at startup) is skipped: `run_wake` runs only if it can still
  remove the trigger from the store.
- Checks and handlers live with the skill that owns their data and register on
  import, e.g. `tools/fitness/gates.py` (`arbox_registrations`: a briefing wake
  2h before each class and a check-in wake when it ends; any change — booked,
  moved or dropped — also wakes Jarvis at once to discuss the week against
  quota, except on the first run, which only records the starting set. Classes
  already underway are ignored, and an empty fetch while future classes are
  known is believed only when the next tick sees it too — Arbox has been seen
  to return an empty set by mistake).

## Boundaries

- Delivery goes through the Outbox (`gateway/factory.default_outbox()`); this
  package never imports a channel.
- [HEARTBEAT.md](HEARTBEAT.md) owns the heartbeat thread: ticks, wakes' turns,
  the ack, delivery and the failure notice.
- [GATEWAY.md](GATEWAY.md) owns the Outbox and the frozen `event` strings
  (`EVENT_REMINDER`, `EVENT_HEARTBEAT`).
- Tests: `tests/test_triggers.py`.
