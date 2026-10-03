# Turn lifecycle — bounded, honest, self-consistent turns

**Status:** slices 0–3 implemented as a native GitHub stack (#122–#126), code-reviewed and
revised; staging checks and slice 4 pending. Decisions marked **OPEN** are still under discussion.
**Date:** 2026-10-02.
**Goal:** a turn always ends in one of a small set of named outcomes, never destroys its own
context, is bounded by wall-clock time rather than an arbitrary step count, and always tells the
owner what happened — including which writes already landed when it did not finish.

**Tracks:** issue #36 (directions 1–4; direction 5 stays deferred there) and PROBLEMS.md B3.
**Evidence:** the 2026-10-02 fault register (prod, 7 Sep – 2 Oct, 1,004 turns), faults JRV-01,
JRV-02, JRV-03, JRV-04, JRV-08 — 22 incidents between them.

---

## Checklist

**Slice 0 — outcome type + honest failure** (independent of slices 1–3; ship first) — offline harness: `scripts/test_turn_lifecycle.py`
- [x] New `turn_budget.py`: `TurnOutcome` (`kind`, `text`, `cause`, `committed_calls`)
- [x] `ask_jarvis` returns `TurnOutcome`; model/upstream exceptions become `kind="failed"` instead of raising
- [x] Failure text built in the runtime: error class (upstream overloaded/timeout vs internal) + committed tool calls by name and count + "do not re-run these"
- [x] Failure note written into the thread checkpoint as an `AIMessage` (both scopes)
- [x] Mirror cursor advances once the turn's input is checkpointed, not only on success
- [x] Replace the false "Nothing was left half-saved" fallback in `_summarize_exhausted_turn`
- [x] `outcome` field (`completed` / `wrapped_up` / `budget_exhausted` / `failed`) in `turns.jsonl`
- [x] User callers: one `main.py` helper used by `process_inbound_message` **and** `on_confirmation_outcome`; sends `outcome.text`, chat-logs it
- [x] Heartbeat: logs `outcome.cause` (the real cause) instead of a generic error
- [x] Heartbeat: `failed` / `budget_exhausted` → code-built notice to the owner via `default_outbox().notify_owner(..., event=EVENT_HEARTBEAT, metadata={"tick_failed": True})`
- [x] No new event: the notice is an ordinary heartbeat send, its log row marked `"tick_failed": true`
- [x] Staging (2026-10-03, injected 503 after `manage_reminder`): reply named the committed call; the next turn read the thread note and answered from the saved result without re-running the tool. No-duplicate-mirror covered offline.
- [x] Heartbeat failure notice: verified offline through the real Outbox (delivered, logged as `tick_failed`, mirrored as `[Heartbeat]`); live staging run judged unnecessary — the send path is the hourly briefing path

**Slice 1 — trim at turn boundaries** — offline harness reproduces JRV-01 on the old reducer
- [x] `_add_and_trim` trims only when `new` contains a `HumanMessage`; otherwise appends
- [x] Trim cut never starts on a `ToolMessage` or splits a tool-call/response pair
- [x] Trim by whole turns, always keeping the previous turn (review fix: one long turn used to leave only the new input)
- [x] RUNTIME.md `messages` row updated
- [x] Staging (2026-10-03): 101 tool calls / 12 LLM calls in one turn, `completed`; checkpoint kept the request and all prior history; the next turn kept that 114-message turn whole

**Slice 2 — budget enforced in the graph** — numbers kept at today's behaviour (13 calls; heartbeat 90s) until slice 3
- [x] `ScopePolicy` (budget + wrap-up notice + exhaustion ask) and `POLICIES` per scope in `turn_budget.py`
- [x] Budget tracker created by `ask_jarvis`, carried in a ContextVar; it counts calls and input tokens itself (never reads telemetry)
- [x] `_llm_node` checks the tracker before every call; it sets `wrapped_up` / `exhausted_by`
- [x] `wrap_up`: scope's notice appended to the request only — never persisted to the checkpoint
- [x] `exhausted`: model called with no tools + scope's exhaustion ask → graph ends normally → `outcome = budget_exhausted`
- [x] Remove the `GraphRecursionError` catch + out-of-graph `_summarize_exhausted_turn`; `recursion_limit` becomes a backstop above the step guard
- [x] Per-call `timeout` passed per invoke, capped to the remaining budget (kwarg path verified in the client source; values covered offline — no log records it)
- [x] Heartbeat drops `asyncio.wait_for(..., 90)`; its bound comes from its `ScopePolicy`
- [x] 504 retry policy decided: keep the client default (two retries) for now (2026-10-03); revisit with the slice 4 readings
- [x] Staging (2026-10-03): 6-call and 20s budgets → wrap-up notice delivered, model answered from what it had (`wrapped_up`), notice never persisted, next turns ran normally. The run surfaced two wording fixes (system-notice label, code-appended "Wrapped up early" line), now in slice 2.
- [ ] Heartbeat wrap-up with the real model — skipped on staging: at the slice 3 limits no normal tick on record reaches the notice, and ignoring it fails safe (no ack → no stamp → re-run + notice). Watch in the slice 4 readings.
- [x] RUNTIME.md: turn-budget section (policy table, outcome vocabulary)

**Slice 3 — budget numbers** (requires slice 1 shipped)
- [x] Provisional numbers applied (user 300s / 30 calls / 1.5M input; heartbeat 120s / 15 calls / 400k) — **still OPEN for review**
- [x] Incorrect "LangGraph's own default" comment removed (slice 2 replaced the constant with a backstop)

**Slice 3b — budget telemetry** (the instrument slice 4 reads)
- [x] Each `turns.jsonl` row carries a `budget` block: limits in force, `exhausted_by`, `wrapped_up`
- [x] `/usage` shows outcomes: errors (failed only), stopped early by limit, wrapped up
- [x] `scripts/trace.py` prints `outcome` (and the limit that ended the turn)
- [x] OBSERVABILITY.md schema + `/usage` section

**Slice 4 — prod verification & re-tune**
- [ ] Deploy; record the deploy date here
- [ ] 14-day reading: 400 `INVALID_ARGUMENT` count (expect 0)
- [ ] 14-day reading: `outcome` distribution per scope; user `budget_exhausted` rate vs 12/705
- [ ] 14-day reading: uncensored LLM-calls distribution above 13
- [ ] 14-day reading: input tokens per user turn p50/p99 vs trip baseline
- [ ] 14-day reading: errored turns with no owner-facing message, either scope (expect 0)
- [ ] 14-day reading: `tick_failed` notices sent — decide whether they are noise
- [ ] Re-tune slice 3 numbers from the readings; record final values and why
- [ ] Close out #36 directions 1–4 and archive this plan

---

## Context

A turn currently has four independent ways to stop, and none of them owns the outcome:

| Exit | Mechanism | What the owner sees |
|---|---|---|
| Context self-destruction (JRV-01, 7×) | `_add_and_trim` runs on **every** state write. Once a turn's own `AIMessage` + `ToolMessage` traffic reaches ~50 entries (≈40 tool calls), the turn's `HumanMessage` is sliced off, the no-human fallback returns a raw slice starting on an orphaned `ToolMessage`, Gemini answers `400 INVALID_ARGUMENT`. The trimmed state is checkpointed, so prior history is lost too. | Nothing (see JRV-04) |
| Step budget (JRV-08, 12×) | `RECURSION_LIMIT = 25` super-steps = 12 tool rounds + a 13th LLM call, then `_summarize_exhausted_turn` = 14 calls. Every one of the 12 incidents stopped at exactly 14. | A tool-free summary |
| No wall clock (JRV-02, 3×) | User path has no outer bound. Per call: 60s × (1 + 2 retries) ≈ 3 min, unbounded across steps. | Typing, then nothing |
| Raised exception (JRV-04, spans all) | `process_inbound_message` re-raises; the channel router logs and sends nothing. Tool calls from earlier steps have **already committed**. | Nothing — which is how 18 Sep produced two retries and six orphaned travel records |

JRV-03 (one stuck turn freezes the queue) is a consequence, not a cause: turns are serialized
on purpose (`_owner_turn_lock`, checkpoint race). Bounding turns and reporting failures removes
its cost; parallelizing turns is not on the table.

### Finding: the 25-step budget rests on a wrong premise

`agent.py` declares `RECURSION_LIMIT = 25` with the comment *"this is LangGraph's own default"*.
For the installed LangGraph (1.2.7) it is not: `langgraph/_internal/_config.py` sets
`DEFAULT_RECURSION_LIMIT = 10007` (25 is LangChain-core's runnable default). Declaring 25 in
PR #116 therefore **lowered** the user-scope ceiling by ~400×; it did not keep it.

`turns.jsonl` agrees. Before prod picked up PR #116 (deploy 2026-09-07), user turns ran past 13
LLM calls and completed normally:

| Date | LLM calls | Tool calls | Duration | Outcome |
|---|---|---|---|---|
| 07-13 | 15 | 14 | 37s | ok |
| 07-17 | 16 | 15 | 108s | ok |
| 07-20 | 22 | 21 | 53s | ok |
| 08-06 | 16 | 16 | 284s | ok |
| 09-06 | 21 | 20 | 257s | ok |

Since the deploy, **no user turn has exceeded 14 calls, and every turn that reached 14 was
clipped**. The distribution is censored at the ceiling: the 12 clipped turns tell us demand
exceeds 13 calls, not by how much. The p99.5 = 10 figure behind PR #116 was measured on the
pre-travel workload.

### Reading (user scope, `turns.jsonl`)

| | pre-trip (n=526) | trip 09-07..10-02 (n=705) | heartbeat, trip (n=97) |
|---|---|---|---|
| LLM calls p50 / p95 / p99 / max | 2 / 7 / 16 / 25 | 3 / 7 / 14 / **14 (ceiling)** | 4 / 6 / 9 / 9 |
| Tool calls p50 / p95 / p99 / max | 2 / 10 / 21 / 36 | 3 / 16 / 38 / 61 | 6 / 10 / 12 / 12 |
| Duration p50 / p95 / p99 / max | 9s / 24s / 71s / 464s | 10s / 42s / 118s / 279s | 13s / 26s / 86s / 86s |

Per-LLM-call wall time (trip, user): p50 3.6s, p90 9.2s. Turn input tokens grow roughly
linearly with calls at ~32k/call (1 call ≈ 31k, 4 ≈ 118k, 8 ≈ 232k, 14 ≈ 447k), because the
cached system prompt + window dominates each call (PROBLEMS.md A4/A5).

### What the reference systems do

From source (`NousResearch/hermes-agent` @ `009afb3a`, `openclaw/openclaw` @ `d0f7dcca`):

- **Neither uses a low step count as its primary bound.** OpenClaw has no step cap (opt-in
  repeated-call loop detection only); Hermes's gateway/CLI default is 500 iterations. Both
  bound by **time**: OpenClaw `agents.defaults.timeoutSeconds` (aborting; progress does not
  reset it) + a 120s model-idle watchdog; Hermes an opt-in `--run-budget` that caps each call's
  timeout at half the remaining budget.
- **Hermes warns before it stops.** At 80% of the run budget it injects one notice — *"Stop new
  discovery/verification work now. Produce the required final deliverable from the state you
  already have, completing only mandatory writes"* — while tools are still bound. Only at
  iteration exhaustion does it ask for a tool-free summary (the same shape as ours).
- **A timeout is a failed turn.** OpenClaw: *"A terminal timeout is a failed turn, not a
  successful completion"* — the explanation is retained in the result.
- **Neither severs a tool-call/tool-result pair** (both compact rather than trim).
- **After an interrupted turn, Hermes tells the model** *"Do NOT re-run tool calls whose results
  already appear in the history"* — the instruction that would have prevented 18 Sep.
- **Mid-run messages:** OpenClaw defaults to `steer` (inject before the next LLM call), Hermes
  to `interrupt`. Recorded under Deferred.

---

## Decisions

1. **One outcome vocabulary.** Every turn ends as exactly one of `completed`, `wrapped_up`
   (finished after the wrap-up notice), `budget_exhausted` (tool-free summary), `failed`. It is
   written to `turns.jsonl` as `outcome` alongside the existing `error`, so every later reading
   in this plan is a query rather than a log grep.
2. **Trim at turn boundaries only** (JRV-01 option A). Within a turn the window only grows;
   the 50-message cap applies when a new turn's `HumanMessage` arrives. Chosen over "pin the
   user message, keep trimming per write" because it also closes B3 (the prefix stops shifting
   mid-turn) and leaves exactly one forgetting event for a future pre-trim memory flush
   (REFERENCE_ARCHITECTURES §7). Accepted cost: a large turn re-sends a window longer than 50 —
   only on turns that today crash.
3. **Time is the primary bound; steps are a runaway guard.** Cooperative and in-graph (see
   Architecture) — never `asyncio.wait_for` around the thread, which abandons a still-running
   thread and, on the user path, would release `_owner_turn_lock` while it is still writing the
   `owner` checkpoint.
4. **Warn, then stop.** Wrap-up notice at 80% of the budget (tools still bound); tool-free
   exhaustion ask at 100%. Both scopes, different numbers and wording.
5. **Failures are classified once, in the runtime, and remembered.** The runtime writes the
   failure note (committed work, "do not re-run") into the thread for both scopes; callers only
   decide where the text goes.
6. **A failed heartbeat tells the owner.** A `failed` or `budget_exhausted` tick that left no
   `heartbeat_respond` ack sends a short code-built notice (a tick that acked what it finished
   delivers and stamps as usual) to the default channel (no model call — the model may be what failed).
   Because it is an Outbox send with an event, it is logged to `notifications.jsonl` and the
   pending-mirror drain carries it into the owner thread, so the chat side knows a tick failed
   instead of inventing a reason (#36 incident 1). Easy to reverse if it proves noisy; slice 4
   reads its volume.

---

## Architecture

The runtime enforces and classifies; callers decide delivery. Scope differences are data in one
policy table, never branches in the loop.

```
           ┌────────────── runtime (agent.py + turn_budget.py) ─────────────────┐
caller ──► │ ask_jarvis(scope) → policy = POLICIES[scope]; tracker → ContextVar │
           │   _llm_node: tracker.check() before every call                     │
           │     ok        → normal call, timeout = min(per-call cap, remaining)│
           │     wrap_up   → scope notice appended to the request (not stored)  │
           │     exhausted → no tools bound + scope exhaustion ask → END        │
           │   exception   → failure note written to the thread                 │
           │   returns TurnOutcome(kind, text, cause, committed_calls)          │
           └────────────────────────────────────────────────────────────────────┘
user callers (inbound, confirmation outcome) → one main.py helper: send outcome.text
heartbeat.py → ack/stamp as today; failed/budget_exhausted → owner notice (tick_failed)
```

| | user | heartbeat |
|---|---|---|
| Budget | deadline / LLM calls / input tokens (slice 3) | 120s / 15 calls / 400k input tokens |
| Wrap-up notice | finish the deliverable from what you have; only mandatory writes | call `heartbeat_respond` now, listing only tasks you completed |
| Exhaustion ask | what is done, what is not, what would finish it | list what was completed (no ack → no stamp → re-run, as today) |
| Failure delivery | `outcome.text` as the reply | code-built `tick_failed` heartbeat notice to the default channel |

Why these choices:

- **Enforcement in `_llm_node`, not the stream loop.** Every model call passes through it, so
  time, steps and tokens are checked at one point; exhaustion ends the graph through its normal
  `tools_condition` route, leaving a clean checkpoint and replacing both the
  `GraphRecursionError` catch and the out-of-graph summary call.
- **Wrap-up notice is request-only.** Hermes persists its notice; PROBLEMS.md F2 shows a polluted
  checkpoint re-seeds its own behaviour, so "stop now" text must not sit in history for later
  turns to imitate. It is a trailing user turn — the shape the exhaustion ask (and the old
  step-exhaustion summary before it) already used in prod — rather than text spliced into the
  last tool result. Appending at the tail also leaves the cached prefix intact.
- **Per-call timeout per invoke.** `ChatGoogleGenerativeAI` reads `timeout` and `max_retries`
  from call kwargs, so capping a call to the remaining budget needs no second client.
- **`turn_budget.py` as a top-level module** beside `turn_context.py` / `heartbeat_state.py`;
  `agent.py` only gains the node hooks.
- **`ask_jarvis_once`** (single tool-free call for media notifications) is out of scope.

Found while mapping callers:

- `on_confirmation_outcome` (`main.py`) is a third turn path and is silent on failure too — hence
  one shared user helper rather than a fix in `process_inbound_message` alone.
- The pending-mirror block is checkpointed at the first super-step, but `advance_cursor` runs
  only on success — a failed turn leaves the block in the thread **and** re-drains it next turn.

---

## Slice 0 — outcome type + honest failure (JRV-04; #36 directions 2–4)

Independent of everything else; ship first. Introduces `TurnOutcome` and moves failure
classification into the runtime (see Architecture).

- Failure text names the error class and the turn's committed tool calls by name and count
  (counted by the turn's tracker — e.g. *"I'd already run 11 manage_itinerary calls before this
  failed — check before asking me to redo it."*).
- The note is appended to the thread checkpoint as an `AIMessage` — valid after anything a failed
  turn can leave behind, because the tool node never raises: the turn ends on a `ToolMessage` or
  its `HumanMessage`. Wording carries the Hermes instruction that completed calls must not be
  re-run.
- `_summarize_exhausted_turn`'s fallback claims *"Nothing was left half-saved"* — false, since
  earlier tool calls commit. Replaced with the committed-work wording (the function itself goes in
  slice 2).
- Heartbeat failure notice is built in code from the due task names, `outcome.cause`, and the
  committed calls; it says the tasks re-run next tick if still due.

## Slice 1 — trim at turn boundaries (JRV-01, B3)

- `_add_and_trim`: trim only when `new` contains a `HumanMessage`; otherwise append.
- Trim by whole turns: cut only at a turn start (a `HumanMessage` not preceded by one), at
  the earliest start that fits the cap but never past the previous turn's start. Never starts on
  a `ToolMessage` or separates a call from its response; a long turn followed by "continue"
  still sees what it is continuing. Storage: the cap plus up to two turns.
- Media-blob stripping is unchanged.
- Docs: RUNTIME.md `messages` row; PROBLEMS.md B3 (`MEASURED`, points here).

## Slice 2 — budget enforced in the graph (JRV-02, JRV-08; #36 direction 1)

Mechanism only; the numbers are slice 3. See Architecture for the design.

- Worst case after this slice: deadline + one capped LLM call + one tool round + the exhaustion
  call. A tool that blocks inside the tool node is still bounded only by its own client timeouts
  (Deferred).
- Heartbeat wrap-up changes tick semantics slightly for the better: today a long tick dies
  without an ack and stamps nothing even for tasks it finished; with the notice it acks what it
  completed.
- Retry policy for `504 DEADLINE_EXCEEDED`: a generation that already ran 60s is retried twice
  today (11 occurrences in the window). **Decided 2026-10-03:** keep two retries for now; revisit
  if the slice 4 readings show 504s costing long turns (per-invoke `max_retries` is the lever).

## Slice 3 — budget numbers

**OPEN — provisional, not final.** Today's data cannot settle these: the trip distribution is
censored at 14 calls, so true demand is unknown. The first numbers only need to be generous
enough to *uncover* the real distribution while still capping a runaway; slice 4 re-tunes them
from the 14-day reading. Starting proposal:

| | user | heartbeat |
|---|---|---|
| Deadline | 300s (wrap-up at 240s) | 120s (wrap-up at 96s) |
| Call budget | 30 LLM calls | 15 (wrap-up at call 12) |
| Token ceiling | **OPEN** — ~1.5M input (~45 calls at the measured ~32k/call) | 400k (wrap-up at 320k) |

Token reading behind the ceilings (trip window, input tokens per turn): user p50 80k / p99 437k /
max 1.21M; heartbeat, all 1,805 ticks since Jun 2026 excluding the one runaway, max 86s / 10 calls / 245k.
The heartbeat limits put its wrap-up point above every normal tick on record, so the notice only
reaches a runaway; the runaway seen (412s, 13 calls, 472k) still stops, at 120s.

Reasoning: 300s covers the trip p99 (118s) and the longest successful pre-#116 turn (284s); at
the measured 3.6–9.2s per call, 30 calls fit in 2–4.5 min, so the deadline and the step guard
bind at about the same place for ordinary turns, and the deadline wins for slow ones. The
token ceiling is the only bound aimed at cost rather than latency — the original reason the
step budget exists (a runaway tick at 25 steps cost 128k output tokens).

Also fixes the incorrect "LangGraph's own default" comment.

**Order constraint:** slice 3 must not ship before slice 1. Raising the step guard alone moves
JRV-08's graceful summaries into JRV-01's 400s, because longer turns cross the 50-message line.

## Slice 3b — budget telemetry

Every row already recorded what a turn *used* against each limit (`duration_ms`, `llm_calls`,
`input_tokens`), but not what it was *allowed*, nor which limit ended it except inside the `error`
string. The `budget` block makes each row self-describing across limit changes, so re-tuning
never depends on deploy dates; `/usage` turns the slice 4 readings into one command instead of
ad-hoc queries (PROBLEMS.md E1: nothing read the instrument). A budget stop no longer counts as an
error in `/usage`. Censoring stays the reader's job: a `budget_exhausted` row shows the limit, not
what the turn wanted.

## Slice 4 — prod verification & re-tune

Staging checks live in each slice's checklist, against planted conditions (a 40+ tool-call fan-out
on a scratch staging trip; a forced mid-turn exception after a write; a temporarily tiny
deadline; a forced heartbeat failure).

Prod after-readings, defined now so they are taken from the instrument, not recall
(PROBLEMS.md E3), over the first 14 days after deploy:

- 400 `INVALID_ARGUMENT` count — expected 0.
- `outcome` distribution per scope; user `budget_exhausted` rate vs. the trip's 12 / 705.
- LLM-calls distribution above 13 — the uncensored demand curve this plan currently lacks.
- Input tokens per user turn p50/p99 vs. the trip reading above (cost of Decisions 2 and 3).
- Turns with `error` and no owner-facing message, either scope — expected 0.
- `tick_failed` notices sent — the input to keeping or reversing Decision 6.

---

## Deferred (recorded, not planned here)

- **Steering / interrupt for mid-turn messages** (OpenClaw `steer`, Hermes `interrupt`).
  Fits the graph — the stream already yields per super-step — but touches the lock, the
  checkpoint and both channels. Revisit only if queueing still hurts once turns are bounded.
- **Hung tools.** The budget is checked between super-steps; a tool that blocks inside the tool
  node is bounded only by its own client timeouts. Not observed in the window. Without
  `asyncio.wait_for`, a hung tool inside a tick holds the APScheduler job, so later ticks are
  skipped (APScheduler `max_instances=1`) — the accepted trade against two turns on one thread.
  If it ever happens: a watchdog that logs/notifies once a tick is well past its deadline,
  without cancelling it.
- **Read vs write tools.** Committed-call reporting counts every successful tool call, reads
  included, because the registry carries no read/write flag; the wording only claims the calls
  ran. A per-tool flag would let "do not repeat" apply to writes alone.
- **Fan-out at the source.** 37 parallel `manage_wishlist` calls in one step is a tool-shape
  problem (batch-capable tools, idempotent writes); belongs with the travel-tool work
  (register family 5).
- **#36 direction 5** — re-offering a heartbeat task whose tick errored.
- **Compaction instead of trimming** (REFERENCE_ARCHITECTURES §3) — the long-term answer to
  B3/C3; slice 1 is the minimal step toward it, not a substitute.
