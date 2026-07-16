# Heartbeat — Gated Background Ticks

APScheduler fires `run_heartbeat()` hourly (`main.py`, `IntervalTrigger(hours=1)`).
**Code decides *when* the model runs; the model decides *what* to do.** A tick
only becomes an LLM turn when at least one task is due per code-owned state,
and that turn sees only the due tasks. Everything else about a heartbeat turn
(graph, tools, checkpointing) is the ordinary runtime layer under
`scope="heartbeat"` — see [RUNTIME.md](RUNTIME.md).

---

## The tick pipeline

```
APScheduler (hourly)
        │
        ▼
run_heartbeat()                                  heartbeat.py
        │
        ├─ any_due(now)?                         heartbeat_state.py
        │    per task: cadence elapsed AND due-window open
        │    ├─ nothing due ──► log "nothing due", RETURN (no model, no agent import)
        │    └─ gate error  ──► FAIL OPEN: run with the full task list
        ├─ last tick started <30s ago? ──► defer
        ▼
ask_jarvis(scope="heartbeat", heartbeat_due_tasks=[…])       agent.py
        │
        ├─ build_system_prompt injects ONLY the due HEARTBEAT.md blocks;
        │  non-due tasks collapse to a one-line note naming them
        ├─ agent works the due tasks (reads/writes its notes files, uses tools)
        ├─ agent calls heartbeat_respond(acted_tasks, notify, summary, …)
        └─ agent replies ([NO_ACTION] if nothing was done)
        │
        ▼
run_heartbeat() reads the ack (agent.get_heartbeat_ack)
        ├─ ack.notify? → default_outbox().notify_owner(notification_text,
        │                event="heartbeat")         send + log-on-success
        └─ stamp(acted_tasks) → state.json          only acted tasks advance,
                                                    and only after delivery
                                                    settled (see below)
```

The ack is authoritative end to end: `acted_tasks` drives state stamping,
`notify`/`notification_text` drive message delivery. The reply text (the
`[NO_ACTION]` contract in `prompts/heartbeat.md`) survives only as the
fallback delivery path when the ack is missing — slated for removal once logs
show it never fires.

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
| `/app/jarvis_memory/HEARTBEAT.md` | Roi (hand-edit) + agent (via `manage_heartbeat_task` only) | Task **definitions**: name, cadence, optional `due:` window, prose instruction | gate parser AND prompt injection |
| `/app/jarvis_memory/heartbeat/<task>.md` | agent (free-form via memory tools) | **Notes**: narrative state for reasoning (`target_date`, `last_known_schedule`, …) | agent only — code never parses it |
| `/app/jarvis_data/heartbeat/state.json` | code (`heartbeat_state.py`) | **Machine state**: `{"last_run": {"<task>": "<iso8601>"}}`, stamped only from the tick ack | code only — outside the memory sandbox, the agent cannot touch it |

Transitional note: the agent currently still writes a `last_run:` line into its
notes files in parallel with `state.json`; the markdown copy is retired once
the two have demonstrably agreed in production (`state.json` is already the
only input to the gate).

---

## Task grammar

```
- **<task-name>** | every <N><unit> [| due: <window>] | notes: `heartbeat/<file>.md`
  <free-form prose instruction — the model's brief, never parsed by code>
```

- **Cadence**: `every 1h`, `every 24h`, `every 7d` — also accepted: `hours`/
  `days`/`7 days`. Minimum consideration interval, not an exact schedule.
- **`due:` window** (optional, Israel time): the task is never due outside it.
  Forms: `HH:MM-HH:MM` (range, may wrap midnight) or `HH:MM±Nh` (center ±
  radius; `+-`/`+/-` accepted), each optionally prefixed by weekdays
  (`Tue,Sat 20:30±3h`). Enforced by the gate — if a task reaches the model,
  its window is open.
- **`notes:`/`state:` pointer**: both words accepted; names the task's notes
  file.

### Fail directions (deliberate, asymmetric)

| Surface | On bad input | Rationale |
|---|---|---|
| Read side (`parse_tasks`, gate) | **Fail open** — unparseable cadence/window/file → task (or whole tick) treated as due; run the model | A malformed hand edit may cost a model call; it must never silently kill a task |
| Write side (`manage_heartbeat_task`) | **Fail loud** — invalid name/cadence/window/duplicate → clear error, file untouched | The agent authors tasks; a silent malformed write would create a task that never fires with nobody knowing |

---

## The gate (`heartbeat_state.any_due`)

A task is due when **cadence elapsed AND window open**, where cadence elapsed
means: never stamped, stamp unreadable, cadence unparseable, or
`now − last_run ≥ cadence`. Empty/unreadable `HEARTBEAT.md` → `(True, None)`:
run the model with the *full* file rather than skip. Any exception in the gate
itself → run the model. A 30s min-spacing guard protects against back-to-back
ticks. Stamps advance **only** for tasks the agent listed in `acted_tasks` —
a task the model checked but skipped stays due and re-fires next tick.

## Prompt injection (`heartbeat_state.filter_heartbeat_md`)

Only due task blocks are injected; the preamble is kept and omitted tasks are
named in a single line so the model knows they exist and are not due
(`prompts/heartbeat.md` forbids acting on omitted tasks). Cold start / gate
failure (`due_names=None`) injects the full file.

## The ack (`heartbeat_respond`)

Bound **only** in heartbeat scope (`scopes=("heartbeat",)` — the first user of
the registry's per-scope binding). The runner extracts the last call's args
from the turn's checkpointed messages (`agent.get_heartbeat_ack`; walks only
past the final HumanMessage, so a stale ack from an earlier tick is never
picked up). Missing ack → warning, no stamp, task re-fires — safe.

## Authoring (`manage_heartbeat_task`)

`create` / `update` / `delete` / `list` in `tools/core/heartbeat.py`, bound in
both scopes. Validates the mutated file end-to-end before writing it (the
changed task must round-trip through the same parser the gate uses; all other
tasks must survive byte-identical), then writes via the memory module's atomic,
lock-serialized writer. `update` keeps unspecified fields; `due="none"` clears
a window.

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
