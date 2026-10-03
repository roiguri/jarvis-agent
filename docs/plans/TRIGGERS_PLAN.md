# Triggers — Plan

**Date:** 2026-10-03 · **Status:** S1–S4 implemented (2026-10-03); awaiting PR, deploy, and the post-deploy checks
(S3 behavior in prod; the no-op-turn measurement).
**Problems addressed:** [context/PROBLEMS.md](context/PROBLEMS.md) A7, A8, A9 (no-op heartbeat
ticks), plus two capability gaps PROBLEMS.md doesn't list: Jarvis can't wake itself at an exact
time, and reminders and heartbeat tasks are two separate scheduling systems.
**Constraints carried in:** [context/RESEARCH.md](context/RESEARCH.md) §3.1 (four constraints on
any deterministic-wake design), restated in §3.

---

## Slices

Each slice ships and is verified on its own. Design detail is in §5–§7; this list is what gets
ticked.

**S1 — Triggers skeleton, reminders moved in.** A refactor with no behavior change.
- [x] `triggers/model.py`, `triggers/store.py`: `jarvis_data/triggers/triggers.json`, migrated
      from `scheduling/scheduled_events.json` on first start.
- [x] `triggers/scheduler.py`: APScheduler wiring moved out of `heartbeat.py` and `main.py`
      (closes #32). One-shots armed on start from the store.
- [x] `triggers/runner.py`: the `send` action, which is today's `fire_reminder` with its retries.
- [x] `manage_reminder` keeps its interface, now backed by the store.
- [x] `CLAUDE.md` layout and `docs/architecture/` updated for the new top-level package.
- [x] Verify: pending reminders survive the migration and a restart; a staging reminder fires.
      Offline: `scripts/test_triggers.py` (35 checks). Staging 2026-10-03: the legacy file
      migrated on first read; a 2-minute reminder fired on time and was removed.

**S2 — The `turn` action and self-wake.**
- [x] `runner.py`: `turn` action, run by `heartbeat.run_wake` under one `TURN_LOCK` shared with
      ticks. It replaces the 30s guard that dropped a colliding turn.
- [x] `manage_trigger(create | list | cancel)` replaces `manage_reminder`. Origin and parent come
      from `turn_context`, never from model arguments; D2's limits enforced in code.
- [x] `/triggers` slash command (list and cancel), with `check_command_replies.py` cases.
- [x] Prompts (`AGENTS.md`, `heartbeat.md` with a "Scheduled wakes" section) and tool docstrings
      name `manage_trigger`; staging's `HEARTBEAT.md` task bodies updated.
- [ ] **Deploy step:** prod's `HEARTBEAT.md` names `manage_reminder` in the crossfit and
      fitness-scouting task bodies (3 lines). Update them with the deploy.
- [x] Offline: `scripts/test_triggers.py` extended (wake turn and delivery, quiet wake, broken
      wake, delivery retry, every D2 rule, waiting on the lock, the running wake seen in-turn).
- [x] Verify: "check X in 10 minutes" from chat wakes a turn at that time. A triggered turn can
      create one follow-up; that follow-up can't create another. A one-shot landing on the
      hourly tick waits instead of being dropped.
      Staging 2026-10-03: `/triggers` listed reminders; a chat-created wake ran on time and
      delivered a model-written message; a wake scheduled a follow-up from inside its own turn
      (origin `jarvis`, parent = that wake), which ran. Wake turns made no tool calls besides
      the ack.

**S3 — Gates, the `code` action, crossfit.**
- [x] Prerequisite: the fitness read/sync split (constraint 1). `tools/fitness/classes.py`:
      `_fetch_registered()` only reads Arbox; `_apply_registered()` is the DB upsert and purge;
      `_sync_registered_classes()` = apply(fetch), so the existing tools are unchanged. The fitness
      redesign had already shipped its slices 0–2, so nothing collided.
- [x] `triggers/gates.py`: `@gate` / `@gate_handler` registries, `evaluate()`; the gate's state and
      its keyed `Upsert` / `Cancel` changes commit in one store write, after the handler succeeds. A gate error retries next tick; one owner notice after 3 in a row.
      The "code" action is the handler: it returns follow-ups and never runs a turn itself.
- [x] `HEARTBEAT.md` header grammar gains `| gate: <name>` (parser; `manage_heartbeat_task`
      preserves it on every edit). A gated task never runs in the tick's model turn.
- [x] `Turn` gains `task`: a wake's turn is shown that task's block (a gate's wakes belong to
      its task); `prompts/heartbeat.md` says how to follow it.
- [x] `tools/fitness/gates.py`: `arbox_registrations` gate + handler. Briefing wake 2h before
      start (now, if closer); check-in wake at the class's end (start + 60 min); a dropped class
      cancels both. Any change (booked, moved, dropped) also wakes Jarvis at once with the whole
      change, to discuss the week against quota; the first run only records the starting set.
      Keys `arbox:<class>:{brief,checkin}` and `arbox:change:<digest>`.
- [x] Gated tasks are left out of the tick prompt entirely (not even the "not due" note).
- [x] Staging's crossfit task rewritten for its gate and wakes.
- [x] Offline: `scripts/test_triggers.py` (engine on a fake gate; the Arbox gate on a faked
      fetch; a tick whose gated task never reaches the model; a task-linked wake).
- [x] Independent review (2026-10-03), fixes applied: an empty Arbox fetch must repeat before
      it counts as "everything dropped" (and a reply without `data` is an error); a wake cancelled
      while queued for the lock doesn't run; gated tasks are hidden on the fail-open path too; the
      gate's state and trigger changes commit in one write (an unchanged tick writes nothing);
      a corrupt store is moved aside rather than overwritten; classes already underway are
      ignored; a typo'd gate name falls back to the model; lead times computed in UTC.
      Accepted as known limits: gate state outlives a deleted or renamed task; two tasks naming
      one gate would share its keys; a category-only change reads as "moved"; the failure streak
      is in memory (a restart resets it); a misconfigured `plans.binding` surfaces only as a
      generic gate failure.
- [ ] **Deploy step:** prod's crossfit task gets `| gate: arbox_registrations` and the new body
      (staging's copy is the reference).
- [ ] **Deploy step:** the gate's first run sees every booked class as new and schedules its
      wakes, so cancel prod's old-style class-end reminders (e.g. `b07079b9` "How was the WOD?")
      at deploy to avoid a duplicate check-in.
- [ ] Verify in prod after the deploy (owner's choice, 2026-10-03: no staging run for S3), around
      the end of the following week: an unchanged tick makes no model call; each booked class gets
      both keyed wakes; the briefing and check-in wakes say something useful (the part offline
      tests can't cover); a dropped class cancels them and wakes a turn.
- [ ] Measure: no-op heartbeat turns per day, before and after.

**S4 — Heartbeat stamps into the store, daily log as a task.** Rescoped 2026-10-03, after S1–S3:
- [x] `heartbeat/state.json` migrated into the trigger store (`last_run`), with a `.migrated`
      backup; `heartbeat_state.load_state`/`stamp` read and write it there.
- [x] The hourly tick's registration moved from `main.py` to `triggers.scheduler.add_heartbeat`,
      so every scheduled job is set up in one place.
- [x] The daily log becomes its own task: the per-tick rule leaves `prompts/heartbeat.md`; a
      `daily-log` task (`every 3h | due: 05:00-23:30`) folds in today's chat and proactive sends.
      With crossfit gated, the per-tick rule would have left most days without a log. Staging's
      `HEARTBEAT.md` has the task.
- [x] `docs/architecture/HEARTBEAT.md`, `MEMORY.md`, `CLAUDE.md`, `DEVELOPMENT.md` updated.
- [x] Offline: `scripts/test_triggers.py` (stamp migration, the tick registration, no daily-log
      rule in the tick prompt).
- [ ] **Deploy step:** add the `daily-log` task (and its notes file) to prod's `HEARTBEAT.md`;
      staging's copy is the reference.
- [ ] **After the prod deploy, once both migrations have run** (prod has
      `scheduling/scheduled_events.json.migrated` and `heartbeat/state.json.migrated`, and
      `triggers.json` holds the reminders and `last_run` stamps): delete the migration code —
      `store._migrate_legacy`, `store._migrate_stamps`, `LEGACY_PATH`, `LEGACY_STAMPS_PATH`, their
      calls in `store._read`, and their harness sections in `scripts/test_triggers.py` — plus the
      migration notes in `docs/architecture/TRIGGERS.md`, `HEARTBEAT.md` and `DEVELOPMENT.md`.
      Staging must have migrated too (it already has, for reminders). Then remove the `.migrated`
      backups on both instances.
- **Dropped:** moving the due-gate maths out of `heartbeat_state.py` into `triggers/`. The tick
  already runs on the one shared scheduler; the maths (lattice, misfire handling, #114's DST
  note) is prod-hardened, and moving it would be a rewrite with no change in behavior.
- **Decided against, for now:** a once-a-day log. Kept at a 3h cadence so the user scope's
  "today's log" stays about as fresh as before; the cadence is a task setting, tunable without
  code. A day-close pass (or a code-written log) is a separate question.

---

## 1. Problem

**No-op ticks.** The heartbeat gate decides whether a task *may* run (cadence, window, paused). It
can't tell whether anything *happened*, so that falls to a full model turn. Over 09-30..10-02,
`crossfit-sync-and-remind` (hourly, 05:00–22:00) produced 43 of 45 ticks ending `NO_ACTION` at
~62k input each. Each of those ticks also appended "no new classes" to its notes file, rewrote
today's daily log, and added 6–11 messages to the heartbeat thread, where the no-op pattern then
dominates what the model imitates (PROBLEMS.md F2).

**No exact-time wake.** A tick can only run on the hourly lattice, and a task can't ask to be run
at a specific moment. "Brief me 2h before class", "send the check-in form when class ends", or
"check on that download in 3 hours" have no clean home. `manage_reminder` hits an exact time but
only sends fixed text: it can't run tools, write a briefing, or send a form.

**Two schedulers.** Reminders live in `jarvis_data/scheduling/scheduled_events.json`, are armed
as APScheduler `DateTrigger` jobs, and fire through `heartbeat.fire_reminder`. Heartbeat tasks
live in `HEARTBEAT.md`, are stamped in `jarvis_data/heartbeat/state.json`, and run through
`heartbeat.run_heartbeat` on an hourly cron. Each has its own store, persistence and failure
handling. Any new timed behavior would add a third.

---

## 2. How the reference systems do it

Read from fresh shallow clones on 2026-10-03: OpenClaw `e2dd931a`, hermes-agent `2b52acc2`. Paths
are relative to each repo. Earlier context-focused deep dives are in
[context/reference/](context/reference/).

**Both converged on one scheduler for all timed work.** A job is *when* + an optional cheap *gate*
+ a *payload*.

### OpenClaw — "Automations" (`docs/automation/`)

- **When** (`cron-jobs/schedules.md`): `at` (one-shot), `every`, `cron` with `--tz`, plus event
  sources (`on-exit` of a watched command, `stream` of a supervised command's output lines).
- **Gate:** a "condition watcher" script returns `{fire, message?, state?}`. Two details matter
  most:
  - The returned `state` is **not persisted if the fired payload fails**, so the next evaluation
    sees the old state and fires again. Their guidance: "write scripts as read-only checks and
    keep actions in the payload".
  - Quiet evaluations (`fire: false`) create no run history and no model call.
- **Payload** (`cron-jobs/payloads.md`): a system event (queued into the main session, no model),
  an agent turn, a shell command, or a headless script.
- **The heartbeat is a system-owned job on the same scheduler** (`automation/index.md`). In
  v2026.8.1 the heartbeat's structured `tasks:` block was **removed** and each task migrated into
  an ordinary job (`schedules.md`, "Heartbeat task migration"). The equivalent of our
  `HEARTBEAT.md` tasks is now plain jobs.
- **Inferred commitments were removed** in the same release (`automation/index.md`). That feature
  had the system extract follow-ups from conversation automatically and deliver them via heartbeat.
  Explicit scheduling replaced it.
- **Self-pacing:** a running job may call `next_check(in: "30m")`, clamped to bounds the job
  declares (`pacing.min`/`pacing.max`). It applies only to that job, and failed runs discard the
  proposal. `/loop` is built on it.
- **Loop guard:** creating or editing jobs requires a fresh, authenticated owner turn
  (`cron-jobs/managing-jobs.md`). A scheduled run may only re-pace or remove its own job.
- **Creation flow** (`cron-jobs/how-it-works.md`):
  1. The agent restates the schedule in plain words for confirmation.
  2. It runs the job once immediately as a visible test.
  3. The job is created **enabled**. A job left disabled while awaiting approval "is invisible to
     every guard".
  4. A job that keeps failing auto-disables, and the owner is notified.
- **Busy handling:** scheduled turns *defer* while other work is active; due jobs in one window
  coalesce into one turn.

### hermes-agent — cron (`cron/AGENTS.md`)

- **When:** durations, "every" phrases, cron expressions, and ISO one-shots. Agents schedule
  through a `cronjob` tool.
- **Two cheap gates:**
  - **Monitor jobs** (`cron/monitor.py`) hash a source's exact output (script or URL). Unchanged:
    the agent run is suppressed. Changed: a capped diff plus the new output is injected into the
    prompt. A source failure is an alert, "never a change", and leaves the stored hash untouched.
  - **Pre-run scripts** (`cron/scheduler.py`, the wake-gate) return `wakeAgent: false` to skip the
    model, or have their output injected. `no_agent` makes the script the entire job.
- **Loop guard:** the `cronjob` tool is denied inside cron runs by default, behind the config flag
  `cron.allow_agent_scheduling` (`tests/cron/test_agent_scheduling_gate.py`).
- **No silent drops:** every recurring occurrence is accounted for. `next_run_at` advances before
  dispatch, and an interrupted run restores its slot once.

### What carries over

1. One scheduler, one store, with the heartbeat as a client of it rather than a second scheduler.
2. A gate is a cheap read. Its state commits only after the action succeeds.
3. Explicit, listable, cancellable jobs. No inferred ones.
4. Scheduled runs get narrower scheduling power than the owner does.
5. Busy means *defer*, never *drop*.

---

## 3. Constraints (from RESEARCH.md §3.1 and the current gate)

1. **A gate must be a pure read.** `fetch_upcoming_arbox_classes` upserts the workouts DB and
   purges dropped classes, and its "Removed N classes" notice is one-shot. A gate that called it
   would consume the very change the woken model needs. A read/sync split comes first.
2. **Trigger state is code-owned** (`jarvis_data/`), never a notes file (PROBLEMS.md D1).
3. **Commit only after the action succeeds and delivery settles.** Otherwise a turn that fails
   after the gate saw a change loses that change for good.
4. **"Something changed" and "a time arrived" are different triggers.** A briefing 2h before class
   fires with the schedule unchanged.
5. **Fail open on read.** A gate that errors wakes the model, the same direction as today's
   due-gate. Worst case is today's behavior.
6. **Gates do network I/O.** Each needs its own timeout, and one slow gate must not delay other
   triggers.

---

## 4. Principles

1. **Code decides *when* the model runs; the model decides *what* to do.** Today's heartbeat rule,
   extended from "may it run" to "does it need to".
2. **One scheduler for all timed work.** Reminders, heartbeat tasks and self-wakes are all
   triggers, with one store, one place that arms timers, and one execution path per action kind.
3. **The cheapest action that does the job:** fixed text < code < a model turn. The model is for
   judgment and writing.
4. **Gates read; actions act; state commits after success.**
5. **Explicit and visible.** Every pending trigger is listable and cancellable. Nothing inferred.
6. **Bounded self-scheduling.** The owner can create anything; code can create deterministic
   one-shots; a triggered turn gets a narrow, bounded allowance (§7, D2).
7. **Defer, never drop.** Turns go through one queue. Today's 30s min-spacing guard *drops* a
   turn, which a trigger system can't afford.
8. **Existing fail directions stay:** fail open on read, fail loud on write.

---

## 5. The model

```
trigger = WHEN              + GATE (optional)              + ACTION
          at <instant>        registered code check:         send <text>          no model
          every <N> [due]       read-only; returns            code <handler>       no model; may emit
                                fire? + message + state                            follow-up actions
                                                             turn <instruction>   model, heartbeat-style:
                                                                                  tools, forms, ack
```

- **`send`** is today's reminder: fixed text through the Outbox, with retries.
- **`code`** runs a registered Python handler. It's how deterministic work (a DB sync, scheduling
  or cancelling one-shots) happens without a model while gates stay pure. A handler returns
  follow-ups: zero or more `send`/`turn` actions to run now, and one-shot triggers to create or
  cancel.
- **`turn`** runs a heartbeat-scope agent turn with an instruction (and optionally a linked task,
  whose notes come along). The ack, delivery and stamping rules are today's heartbeat rules.
- **State** (last run, gate state) lives in the trigger store and commits only after the action
  (and its delivery) succeeds.
- **A gate or handler's message reaches the woken turn verbatim.** What it says is that trigger's
  choice: a one-line reason, the facts of a change, or the data it fetched. The one general rule
  is correctness: **if code consumed a change, its message must carry it.** After a handler has
  synced, the model's own fetch sees nothing new (constraint 1), so the message is the only place
  the change still exists.
- **Origin** (`owner`, `jarvis`, `code`, `system`) and **parent** (the trigger whose run created
  it) are recorded on every trigger. They drive the self-scheduling rules (D2), and they're what
  `/triggers` shows to explain why a trigger exists.

### Crossfit, end to end

```
every 1h, due 05:00–22:00
  gate arbox_registrations (pure read: fetch registrations, diff against gate state)
   ├─ unchanged ───────────────► stop. No model, no notes edit, no daily-log rewrite
   └─ changed → code apply_arbox_change (sync the DB; then per class:)
         ├─ new class 2026-10-06 20:00
         │     create  at 18:00 → turn "pre-class briefing"           key: arbox:<id>:brief
         │     create  at 21:05 → turn "end-of-class check-in (form)"  key: arbox:<id>:checkin
         └─ dropped class
               cancel  arbox:<id>:*
               run now → turn "class dropped: tell owner, check quota, offer alternatives"
  gate state commits after apply_arbox_change (and any turn it started) succeed
```

One-shots carry a deterministic key, so a re-run after a failure updates instead of duplicating.

### Self-wake from chat

"Check on that download in 3 hours" → `manage_trigger(create, at=+3h, turn="check the download
and tell the owner")`, origin `jarvis`, parent = none (a user turn, owner present).

---

## 6. Code architecture

A new top-level package beside `gateway/`. It never imports a channel; delivery goes through the
existing Outbox (`gateway/factory.default_outbox()`).

```
triggers/
  model.py      Trigger(id, key, when, gate, action, origin, parent, created_at)
                When:   At(instant) | Every(cadence, due_window)
                Action: Send(text) | Code(handler, args) | Turn(instruction, task=None)
  store.py      jarvis_data/triggers/triggers.json — one code-owned file: every trigger,
                last_run stamps, gate state. Absorbs scheduling/scheduled_events.json and
                heartbeat/state.json. Atomic, lock-serialized, validated on write.
  gates.py      @gate("arbox_registrations") → fn(state) -> GateResult(fire, message, state)
  handlers.py   @handler("apply_arbox_change") → fn(args) -> list[FollowUp]
                (registries, like tools: a new gate or handler is a code deploy)
  scheduler.py  APScheduler wiring, moved out of heartbeat.py (this closes #32).
                One-shots armed exactly (DateTrigger); recurring triggers evaluated on the
                hourly lattice (today's due-gate maths moves here from heartbeat_state.py).
                Reloads the store on restart; overdue one-shots run once on startup.
  runner.py     Executes actions. Send → outbox.notify_owner (today's fire_reminder, with retries).
                Code → handler → follow-ups. Turn → ONE serialized queue → ask_jarvis(
                scope="heartbeat") → ack → deliver → commit. Waits, never drops.
tools/core/scheduling.py
                manage_trigger(create | list | cancel). Replaces manage_reminder rather than
                adding a tool (same per-turn schema cost). Enforces D2 from turn_context
                (scope, and whether this turn was itself triggered), never from model arguments.
gateway/commands/
                /triggers: list and cancel, no model.
```

What existing modules become:
- **`heartbeat.py`**: the `turn` action's heartbeat specifics (tick prompt, ack extraction,
  delivery, failure notice). The scheduler and `fire_reminder` move out.
- **`heartbeat_state.py`**: the `HEARTBEAT.md` parser stays (if D1 keeps the file); the gate
  maths moves to `triggers/scheduler.py`.
- **`tools/core/scheduling.py`**: folded into `manage_trigger`.
- **Fitness skill**: the read/sync split (constraint 1). This overlaps
  [FITNESS_LOGGING_PLAN.md](FITNESS_LOGGING_PLAN.md) and should be sequenced with it.

### The daily log

Today the daily log isn't a task. It's a rule in `prompts/heartbeat.md` ("after the task work,
update today's daily log") attached to every tick, which is why A7 rewrites it hourly. Once most
ticks stop waking the model, nothing keeps it current. The fix is to move that rule out of the
per-tick prompt into an ordinary recurring task in `HEARTBEAT.md`, whose schedule is set like any
other task's. A reasonable start is once in the evening: during the day the user scope already
has the real conversation in its thread, mirrored sends included, and the log mainly feeds the
next day's heartbeat as "yesterday's log". Adjust if the user scope visibly misses it.

---

## 7. Decisions

### D1 — Where are recurring heartbeat tasks authored?

**(a) Keep `HEARTBEAT.md` as the authoring surface; triggers own all timing and state.** The
triggers module reads the file as the source of recurring `turn` triggers.
- Pros:
  - No migration of a file the owner already edits by hand.
  - Markdown prose stays readable and hand-editable.
  - `manage_heartbeat_task` and its validation, pause/resume and scope guards stay as they are.
  - The due-tasks prompt injection is unchanged, so the model's behavior is unchanged.
- Cons:
  - Two authoring surfaces: the file for recurring tasks, `manage_trigger` for everything else.
  - The store keys state on task names it doesn't own. A rename orphans the stamp, which is
    already true today.

**(b) Tasks become ordinary triggers in the store** (OpenClaw's move).
- Pros:
  - One model, one tool, one list.
  - A task's gate and action are first-class fields.
- Cons:
  - A large migration.
  - Prose in a JSON store is worse to hand-edit.
  - `manage_heartbeat_task`, its docs and the prompt injection all have to be rebuilt.
  - The file stops being visible through the memory tools.

**Decided (2026-10-03): (a).** It gets every benefit that matters here (one scheduler, one store,
gates on tasks) without rebuilding a working authoring path. The header grammar gains an optional
`| gate: <name>` field. `/triggers` lists both sources together, so the split isn't visible when
reading what's scheduled. (b) stays possible later, and nothing in (a) blocks it.

### D2 — How much may Jarvis schedule for itself?

**(i) Nothing from triggered turns** (both reference systems' default). The owner, in chat, and
code handlers create triggers; a triggered turn can't.
- Pros: no loop is possible; the simplest rule.
- Cons: a triggered turn can't follow up on its own findings ("the download is still at 40%,
  check again in an hour"). The owner has to ask, or a code handler has to anticipate it.

**(ii) A narrow allowance.** A triggered turn may create **one-shot `turn` triggers only**, with
these limits:
- one level deep: a trigger created by a triggered turn can't create another;
- a cap on pending Jarvis-created triggers (e.g. 10).

- Pros:
  - Covers real follow-ups.
  - Loops are structurally impossible (depth 1, one-shots only).
  - Everything stays visible in `/triggers` with its origin and parent.
- Cons:
  - More rules to implement and explain.
  - The limits are judgment calls.

**(iii) Unrestricted.**
- Pros: maximum flexibility.
- Cons: the failure both reference systems guard against, and the reason ticks can't create
  heartbeat tasks today.

**Decided (2026-10-03): (ii), with the predictable cases in code.** The crossfit briefing and
check-in come from `apply_arbox_change`, deterministic and keyed, so they never depend on the model
remembering. The allowance is for genuine follow-ups only. Chat turns (owner present) create
one-shots without limits; recurring work stays with `manage_heartbeat_task` (D1). Only wakes are
limited: a reminder runs no model, so it can't schedule anything. A 48h horizon was drafted and
dropped at implementation (2026-10-03): depth and the pending cap already bound it.

---

## 8. Not decided here

- Whether gates need a timeout budget per trigger or one shared budget per tick.
- Event-driven `when` kinds (webhooks, OpenClaw's command streams). The model leaves room for
  them; nothing here needs them.
- An auto-disable policy for triggers that keep failing (OpenClaw does this). Probably wanted;
  sized after slice 2.
