# Context Handling — Research and Retained Findings

**Date:** 2026-08-06.
**Companion:** [PROBLEMS.md](PROBLEMS.md) — the problem inventory this material informs.

**What this is.** The material worth keeping from the context-handling work that is **not** a
problem statement and **not** a proposed solution: how two reference systems handle the same
concerns, how the measurements were actually taken, and a set of findings about Jarvis's own code
that were discovered while planning and would otherwise be lost.

It was extracted from `CONTEXT_HANDLING_PLAN.md` and `TEST_HARNESS_PLAN.md` before both were
deleted (2026-08-06). Those documents are recoverable from git history; this file exists so that
nothing here depends on recovering them.

**What this is not.** A plan, a ranking, or a recommendation. Nothing below argues for building
anything.

---

## 1. How the reference systems handle these concerns

Two systems were read during planning: **OpenClaw** (full source dive; the detailed writeup lives in
`docs/plans/archive/HEARTBEAT_GATING_PLAN.md` §2) and Nous Research's **hermes-agent** (docs). The
comparison below is the useful residue, stated against Jarvis as it stands today.

| Concern | OpenClaw | hermes-agent | Jarvis today |
|---|---|---|---|
| **Cache-stable prompt** | Explicit stable-prefix / dynamic-suffix boundary; **timezone only, no live clock** in the prompt — time is obtained via a tool | Three tiers (stable → context → volatile); ephemeral material goes in the **user message**, never the system prompt; memory injected as **frozen snapshots** | Timestamp is the first line of the system prompt, rebuilt on every LLM call |
| **Injected-file budgets** | 20k chars per file, 60k total, with truncation warnings; the file stays intact on disk | 20k chars, 70/20 head/tail split, plus an injection scan | Uncapped (SOUL.md, USER.md, HEARTBEAT.md, daily logs) |
| **Heartbeat cost** | Only-due-task injection; skip if nothing is due; `lightContext` (~100k → 2–5k); `isolatedSession` | Cron jobs in isolated sessions; **cron tools disabled inside cron runs** | Due-only injection and a pre-LLM gate shipped; per-tick ceremony remains |
| **Memory recall** | `memory_search`: hybrid vector + BM25 over chunked files | Bounded in-context memory plus **FTS5 full-text search over all session history** (~20ms, no LLM involved) | `list_memory` + `read_memory` by exact name; no search |
| **Memory size control** | Curated MEMORY.md plus opt-in "dreaming" consolidation | **Hard caps** (MEMORY.md ~2,200 chars, USER.md ~1,375) + a `[67% full]` gauge in the prompt + write-errors that force same-turn consolidation | No size pressure anywhere |
| **History compaction** | Summarize-old near the context limit, with a **pre-compaction memory flush** (a silent save-to-memory turn first); separately, a lighter **tool-result pruning** pass that keeps head+tail and is cache-TTL-aware | Head/torso/tail: protect the last ~20 messages / ~20k tokens, summarize the middle at 50% of the window | Hard trim at 50 messages; older context discarded |

**The one convergence worth naming.** Both systems independently keep a live clock *out* of the
system prompt and put ephemeral material in the message stream instead. They arrived there by
different routes and state different reasons, but the shape is the same in both.

**A constraint on anything built here: cross-tick caching is unreachable.** `ASSERTED`
Implicit cache TTL is on the order of minutes; heartbeat ticks are hourly. One tick can therefore
never hit the previous tick's cache, no matter how stable the prefix is. Recorded because an earlier
plan claimed that win, and because it bounds what any prompt-layout work can be worth: the reachable
gains are *intra-turn* (a turn's own later calls) and *user conversation bursts*, not tick-to-tick.
Carried from a design pass whose draft was lost and never re-verified against current Gemini
documentation — confirm before relying on it.

---

## 2. How the measurements were taken

Recorded so any figure can be re-derived rather than copied forward. Every number in PROBLEMS.md
came from one of these.

**Per-call input composition** (the 63% / 18% / 10% / 9% split for a heartbeat call):

| Component | Measured how |
|---|---|
| Message history | Byte length of the `heartbeat` row in `threads.sqlite` (read-only via `SqliteSaver`) |
| System prompt | `len(build_system_prompt("heartbeat", set(), due_tasks=[]))` — directly callable |
| Tool schemas | Serialized `args_schema` summed over `get_tools(scope="heartbeat")` |
| Tool results | **Residual**: observed input per call − prompt − schemas − history |

Only the last is inferred. Note the residual absorbs any error in the other three.

**Token and turn totals:** `observability.usage.summarize_usage(group_by="scope")` over
`turns.jsonl`. **Per-tool counts:** join `turns.jsonl` ↔ `tool_calls.jsonl` on `turn_id`;
`scripts/trace.py` performs that join for a single turn and prints a timeline.

**Gate behaviour:** grep the service journal for the skip and fail-open lines. Cross-check against
heartbeat rows per day in `turns.jsonl` — a gated tick records no row, so a drop in rows/day *is*
the gate arriving, and the two sources should agree.

**Caveats that have already bitten.**

- `turns.jsonl` contains at least one unparseable line (as of 2026-08-06). Parse tolerantly and
  report the skip count; a strict `json.loads` over the file raises.
- Dollar figures are not safe to carry forward. `MODEL_PRICES` priced `gemini-3-flash-preview` 6.5x
  too low until 2026-07-29, so every cost figure written before that date is wrong. Token and turn
  counts were never affected.
- Absolute per-day figures are sensitive to development activity in the user scope; compare
  equal-length windows, and prefer per-turn and per-tick ratios over daily totals.

---

## 3. Findings about Jarvis's own code

Discovered while planning, independent of any plan. Each is a constraint on some future change, not
a problem to be fixed.

### 3.1 Four constraints on any event-driven / deterministic-wake design

The idea repeatedly proposed was a cheap deterministic "probe" that checks a condition without an
LLM turn, waking the model only on a change. Four things make that harder than it looks:

1. **A probe cannot call `fetch_upcoming_arbox_classes()`.** It is not a read. It upserts the
   workouts DB and calls `_purge_dropped_arbox_classes` (`tools/fitness/fitness_tools.py:336`),
   which **deletes** the dropped rows and returns them so the caller can build a
   `"Removed N class(es) you're no longer registered for…"` notice (`:437-443`). That notice is
   **one-shot** — the rows are gone. A probe that calls it and then wakes the LLM to call it again
   gets nothing the second time, so the whole dropped-class flow (delete the stale reminder, tell
   the owner, re-check the weekly quota) silently never fires. Any probe needs a pure-read split
   first. *(Verified against the code 2026-08-06.)*
2. **Probe state must be code-owned**, not stored in a `heartbeat/*.md` notes file. Same reasoning
   that moved `last_run` out to `state.json`: agent-written prose is not a reliable machine record
   (see 3.2).
3. **Stamp after success, or a transient error becomes permanent.** The failure shape: probe sees a
   new class → escalates → writes its state → the LLM turn times out at 90s (this happens) → the
   next tick sees no diff → it skips forever, and the class never gets its reminder. The existing
   discipline is the answer — only acted tasks advance state, and only after delivery settles.
4. **A "<4h before the class" briefing is time-shaped, not diff-shaped.** A pure schedule diff never
   fires it: the schedule is unchanged, the clock moved. Wake-on-change and wake-at-time are two
   different mechanisms, and conflating them is what made a probe look simple.

**Background on the design choice this reopens.** OpenClaw runs **two** mechanisms — a
fixed-interval heartbeat poll *and* cron for exact-time wake. Jarvis deliberately chose
windowed-poll only, omitting OpenClaw's `nextCheck`, on the grounds that one mechanism was enough.
"Wake before practice" reopens that decision. Note also that `manage_reminder` + APScheduler already
does exact-time wake with restart persistence and past-due handling — it just notifies the owner
rather than waking the agent.

### 3.2 Heartbeat notes files are not a reliable machine record

Stated in PROBLEMS.md as D1. The detail worth keeping: four different spellings of the same concept
(`last_checked`, `last_checked_date`, `last_sync_date`, `last_notified`), one file missing the `#`
header the others carry, three carrying no state at all, and `running_prep.md` / `readiness_check.md`
already disagreeing with each other about the same session number. `last_run` was moved out to
code-owned `state.json` for precisely this reason; nothing else was.

### 3.3 What actually drives tool-use behaviour

From one failed change, fully instrumented. Three explanations were proposed and two were wrong;
only reading the checkpoint and the tool-call profile distinguished them.

- **The docstring is the behavioural contract, not prompt prose.** An instruction was removed from
  the tick message and the behaviour did not change. The real driver was the tool's own docstring,
  bound into every call via `llm.bind_tools()` — its worked example advertised exactly the redundant
  usage, and the model's calls reproduced that example's shape (`Z`-suffixed), not the removed
  instruction's (`+03:00`). Scoping a behaviour change without grepping the tool surface is how that
  was missed.
- **A polluted checkpoint re-seeds itself.** After both the prose and the docstring were corrected,
  the behaviour persisted: three identical wrong-shaped calls sat in the 50-message window and kept
  the pattern alive by imitation. It would not have self-cleared. Resetting the affected thread was
  what stopped it — durable state (`state.json`, notes files, `scheduled_events.json`) lives outside
  the checkpoint, so clearing a thread does not lose it.
- **A negative instruction was deliberately not added.** It would have guessed at an unobserved
  driver, and adding prose to steer a tool is the anti-pattern the episode had just demonstrated.
  The behaviour went to ~0 without one.
- **Docstring examples can be live bugs.** The same example's UTC-midnight bound silently dropped
  00:00–03:00 Israel time from every fold-in. It was found only because something else was being
  investigated.

### 3.4 The deploy loop shapes what can be verified

Deploys are manual by choice — the owner restarts. Every "after" reading therefore lands hours or
days after the commit, which is exactly the gap in which recall substitutes itself for a
measurement. This is a standing property of the setup, not a defect, and any verification approach
has to survive it.

**Two deliberate non-goals** were recorded alongside it, with reasoning worth keeping:

- **Continuous deployment / auto-deploy on merge.** Not merely unbuilt — declined. The owner does
  every restart by choice, and the host sits behind default-deny on an LXC, so a self-hosted runner
  is real work that unblocks nothing. Relevant to issue #5, which still lists auto-deploy as a goal.
- **An LLM-judge eval framework.** Most of this problem space is assertable without a model at all:
  prefix stability, truncation markers, prompt size, checkpoint weight and tool-call shape are all
  plain asserts over data that already exists. Exactly one concern was identified as genuinely
  needing a judge — whether a lighter heartbeat prompt produces blander or wronger briefing text —
  and one narrow judge over a handful of fixtures is not a framework.

---

## 4. Issue numbering: the cross-references do not resolve

The deleted plans cite issues by number, and **most of those numbers do not mean what the plans
think they mean in this repository.** Checked 2026-08-06:

| Cited as | Actually, in this repo |
|---|---|
| #18 — "reduce token spend" umbrella | **Correct.** #18 is that umbrella, open |
| #33 — "the same umbrella, archive mirror" | #33 is *Graceful shutdown never runs* (open, unrelated) |
| #54 — "compaction; Stage 3/4 sketch, evidence gate" | #54 is *Unify slash-command reply formatting* (closed, unrelated) |
| #20 — "on-demand / event-driven heartbeat" | #20 is *Image memory & persistence* (closed, unrelated) |

The plans were written against an earlier "archive" repository whose numbering does not map here,
and only some citations were tagged as such. **Consequence:** #18 is the only live, correct issue
reference in that material. The compaction analysis and the event-driven-heartbeat deferral have no
issue in this repository — treat them as untracked, not as filed elsewhere.
