# Heartbeat — Gated Background Ticks

The shared APScheduler (`triggers/scheduler.py`) fires `run_heartbeat()` at the
top of every hour (`main.py`, `CronTrigger(hour="*/1", minute=0)` in UTC). The phase is fixed and survives
restarts by design — see [The gate](#the-gate-heartbeat_stateany_due).
**Code decides *when* the model runs; the model decides *what* to do.** A tick
only becomes an LLM turn when at least one task is due per code-owned state,
and that turn sees only the due tasks. Everything else about a heartbeat turn
(graph, tools, checkpointing) is the ordinary runtime layer under
`scope="heartbeat"` — see [RUNTIME.md](RUNTIME.md).

---

## The tick pipeline

```
APScheduler (top of every hour, UTC)
        │
        ▼
run_heartbeat()                                  heartbeat.py
        │
        ├─ any_due(now)?                         heartbeat_state.py
        │    per task: cadence elapsed AND due-window open
        │    ├─ nothing due ──► log "nothing due", RETURN (no model, no agent import)
        │    └─ gate error  ──► FAIL OPEN: run with the full task list
        ├─ gated due tasks ──► triggers.gates.evaluate in code; stamped on
        │    success, never shown to the model (see TRIGGERS.md "Gates")
        │    └─ none left ──► RETURN (no model)
        ├─ wait for TURN_LOCK (a wake may be running on this thread)
        ▼
ask_jarvis(scope="heartbeat", heartbeat_due_tasks=[…])       agent.py
        │
        ├─ build_system_prompt injects ONLY the due HEARTBEAT.md blocks;
        │  non-due tasks collapse to a one-line note naming them
        ├─ agent works the due tasks (reads/writes its notes files, uses tools)
        ├─ agent calls heartbeat_respond(acted_tasks, notify, summary, …)
        └─ agent replies (terse tick log — never delivered)
        │
        ▼
run_heartbeat() reads the ack (agent.get_heartbeat_ack)
        ├─ no ack AND the turn did not finish (failed / budget_exhausted)
        │  → code-built notice, event="heartbeat", tick_failed=true
        ├─ ack.notify? → default_outbox().notify_owner(notification_text,
        │                event="heartbeat")         send + log-on-success
        └─ stamp(acted_tasks) → last_run (store)    only acted tasks advance,
                                                    and only after delivery
                                                    settled (see below)
```

The ack is authoritative end to end: `acted_tasks` drives state stamping,
`notify`/`notification_text` drive message delivery. The reply text is a
terse tick log only; a tick with no ack delivers nothing and its tasks
re-run next tick (unstamped).

**A broken tick is not silent.** A tick that ends unfinished (`TurnOutcome`,
`turn_budget.py`) *and* left no ack sends the owner a code-built notice —
never model-written, since the model may be what failed — naming the due tasks,
the cause and any tool calls that ran. It is an ordinary heartbeat send
(`event="heartbeat"`, row marked `"tick_failed": true`), so it is logged and
mirrored into the owner thread like any briefing. A finished tick that merely
omitted its ack sends nothing.

**Delivery before stamping.** The send goes through the gateway Outbox, which
returns an outcome instead of raising. Stamps advance only when the tick had
nothing to deliver or the delivery succeeded; a failed send leaves the acted
tasks unstamped, so they come due again next tick and the notification gets
another chance rather than being silently dropped. A `notify=False` tick
stamps normally.

---

## Three files, three owners

| File | Owner | Holds | Read by |
|---|---|---|---|
| `/app/jarvis_memory/HEARTBEAT.md` | the owner (hand-edit) + agent (via `manage_heartbeat_task` only) | Task **definitions**: name, cadence, optional `due:` window, optional `gate:`, prose instruction | gate parser AND prompt injection |
| `/app/jarvis_memory/heartbeat/<task>.md` | agent (free-form via memory tools) | **Notes**: narrative state for reasoning (`target_date`, `last_known_schedule`, …) | agent only — code never parses it |
| `/app/jarvis_data/triggers/triggers.json` | code (`triggers/store.py`, via `heartbeat_state.load_state`/`stamp`) | **Machine state**: `last_run: {"<task>": "<iso8601>"}`, stamped only from the tick ack or a completed gate; also the gates' state and pending triggers ([TRIGGERS.md](TRIGGERS.md)) | code only — outside the memory sandbox, the agent cannot touch it |

---

## Task grammar

```
- **<task-name>** | every <N><unit> [| due: <window>] [| paused] [| gate: <name>] | notes: `heartbeat/<file>.md`
  <free-form prose instruction — the model's brief, never parsed by code>
```

- **Cadence**: `every 1h`, `every 24h`, `every 7d` — also accepted: `hours`/
  `days`/`7 days`. Minimum consideration interval, not an exact schedule.
- **`due:` window** (optional, Israel time): the task is never due outside it.
  Forms: `HH:MM-HH:MM` (range, may wrap midnight) or `HH:MM±Nh` (center ±
  radius; `+-`/`+/-` accepted), each optionally prefixed by weekdays
  (`Tue,Sat 20:30±3h`). Enforced by the gate — if a task reaches the model,
  its window is open.
- **`paused`** (optional): a bare field switching the task off. Matched only as
  a whole field between pipes, so a notes path or window containing the word is
  not the flag. Owner-declared and manual — nothing in the system sets or clears
  it on its own.
- **`gate:`** (optional): the name of a registered code check
  (`triggers/gates.py`). The task then never runs in a tick's model turn: its
  check runs in code when due, and any model work happens in the wakes its
  handler creates, whose turns are shown this task's block — so the prose
  describes those wakes. Set by hand-editing (a gate is code);
  `manage_heartbeat_task` preserves it on every edit. See
  [TRIGGERS.md](TRIGGERS.md), "Gates".
- **`notes:`/`state:` pointer**: both words accepted; names the task's notes
  file.

### Fail directions (deliberate, asymmetric)

| Surface | On bad input | Rationale |
|---|---|---|
| Read side (`parse_tasks`, gate) | **Fail open** — unparseable cadence/window/file → task (or whole tick) treated as due; run the model | A malformed hand edit may cost a model call; it must never silently kill a task |
| Gated task (`gate:`) | **Retry, then tell** — a failing check commits and stamps nothing, so the task is due again next tick; one owner notice after 3 failures in a row. On the fail-open path (`due_names=None`) gated tasks are skipped for that tick | A gated task never runs in the model, so there is nothing to fail open *to*; staying unstamped keeps it retrying and the notice keeps a persistent failure from going silent |
| Write side (`manage_heartbeat_task`) | **Fail loud** — invalid name/cadence/window/duplicate → clear error, file untouched | The agent authors tasks; a silent malformed write would create a task that never fires with nobody knowing |

---

## The gate (`heartbeat_state.any_due`)

A task is due when **not paused AND cadence elapsed AND window open**. `paused`
short-circuits first: no cadence maths, no window check, and the task never
enters `due_names` — so a tick whose only candidates are paused makes no model
call at all. Pausing does not touch the stamps, so a task resumed after a long
pause is immediately cadence-due and runs on the next tick inside its window.
That is intended: resuming is when you want the check to happen.

Otherwise, cadence elapsed
means: never stamped, stamp unreadable, cadence unparseable, or
`now − last_run ≥ cadence` (less `CADENCE_GRACE`). Empty/unreadable
`HEARTBEAT.md` → `(True, None)`: run the model with the *full* file rather than
skip. Any exception in the gate itself → run the model. Every heartbeat-thread
turn (tick or scheduled wake) runs under one `TURN_LOCK`, so a tick that arrives
while a wake runs waits for it rather than being dropped. Stamps advance
**only** for tasks the agent listed in `acted_tasks` — a task the model checked but skipped stays due and
re-fires next tick.

**The tick lattice.** Elapsed time is measured raw first. If that comes up
short, and the task's cadence is longer than one tick, it is measured again with
both ends rounded down to the lattice (`TICK_INTERVAL_HOURS`); either result can
make the task due. The floored pass exists because a stamp that lands *off* the
lattice reads a few minutes short at every remaining tick in that task's window,
which silently costs a daily task its run for the day.

Two constraints on that pass, both load-bearing:

- **It may only ever pull a task earlier, never later.** Flooring can shrink a
  gap as easily as grow it — a `:00` stamp checked at `:59` floors to zero — so
  it is an extra chance to be due, never a replacement for the raw comparison.
- **It does not apply to single-tick cadences.** Flooring discards up to a whole
  tick, so for an `every 1h` task it would call an eight-minute-old stamp an
  hour old and re-run it. Such a task is due from the raw comparison at every
  tick anyway, so it needs no rescue and must not get one.

The trigger is built from `TICK_INTERVAL_HOURS`, so the lattice the gate rounds
to and the lattice the scheduler fires on cannot drift apart; holding the
scheduler's misfire grace to `CADENCE_GRACE` bounds how far off-lattice a *tick*
can stamp. Stamps written by anything else (a hand edit, a state reset) carry
no such bound — which is why the single-tick exclusion is a rule
and not an optimization.

**Known gap: DST.** Windows are evaluated in Israel time while cadences count
absolute hours, so when the offset shifts, a window moves an hour in UTC and a
task pinned to its last in-window tick reads one hour short at every tick of the
shifted window. Flooring does not help (the floored gap is short too). Twice a
year, self-healing the next day. The durable fix is to stop measuring elapsed
time at all and compare window *occurrences* instead — due iff the window is
open and `occurrence_start(now) − occurrence_start(last_run) ≥ cadence`, with
the occurrence starts taken in Israel local days. `DueWindow.is_open` already
computes that start internally.

## Prompt injection (`heartbeat_state.filter_heartbeat_md`)

Only due task blocks are injected; the preamble is kept and omitted tasks are
named in a single line so the model knows they exist and are not due
(`prompts/heartbeat.md` forbids acting on omitted tasks). Gated tasks are left
out of those notes entirely: code runs them, so naming them is only noise. Paused tasks are named
in a *separate* line: "not due yet" invites the model to reason about a next
run, which is wrong for a task the owner switched off. Cold start / gate failure
(`due_names=None`) injects every task except gated ones, paused tasks included
and carrying no note — an accepted, bounded cost of the deliberate fail-open: a
gate that cannot say what is due cannot vouch for what is paused either. Gated
tasks are left out even here, since code (not the model) runs them; they are
simply not evaluated that tick.

## The ack (`heartbeat_respond`)

Bound **only** in heartbeat scope (`scopes=("heartbeat",)` — the first user of
the registry's per-scope binding). The runner extracts the last call's args
from the turn's checkpointed messages (`agent.get_heartbeat_ack`; walks only
past the final HumanMessage, so a stale ack from an earlier tick is never
picked up). Missing ack → warning, no stamp, task re-fires — safe.

## Authoring (`manage_heartbeat_task`)

`create` / `update` / `delete` / `pause` / `resume` / `list` in
`tools/core/heartbeat.py`, bound in both scopes. Validates the mutated file
end-to-end before writing it (the changed task must round-trip through the same
parser the gate uses; all other tasks must survive byte-identical), then writes
via the memory module's atomic, lock-serialized writer. `update` keeps
unspecified fields; `due="none"` clears a window.

`pause` / `resume` take only `name` and run through the same keep-everything
path as `update`, so cadence, window, instruction and a hand-named notes path
all survive. They are the *only* way to move the flag — `update` has no `paused`
argument and preserves whatever the task already was, so an unrelated edit can
never silently switch a task back on. Both are **rejected in heartbeat scope**
(like `create`): a tick that could pause a task could silence itself, which is
the automatic behavior the feature exists to avoid. Re-pausing an already-paused
task is a no-op with a plain message rather than an error.

Changes land immediately — no confirmation step. HEARTBEAT.md is a
Jarvis-managed file, and validation (not an owner tap) is what protects it: a
malformed task is rejected before anything touches disk. The tool returns the
resulting task block so the agent reports what actually landed. This also makes
the autonomous path work: a tick tightening its own due window would otherwise
depend on a confirmation nobody is watching for.

Guards:
- **Heartbeat turns cannot `create`** (update/delete/list only) — a tick must
  not be able to schedule new work for itself; new tasks originate from chat,
  where the owner is in the loop conversationally. The tool learns the
  running scope from `turn_context.CURRENT_SCOPE` (a ContextVar set by
  `ask_jarvis`; never from model-supplied arguments), defaulting to `user`
  outside a turn.
- Raw `write_memory("HEARTBEAT.md", …)` is rejected in code — the guard
  compares the canonical sandbox-relative name, so alias spellings like
  `./HEARTBEAT.md` cannot bypass it. `manage_heartbeat_task` is the agent's
  only write path. Roi's hand edits on disk remain possible; the lenient read
  side is the safety net for those.

## The daily log

`daily/daily_YYYY-MM-DD.md` is written by an ordinary task, `daily-log` in
HEARTBEAT.md (every 3h, 05:00–23:30 Israel time), whose prose folds in today's
chat, today's proactive sends (`get_notification_history`) and the day's
heartbeat activity. It used to be a rule in `prompts/heartbeat.md` that every
tick reaching the model followed, which only kept it fresh while an hourly task
kept waking the model; once crossfit moved behind a gate, that stopped. Chat
after the last run of the day (23:00) is not captured.

## Scheduled wakes (`run_wake`)

A wake is a one-shot trigger whose action is a turn ([TRIGGERS.md](TRIGGERS.md)).
When it fires, `run_wake` runs it on the heartbeat thread, under the same
`TURN_LOCK` as ticks: `ask_jarvis(scope="heartbeat", heartbeat_due_tasks=[],
trigger=...)`, so every task collapses to the not-due note and the
"Scheduled wakes" rules in `prompts/heartbeat.md` apply (work only the
instruction; no task list, no daily log; ack with `acted_tasks=[]`). The ack is
delivered by the same `_deliver` helper as a tick, as a `heartbeat` event whose
metadata names the trigger. Nothing is stamped.

A wake runs at most once. It leaves the store before its turn starts, so a crash
mid-turn loses it instead of re-running it. A turn that breaks without an ack
sends the code-built failure notice (shared with ticks via `_notify_failed`).
Only a failed delivery is retried: the text the turn wrote is stored as a plain
send under the wake's id and goes through the reminder retry path.

---

## Boundaries

- **This doc** owns the tick lifecycle, task grammar, gate semantics, ack and
  authoring contracts.
- [RUNTIME.md](RUNTIME.md) owns the agent loop, scopes, skill activation and
  the registry (including per-scope tool binding).
- [MEMORY.md](MEMORY.md) owns file placement, the sandbox, and per-scope
  prompt composition.
- [OBSERVABILITY.md](OBSERVABILITY.md) owns the telemetry the gate's impact is
  measured with (`turns.jsonl`: per-turn tokens, `no_action`, scope).
