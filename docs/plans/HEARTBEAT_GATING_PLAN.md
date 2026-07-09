# Heartbeat Gating, Structured Tasks & Self-Authoring

**Issue:** #33 (archive) = #18 (origin mirror) umbrella / Lever #1.
**Branch:** `feat/heartbeat-gating`.
**Companion:** [CONTEXT_HANDLING_PLAN.md](CONTEXT_HANDLING_PLAN.md) — the full context-handling roadmap this plan is Workstream 1 of.
**Goal:** make Jarvis manage its own heartbeat state well, and stop paying for an LLM turn every hour to do nothing. Two outcomes:

1. **Cost:** the heartbeat should only run the model when something is actually due, and when it runs it should only carry the tasks that are due — not all 8 every time.
2. **Capability (Roi's priority):** Jarvis should author and maintain its own task plan from chat — *"talk to me after my gym session"* should become a real, well-formed heartbeat task — without silently producing broken tasks.

**Non-goals (deferred elsewhere):** Gemini context caching (#33 Lever 4 → companion plan WS2), thread compaction (#54 → companion plan WS7), on-demand/event-driven heartbeat (#20), exact-time self-scheduling (OpenClaw's *cron* model — see §2; we chose the windowed-poll model instead), heartbeat-scope prompt slimming (`lightContext` analog — §7 → companion plan WS3).

---

## Manager summary

**Problem.** Jarvis's heartbeat runs a full LLM turn every hour, 24/7, whether or not anything is due. Measured over the 14 days to 2026-07-09 (observability telemetry): the heartbeat accounts for **84% of total LLM spend**; **88% of its ticks end in `[NO_ACTION]`**, and each of those no-op ticks still burns ~66k input tokens across ~3.7 LLM calls (it reads task state files via tools before concluding there is nothing to do). Separately, Roi wants Jarvis to author its own recurring tasks from chat ("check in after my gym sessions") without silently producing broken task definitions.

**What ships.** Two deliverables across 9 small phases (7 and 9 are evidence-gated on a production parallel-run window):

1. **A deterministic gate** (code, not LLM) that decides *before* any model call whether a task is due — first by cadence, then by machine-enforced time/day windows — and, when the model does run, injects only the due tasks into its prompt instead of all 8.
2. **A validated task-authoring tool** (`manage_heartbeat_task`) so Jarvis can create/update/delete its own heartbeat tasks from conversation — every change validated before write and confirmed by Roi with a tap.

**Expected impact** (phases build on each other; needle moves at 5, 6 and 8):

| Phases | What lands | Impact |
|---|---|---|
| 1–3 | Infrastructure (tool scoping, structured tick-ack, code-owned state file) | No behavior change — de-risks everything after |
| 4 | Cadence gate | Enables 5–6; skips little by itself (two tasks are hourly) |
| 5 | Only-due-task prompt injection | Input tokens cut on **every** remaining tick (8 task blocks → 1–2) |
| 6 | Time/day windows | Most hours have genuinely nothing due → **LLM call skipped entirely** |
| 7 | Retire duplicated state from markdown | Hygiene |
| 8 | Self-authoring tool + prompts | Capability (Roi's priority); compounds 6 — Jarvis tightens its own wake windows |
| 9 | Ack-primary delivery, reply-text fallback | Correctness — removes the ack/reply disagreement windows |

**Risk posture.** Each phase is independently shippable and revertable; Phases 1–3 change no behavior. The gate **fails open** everywhere — a parsing bug or crash runs the LLM rather than silently killing the heartbeat. Machine state moves to a code-owned file the agent never touches; the agent's narrative notes files are unchanged. Every agent-authored task change requires a confirmation tap.

**Validation.** The design was verified against OpenClaw's source (its heartbeat gating works this way in production) and cross-checked against Nous Research's `hermes-agent` (§2b); we deliberately add the write-validation both of them lack, because in Jarvis the *agent* authors the task file and the cost gate depends on it parsing.

---

## 0. Honest framing: where the savings actually come from

**Measured baseline (observability telemetry, 14 days to 2026-07-09):** 27.1M input tokens / $1.72 total across both scopes. Heartbeat scope: 335 turns, 22.8M input ($1.44 — **84% of all spend**), 296 of 335 ticks (88%) ended `[NO_ACTION]`. A no-op tick averages **66.3k input tokens and 3.7 LLM calls**; an acting tick 79.5k and 4.3. Reproduce with `observability.usage.summarize_usage(group_by="scope")`. This is the spend this plan attacks.

Current tasks (`/app/jarvis_memory/HEARTBEAT.md`):

| Task | Cadence | Time/day constraint (today: prose only) |
|---|---|---|
| morning-readiness-check | 24h | ~09:00 |
| **crossfit-sync-and-remind** | **1h** | none (polls Arbox) |
| weekly-fitness-scouting | 24h | Thu ~20:00 |
| running-evening-prep | 24h | Mon/Fri ~20:00 |
| **running-post-check** | **1h** | Tue/Sat after 20:30 |
| weekly-attendance-sync | 24h | Sat evening |
| haircut-reminder | 24h | — |
| memory-index-audit | 7d (168h) | — |

A deterministic gate can skip the LLM only when **no task is due**. Because two tasks are `every 1h`, *something is due every hour* — so a **cadence-only gate skips almost nothing** with this mix. This is the key correction to any naive "the gate kills ~70% of ticks" claim: it doesn't, on its own.

The savings come from two distinct levers; being precise about which does what matters:

1. **Prompt-shrink — inject only the due tasks (Phase 5).** Even when the LLM still runs (for the 1h tasks), it no longer needs all 8 task blocks + 8 notes files in its prompt; most hours it sees 1–2. Saves *input tokens on every tick*, independent of call count. **The always-on win.**
2. **Time/day windows — close the 1h tasks when irrelevant (Phase 6).** `crossfit-sync` needn't poll at 03:00; `running-post-check` only matters Tue/Sat after 20:30. Once those windows are machine-enforced, most hours have *genuinely nothing due* and the gate finally skips the LLM. **The call-count win, and given the 1h tasks it is essential, not optional.**

So the cadence gate (Phase 4) is necessary *infrastructure* that produces the due-list, but on its own it mostly enables Phases 5 and 6 rather than cutting calls. The savings table in §4 reflects this honestly.

> Future idea worth flagging to Roi: `crossfit-sync` polling Arbox hourly via a full LLM turn is itself expensive. A later issue could move that poll to a deterministic tool that escalates to the LLM only when the schedule *changed*. Out of scope here (§7).

---

## 1. Target architecture (the end state, before any implementation)

### 1.1 Three files, three owners, three concerns — none git-tracked

Today `heartbeat/<task>.md` conflates two different things: a machine timestamp (`last_run`) and free-form reasoning notes. We split them so each piece has exactly one owner. **All three live in unversioned runtime directories** (`/app/jarvis_memory/` and `/app/jarvis_data/` are not git repositories — verified). Roi's actual task plan is never committed; only the *machinery* (tool, gate, grammar rules under `/app/jarvis_code/`) is.

```
/app/jarvis_memory/HEARTBEAT.md          ← DEFINITIONS: what tasks exist, cadence, window
                                            Owner: Roi (hand-edit) + agent (via manage_heartbeat_task)
                                            Read by: code (gate parser) AND agent (prompt injection)
                                            Git: NO (runtime memory)

/app/jarvis_memory/heartbeat/<task>.md    ← NOTES: narrative/semantic state for reasoning
                                            Owner: agent (read_memory / write_memory, free-form)
                                            Read by: agent only. Code never parses it.
                                            Holds: target_date, last_known_schedule, prose notes…
                                            Does NOT hold: last_run (moved out)
                                            Git: NO

/app/jarvis_data/heartbeat/state.json     ← MACHINE STATE: gating timestamps
                                            Owner: code (heartbeat.py). Agent NEVER touches it.
                                            Read by: code only.
                                            Holds: { "last_run": { "<task>": "<iso8601>", … } }
                                            Git: NO (tool-opaque state, per CLAUDE.md placement rule)
```

**Why this split (OpenClaw-derived).** OpenClaw keeps task *definitions* in a freeform `HEARTBEAT.md` the agent can edit, but keeps *runtime state* (`heartbeatTaskState`) entirely inside the framework where the model can't see or corrupt it, stamped only on a completed run. We mirror that: definitions stay editable; the gating timestamp becomes code-owned. We go *beyond* OpenClaw by keeping a per-task **notes** file — OpenClaw has no equivalent, but our tasks carry real semantic state (`running_prep.md`'s `target_date`, `crossfit_check.md`'s `last_known_schedule`) the agent needs across ticks. That stays agent-owned; we just stop co-mingling the machine timestamp into it.

This answers "what replaces `heartbeat/*.md`?" — **nothing; they lose one line.** The `last_run:` line moves to `state.json`; everything else stays.

### 1.2 HEARTBEAT.md task grammar (the contract between gate and authoring tool)

One documented grammar, parsed leniently. The task header line:

```
- **<task-name>** | every <N><unit> [| due: <window>] | notes: heartbeat/<task>.md
  <free-form prose body the agent reads to know what to do>
```

- `<unit>` ∈ `h` (hours) | `d` (days). `every 7d` ≡ `every 168h`.
- `due: <window>` is **optional**, parsed only from Phase 6. Until then, time constraints live as prose in the body (§1.4).
- `notes:` replaces today's `state:` pointer (renamed — the file no longer holds state). The parser tolerates either word during transition.
- **Fail-open parsing:** any line the gate can't read → that task is treated as *always due* (degrades to "let the LLM decide," never silently dropped). A malformed `HEARTBEAT.md` must degrade to "run the LLM," never to "skip everything."

### 1.3 The main flow (one hourly tick, end state)

```
APScheduler fires (hourly)
        │
        ▼
heartbeat.py: run_heartbeat()
        │
        ├─ 1. parse HEARTBEAT.md  ───────────────►  [task, cadence, window?] × N
        │
        ├─ 2. load state.json     ───────────────►  { last_run: {task: iso} }
        │
        ├─ 3. compute due tasks:
        │        for each task:
        │          cadence elapsed?  (now - last_run >= cadence)        ── Phase 4
        │          AND window open?  (Phase 6; always-true before)      ── Phase 6
        │          (unparseable task → treated as DUE — fail open)      ── Phase 4
        │
        ├─ 4. NO task due?  ──────────► log "nothing due", RETURN.  ❌ no LLM   ── Phase 4
        │
        ├─ 5. min-spacing guard: last tick < 30s ago? ─► defer          ── Phase 4
        │
        ▼ (≥1 task due)
   ask_jarvis(scope="heartbeat", due_tasks=[…])
        │
        ├─ build_system_prompt injects ONLY due task blocks            ── Phase 5
        │   from HEARTBEAT.md + (agent reads their notes files)
        │
        ├─ agent reasons:
        │     read_memory("heartbeat/<task>.md")  ← narrative state (unchanged)
        │     …acts via skill tools…
        │     write_memory("heartbeat/<task>.md") ← updates notes  (NOT last_run)
        │     heartbeat_respond(acted_tasks=[…], notify=bool, …)        ── Phase 2
        │
        ▼
   heartbeat.py reads heartbeat_respond result
        │
        ├─ 6. stamp state.json[last_run][t] = now  for t in acted_tasks ── Phase 3
        │     (only acted tasks advance — OpenClaw "stamp on success")
        │
        └─ 7. notify=True?  ─► send_to_owner(notification_text) + log notification
```

Two things to notice, because they define the agent's relationship to the files:

- **The agent still reads/writes `heartbeat/<task>.md` exactly as today** for narrative state. That flow is *preserved* — we are not changing how the agent reasons; we only remove `last_run:` and let code own it.
- **The agent no longer decides "is it due" by reading a timestamp.** Code does that before the LLM is invoked. The agent is asked only about tasks code already found due, and reports back only *what it acted on*.

### 1.4 How time-of-day / day-of-week is handled

- **Before Phase 6:** time/day constraints exist **only as prose** in the task body ("Run every Thursday evening ~20:00"). The gate knows only cadence. So a 24h task becomes cadence-due ~00:01 each day, the LLM runs, reads the prose, and may conclude "not the right time → nothing to do." A *wasted but bounded* tick (≤1/day per such task) — acceptable interim. `prompts/heartbeat.md` must keep instructing the agent to honor the prose.
- **From Phase 6:** the constraint is lifted into a machine-readable `due:` field and the gate enforces it deterministically — no LLM turn to discover "wrong time of day." Prose then trims to just *what to do*.

### 1.5 Task add / remove / lifecycle

All programmatic edits go through `manage_heartbeat_task` (§1.6), not raw file writes. Roi may still hand-edit `HEARTBEAT.md` directly; the fail-open parser tolerates a slightly-off hand edit.

| Action | Mechanism | State effect |
|---|---|---|
| Add a task | `manage_heartbeat_task(action="create", …)` (confirmation) | No `state.json` entry → **due on next tick**, fires once to initialize, then settles into cadence. |
| Remove a task | `manage_heartbeat_task(action="delete", name=…)` | Its `state.json[last_run]` entry is **orphaned but harmless** — the gate iterates only tasks present in `HEARTBEAT.md`. `memory-index-audit` can prune orphans. |
| Change cadence/window | `manage_heartbeat_task(action="update", …)` | Next tick reparses; new cadence/window takes effect immediately. |
| Rename | delete + create | Old key orphaned, new key absent → fires once. Acceptable. |

`HEARTBEAT.md` is on the protected list (write requires a confirmation tap), so changes are deliberate.

### 1.6 Task authoring — how Jarvis edits its own plan (the "talk to me after the gym" flow)

**Does OpenClaw do this?** Yes, but only via *unvalidated freeform file writes*. Its docs: *"Can the agent update HEARTBEAT.md? Yes — if you ask it to."* The agent uses its ordinary file-write tool inside a chat turn; there is **no structured API and no validation** (verified — §2). The framework itself never writes task content. A malformed agent edit silently drops (the parser ignores unparseable tasks) with no signal to anyone.

**Why Jarvis must diverge.** OpenClaw's assumption is *a human curates the checklist and notices when a task stops firing*. For Jarvis the **agent authors the tasks from chat** — no human reviewing the raw markdown — and our **cost gate depends on every line parsing**. A silent malformed drop means "Roi asked Jarvis to check in after the gym, Jarvis wrote a broken line, nothing ever fires, nobody knows." Jarvis already solved the analogous problem for one-shot reminders with a *structured* `manage_reminder` tool instead of letting the agent hand-edit `scheduled_events.json`. We mirror that.

**The tool — `manage_heartbeat_task`** (parallels `manage_reminder`):

```python
manage_heartbeat_task(
    action: str,            # "create" | "update" | "delete" | "list"
    name: str = "",         # kebab task name, e.g. "post-class-checkin"
    cadence: str = "",      # "1h" | "24h" | "7d"
    due: str = "",          # optional window (Phase 6): "Tue,Sat 20:30±1h"
    instruction: str = "",  # the prose body: WHAT to do when due
) -> str
```

- **Validate-on-write (fail loud).** Parses the current tasks, mutates the one task, **re-serializes the task section deterministically** (preserving human prose/notes around it), and validates before writing: reject bad cadence, bad `due:` window, duplicate name — with a clear error, leaving `HEARTBEAT.md` untouched. The agent never blind-rewrites the whole file. This is the validation OpenClaw omits.
- **Read-side parser stays fail-open.** A slightly-off *hand* edit by Roi surfaces to the model as always-due (so it can act/repair), never a silent drop. Loud on write, forgiving on read.
- `create`/`update`/`delete` are `destructive=True` against protected `HEARTBEAT.md` → each triggers a **confirmation tap**, so Roi approves every plan change. `list` is read-only, no confirmation.
- Registered `namespace="core"`, **no scope restriction** — available in user scope (Roi authors from chat) and heartbeat scope (a tick can self-maintain, e.g. retire a one-off task it just completed).

**The prompting that makes it reliable** (Roi's "handle the heartbeat state correctly" concern). Rules in `prompts/AGENTS.md` (always-on, so present in *user* scope where authoring happens):

- **Recognize the intent.** An ongoing/conditional proactive wish → a heartbeat task ("check in after my workouts", "nudge me if I skip a run", "every Sunday summarize my week").
- **Disambiguate from a one-shot reminder.** A single absolute-time ping ("remind me at 3pm to call the dentist") → `manage_reminder`. Rule to encode: **recurring / conditional / state-dependent → `manage_heartbeat_task`; single fixed moment → `manage_reminder`.**
- **Author, then confirm.** Translate the wish into `(name, cadence, due?, instruction)`, call the tool, let the confirmation surface it to Roi before it lands.

**Worked example — "talk to me after the gym":**

1. Roi (chat): *"After my CrossFit classes, check in on how it went."*
2. Agent (user scope) recognizes a recurring/conditional wish:
   ```
   manage_heartbeat_task(
     action="create", name="post-class-checkin", cadence="1h",
     instruction="If Roi had a CrossFit class today that ended in the last ~hour "
                 "(check today's class end time in heartbeat/crossfit_check.md / Arbox), "
                 "ask how it went — energy, soreness, any PRs.")
   ```
3. Confirmation tap → validated block written to `HEARTBEAT.md`.
4. From then on each tick: the gate sees it (`1h`), injects only that block, the agent checks whether a class just ended, and either acts (`heartbeat_respond(acted_tasks=["post-class-checkin"], notify=True, …)`) or does nothing.

**The dynamic-time subtlety (honest).** Class times vary, so v1 uses `cadence: 1h` + an LLM check each tick — bounded but not free. Once Phase 6 windows exist, the agent does better: when `crossfit-sync` learns the booked class time, it `manage_heartbeat_task(action="update", …, due="<day> <class-end>±1h")` so the gate skips every hour outside the window. That is the concrete payoff of self-authoring: **Jarvis tightens its own wake windows as it learns Roi's schedule** — no self-scheduling machinery, just narrower deterministic windows the gate already enforces.

### 1.7 Freeform vs structured mode — and why Jarvis is always structured

OpenClaw's parser supports both, and they can coexist in one file:

| File contains | When the LLM runs | What enforces timing |
|---|---|---|
| Only freeform prose | **Every poll tick** (file non-empty + active hours) | Nothing per-item — fixed cadence + the LLM reading prose |
| Only a structured task block | Only when ≥1 task is due | Code, per task cadence |
| Both | Only when ≥1 *structured* task is due; freeform prose rides along as context **only on those runs** | Code gates the run; prose is passenger |

Jarvis's current `HEARTBEAT.md` is the worst of both: it *looks* structured (`every 1h`, `state:`), but nothing parses it, so today it behaves like **freeform mode** — the hourly poll fires unconditionally and the LLM judges everything by reading prose. That is the cost problem.

This plan commits Jarvis to **structured-only**: every task line is parseable, so the gate enforces timing in code; `manage_heartbeat_task` guarantees the agent writes parseable lines; fail-open parsing is the safety valve (an unparseable line degrades to freeform-for-that-one-task, never a silent drop).

---

## 2. Reference: what we verified in OpenClaw source

Checked against `openclaw/openclaw` source — parser, runner, tests, and the doctor command — not just docs:

| Concern | OpenClaw (source) | Our decision |
|---|---|---|
| Two modes | Freeform checklist **and** a structured `tasks:` block (`- name: / interval: / prompt:`), parseable together (`parseHeartbeatTasks`, `src/auto-reply/heartbeat.ts`). | Use structured-only (§1.7). |
| Per-task cadence | `isTaskDue(lastRun, interval, now)` = `now - lastRun >= interval`; never-run → always due. | Direct adopt as our gate (Phase 4). |
| **No validation** | The parser **silently drops** any task missing `name`/`interval`/`prompt` (`if (name && interval && prompt)`); `isTaskDue` swallows a bad interval (`catch { return false }` → never due). The runner adds no warnings; the test suite has a single parse test (field-bleed only); `doctor` only swaps stale *boilerplate templates* and **refuses to touch custom content** — it is not a task validator. | **Add what OpenClaw lacks:** validate-on-write in `manage_heartbeat_task` (fail loud), fail-open on read. Justified because the *agent* authors and the *gate* depends on parseability (§1.6). |
| Who writes the file | Framework **never** writes task content. Humans hand-edit; the **agent** writes via its generic file tool when asked ("Yes — if you ask it to"). No structured task API. | Keep `HEARTBEAT.md` as the editable source, but route agent writes through the structured tool. |
| Runtime state | Per-task `last_run` in `heartbeatTaskState` (session state), advanced **only on a completed run** (skipped `no-tasks-due`/`empty-heartbeat-file` ticks don't stamp). Never in the task file. | Move `last_run` → code-owned `state.json`, stamped only for `acted_tasks`. |
| Only-due injection | *"Only due tasks are included in the heartbeat prompt… If no tasks are due, the heartbeat is skipped entirely (`reason=no-tasks-due`)."* Non-task prose appended after the due list. | Adopt: Phase 4 (skip) + Phase 5 (inject only due). |
| Agent ack | Typed tool `heartbeat_respond({outcome, notify, summary, notificationText?, priority?, nextCheck?})`; runtime keys off the payload, not text. `nextCheck` is **advisory only** — verified it appears in the tool def + test but **never in the scheduler** (`heartbeat-schedule.ts` computes next wake purely from the fixed interval). | Add our own `heartbeat_respond`; `acted_tasks` drives stamping. Omit `nextCheck` (no self-scheduling in the windowed-poll model). `[NO_ACTION]` text → fallback only. |
| Pre-LLM gates | `shouldDeferWake`: `not-due`, `min-spacing` (30s), `flood` (≥5/60s); plus `empty-heartbeat-file`, `activeHours`, visibility skips. | Adopt `not-due` (per-task) + `min-spacing`. `flood` deferred until #20. |
| Heartbeat vs cron | Heartbeat = fixed-interval *poll*. **Cron** is the separate exact-time scheduler (`--at`/`--every`/`--cron`) that wakes the agent at a precise time. Exact-time scheduling is cron's job, not heartbeat's. | Roi chose the windowed-poll model; exact-time self-scheduling (cron analog) is out of scope (§7). |
| Cost levers | `isolatedSession` (~100k→2–5k tokens/run), `lightContext` (inject only HEARTBEAT.md), cheaper `model`, `target: none`. | Note for §7 — a trimmed heartbeat-scope prompt is a cheap future win that compounds with Phases 4–6. |

**Bottom line from the source dive:** OpenClaw has *no* structured/validated task writer and *no* task validation anywhere — the model is its safety net. Jarvis can't rely on that (agent authors, gate depends on parseability), so our divergence — a validated tool + fail-open parser — is adding the guardrails OpenClaw deliberately omits, not reimplementing them.

### 2b. Cross-check: Nous Research `hermes-agent` (added 2026-07-09)

A second reference reviewed after this plan was drafted — [github.com/nousresearch/hermes-agent](https://github.com/nousresearch/hermes-agent), a personal-assistant framework with the same shape as Jarvis (SOUL.md, messaging gateway, cron scheduler, skills). It independently validates the design and contributes one guard we adopt:

- **Isolated scheduled sessions** — Hermes runs every cron job in an isolated agent session, never the main chat thread ([cron docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/cron)). Consistent with our separate `heartbeat` thread and OpenClaw's `isolatedSession`.
- **Scheduling tools disabled inside scheduled runs** — Hermes hard-disables its cron-management tools during cron executions, specifically to prevent runaway self-scheduling loops. Phase 8 exposes `manage_heartbeat_task` in both scopes so a tick can retire a one-off task it just completed — keep that, but **reject `action="create"` when the active scope is `heartbeat`**: a tick may update/delete/list, never spawn new tasks. The failure mode this closes is a tick that keeps scheduling more work for itself with no human in the loop (heartbeat-scope confirmations have no one to tap them). Folded into Phase 8.

---

## 3. Implementation phases

Each phase is one or more commits on `feat/heartbeat-gating`, independently testable and revertable. Phases 1–3 add no behavior change (infrastructure + parallel writes) so regressions are easy to isolate. Phase 8 (self-authoring — Roi's priority) is independent and can be pulled forward to right after Phase 3.

### Phase 0 — Branch (done)

`git checkout -b feat/heartbeat-gating`. This plan doc lands here as the first commit (pending Roi's approval).

### Phase 1 — Tool scoping infrastructure ONLY (no new tools)

**Why split out:** the scope-filtering change touches `registry.py`, which every tool flows through. Land it alone to prove **zero regression** before adding any tool that depends on it.

**Implementation:**
1. `tools/registry.py`: add `scopes: tuple[str, ...] | None = None` to `RegisteredTool`. Extend `_visible(entry, scope, active_skills)` so when `entry.scopes is not None`, the tool binds only if `scope in entry.scopes`. Thread `scope` from `get_tools`/`find` into `_visible`.
2. `tool_register(..., scopes=None)` — new optional kwarg, default `None` (any scope). No existing tool passes it → identical binding.

**Verification (regression — must show NO change):**
- Script: for `scope="user"` and `scope="heartbeat"` with a fixed `active_skills`, `{t.name for t in get_tools(scope, skills)}` is identical to the pre-change set. Diff empty.
- Restart; normal Telegram message → normal reply. Observe one heartbeat tick → unchanged.

**Rollback:** revert the commit.

### Phase 2 — Add the `heartbeat_respond` tool

**Why now:** with scoping proven safe, introduce the agent's structured ack — the signal every later phase keys off. **OpenClaw analog:** `heartbeat-response-tool.ts`.

**Implementation:**
1. `tools/core/heartbeat.py` (heartbeat-management module — Phase 8's `manage_heartbeat_task` lands here too), `@tool_register(namespace="core", scopes=("heartbeat",))`:
   ```python
   def heartbeat_respond(
       acted_tasks: list[str],      # task names (from HEARTBEAT.md) acted on this tick
       notify: bool,                # should Roi see a message this tick?
       summary: str,                # one-line internal log, always recorded
       notification_text: str = "", # used only when notify=True; defaults to summary
   ) -> dict: ...
   ```
   Trivial body returning the recorded payload; real handling is in `heartbeat.py` (Phase 3).
2. `tools/core/__init__.py`: import it.
3. `prompts/heartbeat.md`: mandatory closing step — *"Always end the tick by calling `heartbeat_respond` exactly once. List every task you acted on in `acted_tasks` (exact names from HEARTBEAT.md). Set `notify=true` only if Roi needs to see something; put the user-facing message in `notification_text`."*
4. `heartbeat.py`: after the turn, find the `heartbeat_respond` tool call in the final state; **log it only** (observe one iteration).

**Verification:**
- `heartbeat_respond` in `get_tools("heartbeat", …)` but **not** `get_tools("user", …)`.
- One tick → one logged call with parseable `acted_tasks`; `tool_calls.jsonl` shows it on heartbeat turns, absent on user turns.

**Rollback:** revert; no data migration.

### Phase 3 — Write `state.json` from the tool result (parallel to existing markdown)

**Why:** author machine state ourselves from the trusted signal, in parallel with the existing markdown `last_run:` for one iteration so we can diff before depending on it.

**Implementation:**
1. New `heartbeat_state.py`:
   - `load_state()` — read `/app/jarvis_data/heartbeat/state.json` (missing → `{"last_run": {}}`).
   - `stamp(task_names, when)` — atomic write (tmp + `os.replace`), update only named keys; validate names against parsed tasks; unknown → log + skip (never crash).
   - `parse_tasks()` — lenient HEARTBEAT.md parser (name, cadence; `due:` ignored until Phase 6). Cache by mtime. **Shared by the gate and `manage_heartbeat_task`.**
2. `heartbeat.py`: after a tick, if `heartbeat_respond` was found, `stamp(result["acted_tasks"], now)`. If absent → log warning, **do not stamp** (task re-fires next tick — safe).
3. Leave the agent still updating markdown `last_run:` this phase (don't change the prompt rule yet) so both exist to diff.

**Verification:**
- Tick that acts → `state.json` has a fresh entry; diff vs `heartbeat/<task>.md` `last_run:` agrees within seconds.
- `[NO_ACTION]`/`acted_tasks=[]` tick → `state.json` mtime unchanged.
- Agent omits `heartbeat_respond` → warning logged, `state.json` unchanged, runner doesn't crash.
- 24h run → ~5–8 entries, not 24.

**Rollback:** revert; optionally `rm state.json`.

### Phase 4 — Gate the LLM on cadence + min-spacing (the infrastructure gate)

**Why:** with truth-stamped state, code answers "anything cadence-due?" without an LLM round-trip. (Per §0, few calls skipped *yet* with the 1h tasks — but this produces the due-list Phases 5/6 need.) **OpenClaw analog:** `shouldDeferWake` → `not-due` + `min-spacing`.

**Implementation:**
1. `heartbeat_state.any_due(now) -> (bool, list[str])`: per task, due iff `last_run` missing or `now - last_run >= cadence`; **unparseable task → due (fail open)**. Return `(any, due_names)`.
2. `run_heartbeat`:
   ```python
   try:
       due, due_names = heartbeat_state.any_due(now)
   except Exception:
       log.exception("gate failed — running LLM (fail open)"); due, due_names = True, None
   if not due:
       log.info("Heartbeat: nothing due — skipping LLM"); return
   if now - last_tick_start < timedelta(seconds=30):
       log.info("Heartbeat: min-spacing — defer"); return
   # … existing ask_jarvis call …
   ```
3. **Fail open everywhere:** any gate exception → run the LLM. A gate bug must never silently kill the heartbeat.

**Verification:**
- Force "nothing due" (pre-seed `state.json` fresh) → `"nothing due — skipping LLM"` log, no heartbeat turn in `turns.jsonl`.
- Restore real cadences → 1h tasks make it run (not over-eager).
- Corrupt/move `state.json` → fail-open log, LLM runs. Restore.

**Rollback:** revert the gate guard; `state.json` keeps being written.

### Phase 5 — Inject only the due tasks into the heartbeat prompt (always-on token win)

**Why:** works even while the LLM still runs hourly — shrinks the prompt from 8 task blocks to the 1–2 due. **OpenClaw analog:** *"Only due tasks are included in the heartbeat prompt."*

**Implementation:**
1. `ask_jarvis(..., heartbeat_due_tasks: list[str] | None = None)` → thread to `build_system_prompt`.
2. `build_system_prompt("heartbeat", active_skills, due_tasks=None)`: when a list, inject only matching task blocks (split on `- **name**`). `None` → inject all.
3. `run_heartbeat` passes `due_names` from Phase 4.

**Verification:**
- One-due tick → that turn's system prompt contains only that block. Cold start (all due) → all 8. Token count drops on the HEARTBEAT.md section.

**Rollback:** revert; `due_tasks=None` restores old behavior.

### Phase 6 — Per-task time/day windows (the call-count win)

**Why (per §0):** the 1h tasks keep the LLM running hourly until their windows are machine-enforced. This is where call-count reduction materializes.

**Implementation:**
1. Extend the grammar with optional `due:`:
   - `due: HH:MM±Nh` (daily window); `due: <Day>[,<Day>…] HH:MM±Nh` (specific days, Israel tz).
   - Examples:
     ```
     - **crossfit-sync-and-remind** | every 1h | due: 06:00-22:00 | notes: heartbeat/crossfit_check.md
     - **running-post-check** | every 1h | due: Tue,Sat 20:30±3h | notes: heartbeat/running_sync.md
     ```
2. `any_due` also requires the window open at `now`. Tasks without `due:` keep cadence-only behavior (migrate one, verify, then the rest).
3. Trim redundant "when" prose from migrated bodies; note in `prompts/heartbeat.md` that windowed tasks are code-gated. `manage_heartbeat_task` validates `due:` strings.

**Verification:**
- `running-post-check` with `due: Tue,Sat 20:30±3h`: 14:00 Tue → window-closed, gate skips; 21:00 Tue → runs; 21:00 Wed → day excluded, skips.
- 24h after windowing both 1h tasks → real drop in heartbeat turns.

**Rollback:** remove `due:` annotations; cadence-only resumes.

### Phase 7 — Retire the `last_run:` line from notes files (hygiene)

**Why last:** keep `last_run:` in markdown through Phases 3–6 as a live cross-check. Once `state.json` has matched it ≥1 week, the markdown copy is redundant.

**Implementation:**
1. `prompts/heartbeat.md`/notes conventions: drop the instruction to write `last_run:` (agent keeps writing all other narrative state).
2. Rename pointer `state:` → `notes:` in `HEARTBEAT.md` (parser tolerates both from Phase 3).
3. Clear stale `last_run:` lines from existing `heartbeat/*.md`.

**Verification:**
- One-week audit: `state.json` vs markdown agree → safe. After retiring: next tick doesn't re-add `last_run:`.

**Rollback:** restore the prompt instruction; files untouched.

### Phase 8 — `manage_heartbeat_task` tool + authoring prompts (Roi's priority)

**Why a separate phase, and when:** independent of the gating phases — needs only the grammar (§1.2) and the shared parser (`heartbeat_state.parse_tasks`, Phase 3). Can land right after Phase 3, ahead of the cost phases, since it's the capability Roi most cares about. Full design in §1.6.

**OpenClaw analog:** *"Yes — if you ask it to"* — but via a validated tool, not raw rewrite, because OpenClaw has no validation and our agent authors the tasks (§2).

**Implementation:**
1. `manage_heartbeat_task(action, name, cadence, due, instruction)` (§1.6) in `tools/core/heartbeat.py` (shared heartbeat-management module from Phase 2). `namespace="core"`, no scope restriction, `destructive=True`.
   - `create`/`update`/`delete`: parse → mutate → **re-serialize the task section deterministically** (preserve surrounding prose), validate (cadence, `due:`, duplicate name) before writing; reject with a clear error on failure, leaving the file untouched. Never a blind whole-file overwrite.
   - Reuse `heartbeat_state.parse_tasks` for read/validate.
   - `create`/`update`/`delete` → `get_confirmation().request_confirmation_sync(...)` (protected file). `list` is read-only.
   - **Loop guard (§2b, from Hermes):** reject `action="create"` when the active scope is `heartbeat` — a tick may `update`/`delete`/`list` its existing tasks but never create new ones. Return a clear error telling the agent to propose the task to Roi in chat instead.
2. `prompts/AGENTS.md`: authoring rules (§1.6) — recognize recurring/conditional wishes as heartbeat tasks; disambiguate from one-shot `manage_reminder`; author via the tool, let confirmation surface it.
3. `tools/core/__init__.py`: import.

**Verification:**
- Chat *"check in after my CrossFit classes"* → `manage_heartbeat_task(create…)` → confirmation card → on confirm, canonical block in `HEARTBEAT.md`, `parse_tasks` returns it.
- Chat *"remind me at 3pm…"* → uses `manage_reminder`, not a heartbeat task (disambiguation works).
- `action="list"` → current tasks, read-only.
- Bad cadence (`"weekly"`) → tool rejects with a clear error, `HEARTBEAT.md` unchanged.
- Delete → block removed; orphaned `state.json` entry harmless.
- New task is due next tick (no state entry), fires once, settles.

**Rollback:** revert; remove tool + AGENTS.md rules. Existing tasks untouched.

### Phase 9 — Ack-primary delivery, reply-text fallback (added 2026-07-10; evidence-gated like Phase 7)

**Why.** Phases 2–8 leave the tick with two authoritative outputs: the reply text drives delivery (`[NO_ACTION]` contract), the ack drives stamping. They can disagree — the bad window is `notify=true` in the ack + `[NO_ACTION]` in the reply, a silently lost briefing; the reverse sends noise. The channels aren't redundant (`acted_tasks=[x]` + no message is a legal silent-maintenance tick), so the fix is a clear hierarchy, not deduplication. **OpenClaw analog:** its `heartbeat_respond` payload drives everything; the text token survives as fallback only.

**Gate to start:** the parallel-run window (same as Phase 7) shows ack omission ≲1 in 20 ticks. If the model drops the ack more often, strengthen the mechanism first (prompt wording, or a corrective follow-up turn when the runner detects a missing ack) — migrating delivery onto an unreliable channel converts "no stamp" failures into "lost briefing" failures.

**Implementation:**
1. `heartbeat.py` delivery branch:
   - ack present → deliver iff `notify`, using `notification_text`; reply text ignored for delivery.
   - ack missing → fall back to today's behavior (send reply unless `[NO_ACTION]`), warn, no stamp.
2. `prompts/heartbeat.md`: `notification_text` is the message Roi sees; the reply shrinks to a terse tick log (keep `[NO_ACTION]` only while the fallback still fires in practice, then drop the contract).
3. Telemetry `no_action` flag: derive from the ack (`notify=False and not acted_tasks`) with reply-text fallback, so usage rollups stay comparable.

**Verification:** ack `notify=true` tick → exactly one message, body = `notification_text`; `notify=false` + chatty reply → nothing sent; ack-missing tick → fallback delivery + warning. Watch a week of `notifications.jsonl` for volume/shape regressions.

**Rollback:** revert the delivery branch; the ack keeps driving stamping as in Phase 3.

**Deliberately unlocked later (not in scope):** typed extensions on the payload — `priority` (silent vs push), dedup keys, multi-part messages. Possible only once delivery is payload-driven; add on demand.

---

## 4. Savings summary (honest)

| Phase | Ships | LLM-call reduction | Per-tick token reduction | Risk |
|---|---|---|---|---|
| 1 | scope infra, no new tool | 0 | 0 | very low (regression-tested = no change) |
| 2 | `heartbeat_respond` tool | 0 | 0 | very low |
| 3 | `state.json` parallel write | 0 | 0 | low |
| 4 | cadence gate + min-spacing | **~0 with current 1h tasks** (infra) | 0 | low (fail open) |
| 5 | inject only due tasks | 0 | **large, every tick** (8→1–2 blocks) | low |
| 6 | time/day windows | **large** (closes 1h tasks off-window → LLM skipped) | further | low |
| 7 | retire markdown `last_run` | 0 | tiny | very low |
| 8 | `manage_heartbeat_task` + prompts | — (capability) | indirectly enables tighter windows → more skips | low |
| 9 | ack-primary delivery (reply-text fallback) | 0 | 0 (correctness: kills ack/reply disagreement windows) | low-medium (evidence-gated on ack reliability) |

**The cost needle moves at 5 (tokens) and 6 (calls).** Phases 1–4 are the infrastructure that makes them safe. **Phase 8 is the capability Roi prioritized** — and it *compounds* the cost win, because a Jarvis that maintains precise `due:` windows lets the Phase 4 gate skip more ticks.

---

## 5. Open questions to settle before coding

1. **Reading the `heartbeat_respond` result** — walk the final LangGraph state for the last `ToolMessage` named `heartbeat_respond` (clean) vs. a module-level slot the tool writes (simpler, global state). **Recommend: walk the state.**
2. **Agent acts but omits `heartbeat_respond`** — v1: warn, don't stamp, let the task re-fire. The full OpenClaw-style hierarchy (ack primary, reply-text fallback) is Phase 9. **Recommend: warn + no stamp.**
3. **Partial success** — stamp only the tasks the agent listed in `acted_tasks`. The agent lists only what it finished.
4. **`manage_heartbeat_task` re-serialization fidelity** — does round-tripping the task section preserve arbitrary human prose safely? If markdown round-tripping proves fragile, reconsider a structured store (deferred — §1.6 rationale prefers markdown).

---

## 6. Files touched per phase

| Phase | New | Modified |
|---|---|---|
| 1 | — | `tools/registry.py` |
| 2 | `tools/core/heartbeat.py` | `tools/core/__init__.py`, `prompts/heartbeat.md`, `heartbeat.py` (log only) |
| 3 | `heartbeat_state.py` | `heartbeat.py` (stamp after tick) |
| 4 | — | `heartbeat.py` (gate before LLM) |
| 5 | — | `agent.py` (`build_system_prompt` `due_tasks`, `ask_jarvis` threading), `heartbeat.py` (pass due list) |
| 6 | — | `heartbeat_state.py` (window parser), `HEARTBEAT.md` (`due:` on tasks), `prompts/heartbeat.md` |
| 7 | — | `prompts/heartbeat.md`, `HEARTBEAT.md` (`state:`→`notes:`), `heartbeat/*.md` (clear `last_run:`) |
| 8 | — | `tools/core/heartbeat.py` (add `manage_heartbeat_task`), `tools/core/__init__.py`, `prompts/AGENTS.md` (authoring + reminder-vs-task rules) |
| 9 | — | `heartbeat.py` (delivery branch), `prompts/heartbeat.md`, `agent.py` (`no_action` from ack) |

*(Only files under `/app/jarvis_code/` are git-tracked. `HEARTBEAT.md`, `heartbeat/*.md`, and `state.json` are runtime data and never committed.)*

---

## 7. Out-of-scope follow-ups

- **#20 on-demand heartbeat** — `min-spacing` (Phase 4) and a future `flood` guard prepare for it.
- **#33 Lever 4 (Gemini caching)** — now designed as [CONTEXT_HANDLING_PLAN.md](CONTEXT_HANDLING_PLAN.md) WS2 (cache-stable prompt layout). Compounds: every skipped tick is a saved cache miss; every remaining tick benefits more from a cached prefix because the variable tail (only due tasks) is smaller after Phase 5.
- **Heartbeat-scope `lightContext` analog** — now designed as [CONTEXT_HANDLING_PLAN.md](CONTEXT_HANDLING_PLAN.md) WS3. OpenClaw's `lightContext`/`isolatedSession` cut a run from ~100k to ~2–5k tokens by trimming bootstrap context to just `HEARTBEAT.md`; the measured Jarvis no-op tick is 66k input, so the headroom is real.
- **Exact-time self-scheduling (OpenClaw *cron* analog)** — Roi chose windowed-poll; if hourly polling for the 1h tasks ever proves too expensive even after Phase 6, revisit scheduling exact wakes via the existing reminder substrate.
- **Deterministic Arbox poll** — move `crossfit-sync`'s hourly Arbox check to a deterministic tool that escalates to the LLM only on a schedule change (§0).
