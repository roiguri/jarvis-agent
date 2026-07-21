# Cost Bug, Testing & the Feedback Loop

**Issue:** TBD (new). Related: #33 (archive) = #18 (origin mirror) — "Reduce token spend" umbrella.
**Branch:** `fix/heartbeat-cost` (proposed).
**Companion:** [CONTEXT_HANDLING_PLAN.md](CONTEXT_HANDLING_PLAN.md) — this plan re-baselines it (Phase 5).
**Date:** 2026-07-16.
**Goal:** stop an active token leak, then build the loop that would have caught it. Today there
is no test suite, no CI, no way to run the agent without touching production state, and no
regression check on the metric the whole context roadmap optimizes. A workstream shipped, was
documented as "verified in production", and the number it targeted moved the wrong way for
three days unnoticed.

**Non-goals (deliberately deferred):** CD / auto-deploy on merge (the owner does every restart
by choice; the box is behind default-deny on an LXC — a self-hosted runner is real work and
blocks nothing). A second Telegram bot token + staging systemd instance (most of the context
roadmap's risk is structural and needs no live Telegram loop; the config seam in Phase 2 is the
prerequisite if we ever want it). LLM-judge eval frameworks (exactly one workstream needs a
judge — see §0.6).

---

## Manager summary

**Problem.** Two problems. (1) **An active leak:** the heartbeat tick spends ~214k input tokens
across ~5 LLM round-trips to make **one 785ms API call** — the rest is bookkeeping the harness
asks for and then throws away (§0.7). (2) **No feedback loop:** every test runs against the
production agent, writing into prod memory, prod `threads.sqlite`, and prod `turns.jsonl` — the
last being the *measurement instrument* for the context roadmap, so testing corrupts the data
used to evaluate what's being tested. Nothing is automatically checked, so drift is invisible.
The root cause of (2) is that paths are frozen literals in 8 modules: nothing can point at a
fixture directory, so no test can exist.

**What the research found.** Measuring instead of trusting the roadmap's baseline (§0) showed
the context roadmap is aimed at ~10% of per-call input, its shipped workstream underdelivered
~3x against a "verified in production" claim, and — the finding that matters most (§0.7) — the
tick's cost is not the gate at all. It is ceremony: re-fetching context the prompt already
holds, and rewriting the daily log every hour. §0 is the reason this plan exists and should be
read first.

**What ships.** Five phases. The money is Phase 1; the rest makes it stay fixed.

| Phase | What lands | Impact |
|---|---|---|
| 1 | Stop the ceremony — three deletions from the tick's critical path (§0.7) | **The money.** ~3.8 → ~1 LLM call/tick; helps *every* task, not just crossfit |
| 2 | `config.py` seam — env-driven paths, defaulting to today's literals | No behavior change; byte-identical prod. Unblocks 3–4 |
| 3 | pytest + fixture memory/data tree + conftest | A throwaway Jarvis that cannot touch prod |
| 4 | Four tests, in value order (budget ceiling, checkpoint weight, golden prompts, cache prefix) + a cost report that runs | The regression checks that would have caught the drift |
| 5 | Re-baseline `CONTEXT_HANDLING_PLAN.md` against §0 | The roadmap starts pointing at the real 63% |

**Ordering rationale.** Phase 1 is pure deletion and injection — no new mechanism, no new
state, no capability lost — and its win needs no new instrumentation: it is readable in
`turns.jsonl` as `llm_calls`/tick and input tokens/day (§0.5). Phases 2–4 follow rather than
precede it: the harness reduces zero tokens by itself (§4).

**Deliberately not here: gating the tick more cleverly.** Earlier drafts of this plan made a
deterministic Arbox poll ("probe") the first phase. That is now deferred to **#20**
(on-demand/event-driven heartbeat) — see §3b. The analysis in §0.7 is the reason: the gate opens
~16×/day (§0.3), but each opening costs 214k tokens because of ceremony, not because of the
gate. Making an open gate cheap is smaller, safer, and helps all eight tasks; making the gate
open less often is a genuine capability question that deserves its own design.

**Risk posture.** Phase 1 is the only phase that changes agent behavior, and it only *removes*
instructions — each of the three is independently revertable in one commit. Phase 2 is a no-op
refactor (defaults preserve every current literal). Phases 3–4 add files only. Phase 5 is
documentation.

**Validation.** Every claim in §0 was measured on this host against live telemetry
(`turns.jsonl`, 1,630 turns), the live checkpoint DB, and the service journal.

---

## 0. Honest framing: what the measurements actually say

All figures measured 2026-07-16 on the live host. This supersedes the baseline in
[CONTEXT_HANDLING_PLAN.md](CONTEXT_HANDLING_PLAN.md) §"Measured baseline", which is a week
stale and roughly 2x off.

### 0.1 The context roadmap targets ~10% of per-call input

Per-call breakdown for a recent heartbeat tick (~42.8k input tokens/call — from a no-op tick on
2026-07-16 at 214,008 input over 5 LLM calls):

| Component | Tokens | Share | Measured how |
|---|---|---|---|
| **Message history** (50-msg checkpoint) | **~27k** | **~63%** | `heartbeat` row in `threads.sqlite` = 108,785 bytes |
| Tool results accruing within the turn | ~7.5k | ~18% | residual (observed/call − prompt − schemas − history) |
| System prompt | ~4.5k | ~10% | `build_system_prompt("heartbeat", set(), due_tasks=[])` = 18,118 chars |
| Tool schemas (12 bound tools) | ~3.8k | ~9% | serialized `args_schema` over `get_tools(scope="heartbeat")` = 15,045 chars |

**WS2, WS3 and WS4 all target the system prompt** — the ~10% slice. The 63% is message history
and tool results, which is **WS7 — parked**.

This makes WS3's stated target unreachable, not merely ambitious. WS3 aims at "66k → ≤15k per
tick". A tick is ~5 calls. Deleting the *entire* system prompt (SOUL, AGENTS, USER, framing,
skills — everything) leaves ~38k/call ≈ 190k/tick. The target cannot be hit by the method
proposed, at any level of execution quality.

### 0.2 WS1's "verified in production" does not hold

The roadmap states WS1 is done and "Most hours: no LLM call at all — verified in production."
Service journal, last 7 days:

```
skipped ticks (gate fired): 35
ran the model:             127
gate errors (fail-open):     0
```

**A 22% skip rate, not "most hours."** The gate is working correctly and not failing open —
the task definitions simply don't let it skip.

### 0.3 THE COST BUG — one task pins the gate open

From `/app/jarvis_memory/HEARTBEAT.md`:

```
crossfit-sync-and-remind | every 1h | due: 06:00-22:00
```

WS1 Phase 6 shipped as "all 8 tasks windowed" and technically that is true — but **a 16-hour
window on an hourly cadence is due 16 times a day, every day.** The gate's mathematical ceiling
is the 8 night hours (~33%); observed is 22% (other tasks straddle the night edge).

What that tick actually does, per `/app/jarvis_memory/heartbeat/crossfit_check.md`:

```
last_known_schedule: []
notes: Checked Arbox at 21:23; no registered classes found.
```

**The schedule is empty.** The task wakes a ~5-call LLM turn to fetch an empty list from Arbox
and conclude nothing changed. On 2026-07-16 the journal shows `crossfit-sync-and-remind` as the
**only** due task on 7 of 12 sampled ticks; at ~114k avg input/tick that is **~800k input
tokens/day spent confirming an empty list is still empty.**

The gating plan's own §0 predicted this — *"Because two tasks are `every 1h`, something is due
every hour — so a cadence-only gate skips almost nothing with this mix"* — and Phase 6's windows
were the designated fix. For `crossfit-sync` the window is too wide to be one.

The roadmap's **Cleanups** section names a fix: make the Arbox poll deterministic and escalate
only when the schedule differs from `last_known_schedule`. That remains a real idea, but it is
**deferred to #20** (§3b) — because §0.7 shows the gate's open-rate is not what makes a tick
expensive. Fix the cost of an open gate first; it is cheaper, safer, and helps all eight tasks.

### 0.4 Consequences for spend

- Heartbeat is still **83% of all input tokens** (88.0M vs 17.9M user) — essentially unchanged
  from the 84% that motivated WS1.
- Per-tick input **did not fall** after WS1. It drifted up: ~68k avg in early July, ~90–114k
  recently, with a no-op tick on 2026-07-16 at **214k**.
- `turns.jsonl` totals: 1,307 heartbeat turns / 4,949 LLM calls / 88.0M input (35% cache-read);
  323 user turns / 795 calls / 17.9M input (23% cache-read).

### 0.5 Why nobody noticed — the data was there; nothing reads it

`heartbeat.py:59-60` returns before recording telemetry, so a gated tick writes no row to
`turns.jsonl`. That is **not** an instrumentation gap: fewer rows *is* the measurement. The
gate's arrival is plainly legible in heartbeat rows/day —

```
07-08: 24    07-09: 24    07-10: 21  ← WS1 ships
07-11: 18    07-15: 18               ← ~6 ticks/day now skipped
```

— and it agrees with the journal grep (§0.2). Total heartbeat input/day never fell, which is
equally legible. Every number needed to catch this was sitting in `turns.jsonl` the whole time.
`observability/usage.py` exists, is correct, and **nothing runs it**.

**This is the finding that orders this plan.** The gap isn't instrumentation, isn't evals, and
isn't isolation. It is that no one and nothing ever *reads the instrument*: no test, no CI, no
alert, no scheduled report. A metric regressed ~68% in plain sight while the doc said
"verified". Adding more meters would not have helped; Phase 4's scheduled report is the fix.

*(Narrow residual gap, hygiene not blocker: an absent row can mean "gate skipped" or "service
was down" — indistinguishable. Worth tagging a gated tick eventually; it belongs with the
reporting work in Phase 4, not in front of the money.)*

### 0.6 Most of the roadmap's risk needs no LLM to evaluate

Worth stating, since "prompt changes need behavioral evals" was the opening premise. WS2 is a
reordering; its criterion is "the first N bytes are stable across calls" — an assert. WS4 is
truncation; "marker appears, normal files pass byte-for-byte" — an assert. WS3's risk is silent
content-drop — a golden snapshot diff. WS5's risk is "does the model call the tool" — a
structural assert over `tool_calls.jsonl`.

Exactly one thing needs a judge: **WS3's briefing voice quality** (the roadmap itself names
"blander/wronger notification text" as the failure mode). One narrow judge over a handful of
fixtures — not a framework. Build that last, if WS3 survives §0.1 at all.

### 0.7 What a tick actually does — one API call wrapped in ceremony

The finding that reorders this plan. A full recent Arbox tick, from `scripts/trace.py`:

```
read_memory                     0ms
read_memory                     0ms
read_memory                     0ms
get_chat_history               10ms
read_memory                     0ms
fetch_upcoming_arbox_classes  785ms   ← the actual work
write_memory                    0ms
write_memory                    3ms   (1,334 bytes)
heartbeat_respond               0ms
```

**9 tool calls, 5 LLM round-trips, 214,008 input tokens — to make one 785ms API call.** Every
other call is bookkeeping, and each is a *sequential* round-trip re-sending the full ~43k
context. This is not crossfit-specific: across **1,290** heartbeat turns that call the Arbox
sync, totalling **86.8M input tokens (98% of all heartbeat spend)**, the per-tick averages are:

| Tool | Calls/tick | Verdict |
|---|---|---|
| `read_memory` | 3.1 | notes files + daily log — **injectable** |
| `write_memory` | 2.3 | notes file + daily log — daily log is **hourly for no reason** |
| `get_chat_history` | 0.95 | **redundant** — data is already in the prompt |
| `fetch_upcoming_arbox_classes` | 1.0 | the actual work |

Three causes, all in the harness rather than any task:

1. **`get_chat_history` re-fetches what the prompt already holds.** `build_system_prompt`
   injects a `--- Today's chat with Roi ---` section (`_load_recent_user_chat`, **60** messages,
   240-char cap, `telegram_`-filtered). But `heartbeat.py`'s tick message said *"For today's
   chat use get_chat_history(50, since=…)"* and `prompts/heartbeat.md` repeated it. The tool
   returns **50** messages at a **200**-char cap with **no thread filter** — strictly *less*
   data than the prompt already had, mixed with heartbeat-thread noise. `prompts/heartbeat.md`
   already contradicted itself: step 2 says *"If a `--- Today's chat with Roi ---` section is
   provided…"*.
2. **The daily log is read + rewritten every tick.** `prompts/heartbeat.md`: *"After the task
   work, update today's daily log… If the file already exists, read it first."* Not once a day
   — every tick. So an hourly check that finds nothing still rewrites 1.3KB of prose about
   finding nothing.
3. **Notes files are fetched by round-trip.** All eight `heartbeat/*.md` files together are
   **1,300 bytes (~325 tokens)**. The tick spends ~3 `read_memory` round-trips at ~43k tokens
   each to read them. **The data is ~100x cheaper than asking for it.** HEARTBEAT.md task
   headers already name each file (`notes: heartbeat/crossfit_check.md`), and the prompt already
   injects only the due tasks' blocks — so injecting their notes is mechanical.

**Note on the notes files themselves** (out of scope here, in scope for #20 / a redesign): they
are machine state hand-serialized as prose — four different spellings of "when did this last
happen" (`last_checked`, `last_checked_date`, `last_sync_date`, `last_notified`), one file
missing the `#` header the others have, three carrying no state at all. They already drift:
`running_prep.md` and `readiness_check.md` disagree about session 7. WS1 Phase 7 moved
`last_run` out to code-owned `state.json` for exactly this reason; the rest stayed behind.

---

## 1. Phases

### Phase 1 — Stop the ceremony (the money)

Three deletions from the tick's critical path (§0.7). No new mechanism, no new state, no
capability removed — each is independently shippable and revertable. Target: ~3.8 → ~1 LLM call
per tick, on **every** heartbeat task.

**Before-reading (2026-07-16, for the after-comparison):** 18 heartbeat rows/day, 1.60M input
tokens/day, ~3.8 LLM calls per tick, 214k on the worst no-op tick.

**1a. Drop the redundant `get_chat_history` instruction.** The prompt already holds *more* chat
than the tool returns (60 msgs/240 chars vs 50/200), better filtered (§0.7).

- [x] `heartbeat.py` — remove the `get_chat_history` sentence from the tick message; drop the
      now-unused `today_start`
- [x] `prompts/heartbeat.md` — point the daily-log rule at the injected
      `--- Today's chat with Roi ---` section; state plainly not to call the tool for it
- [x] Verify: assembled heartbeat prompt still carries the chat section + daily-log format rule
- [ ] Verify in production: `get_chat_history` disappears from heartbeat ticks in
      `tool_calls.jsonl` (was 1,229/1,290 ticks), and the daily log's `## Conversations (today)`
      section is still populated — now from the injected slice

**1b. Inject the due tasks' notes files.** ~325 tokens for all eight; replaces ~3 `read_memory`
round-trips at ~43k each.

- [ ] Parse the `notes:` path from each due task header (HEARTBEAT.md grammar already carries it)
- [ ] Inject due tasks' notes content into the heartbeat prompt, next to the filtered
      HEARTBEAT.md blocks — **due tasks only** (`prompts/heartbeat.md` forbids reading not-due
      tasks' notes; injecting them would contradict that rule)
- [ ] `prompts/heartbeat.md` step 1 — "read its notes file" becomes "its notes are below"
- [ ] Writes stay tool-driven: step 3 still calls `write_memory` to update notes. This removes
      the reads only
- [ ] Verify: prompt grows by ~a few hundred chars; `read_memory` per tick drops

**1c. Move the daily log off the hourly path.** Currently read + rewritten every tick (§0.7).
**Open question §3.1 — settle before coding.**

- [ ] Settle §3.1: dedicated end-of-day task vs. write-only-when-something-acted
- [ ] Remove the unconditional "after the task work, update today's daily log" rule from
      `prompts/heartbeat.md`
- [ ] Ensure the day's heartbeat activity still reaches the log — the heartbeat scope does
      **not** currently get today's notifications injected (only the user scope does), so an
      end-of-day writer needs `get_notification_history`: one call/day, not two round-trips/tick
- [ ] Verify: a day's log still lands, with both `## Conversations (today)` and
      `## Heartbeat Activity` populated

**Phase verify (all three):** from `turns.jsonl`, no new instrumentation (§0.5) — heartbeat
`llm_calls`/tick falls from ~3.8 toward ~1, and input tokens/day from ~1.60M.

### Phase 2 — Config seam (no behavior change)

New `config.py`; paths read from env, **defaulting to the exact current literals** so prod is
byte-identical and this is a no-op deploy.

```python
# config.py
MEMORY_DIR = os.getenv("JARVIS_MEMORY_DIR", "/app/jarvis_memory")
DATA_DIR   = os.getenv("JARVIS_DATA_DIR",   "/app/jarvis_data")
```

- [ ] Create `config.py` with `MEMORY_DIR`, `DATA_DIR` + derived sub-paths
- [ ] `agent.py` — `DB_PATH:155`, `_MEMORY_DIR:171`, `chat_log:243`, `notif_log:287`
- [ ] `tools/core/memory.py` — `MEMORY_DIR:20`
- [ ] `tools/core/history.py` — `_LOG_DIR:14`
- [ ] `heartbeat_state.py` — `HEARTBEAT_PATH:31`, `STATE_DIR:32`
- [ ] `tools/core/scheduling.py` — `EVENTS_PATH:15`
- [ ] `tools/fitness/fitness_tools.py` — `DB_PATH:12`
- [ ] Leave `gateway/channels/telegram/media_cache.py` alone (repo-relative, channel-owned)
- [ ] Leave the import-time `GOOGLE_API_KEY` guard (`agent.py:151`) as-is — tests pass a dummy
- [ ] Update the runtime-constants table in `DEVELOPMENT.md` (now env-overridable)
- [ ] Verify: `git diff` shows no default-value changes; assembled prompt byte-identical

*Verified feasible:* `import agent` succeeds with a dummy key in 1.2s with no network, and
`build_system_prompt` is directly callable (probed 2026-07-16).

**Enables:** `JARVIS_MEMORY_DIR=/tmp/scratch/mem JARVIS_DATA_DIR=/tmp/scratch/data python3 agent.py`
— the existing REPL, writing to a throwaway tree. Today that same REPL writes to prod;
`local_dev_test_01` is sitting in prod `threads.sqlite` with a 23KB checkpoint right now.

### Phase 3 — pytest + fixtures

- [ ] Add `pytest` to `requirements.txt` (none of pytest/mock/coverage is currently present)
- [ ] `tests/fixtures/memory/` — small, stable `SOUL.md`, `USER.md`, `MEMORY.md`, `HEARTBEAT.md`, `daily/`
- [ ] `tests/fixtures/data/` — `logs/`, `heartbeat/state.json`
- [ ] Root `conftest.py` — set `JARVIS_*` env + dummy `GOOGLE_API_KEY` **before** any import
      (module-level constants bind at import; conftest top-level runs first)
- [ ] Verify: `pytest` collects and runs with prod dirs provably untouched (stat mtimes before/after)

### Phase 4 — The tests that matter, in value order

- [ ] **Context-budget ceiling** — assert `len(system_prompt) + len(tool_schemas)` per scope
      stays under a threshold. Catches prompt/schema bloat. Seed at today's measured values
      (heartbeat ~33k chars, user ~21.5k chars) + headroom.
- [ ] **Checkpoint weight** — assert the serialized history a tick carries stays bounded.
      **This is the 63%.** Fails today (108,785 bytes); it is the acceptance criterion for
      unparking WS7.
- [ ] **Golden prompt snapshots** — both scopes against fixtures; catches silent content drops
      in any WS2/WS3 reorder.
- [ ] **Cache-prefix invariant** — `common_prefix(build(), build()) > 0.9 * len` — WS2's
      criterion. Mark `xfail` today (the clock is line 1, confirmed by probe); it flips green
      when WS2 lands.
- [ ] Extend `observability/usage.py` (or `scripts/`) with a skip-rate + per-tick-input report
- [ ] Decide how the report runs: weekly heartbeat task vs. cron vs. manual (§3.3)

### Phase 5 — Re-baseline the roadmap

Rewrite [CONTEXT_HANDLING_PLAN.md](CONTEXT_HANDLING_PLAN.md) against §0. It currently contains
claims measured to be false, and sequencing derived from them.

- [ ] Replace the "Measured baseline" section with §0's figures (66k → real numbers)
- [ ] Restate WS1's status honestly: gate correct, 22% skip, **not** "most hours"; the
      remaining spend was the task definition in §0.3, fixed in Phase 1 here
- [ ] Record Phase 1's before/after in the roadmap — the deterministic Arbox poll graduates from
      *Cleanups* to shipped
- [ ] Unpark **WS7** and promote it — it owns the 63% (§0.1)
- [ ] Correct or drop **WS3** — its target is unreachable by its method (§0.1)
- [ ] Re-scope **WS2/WS4** honestly: real but ~10%-slice wins; WS2 stays cheap and worth doing
- [ ] Cross-link this plan from both #18 and archive #33

---

## 2. Files touched per phase

| Phase | Files |
|---|---|
| 1 | `heartbeat.py`, `prompts/heartbeat.md`, `agent.py` (notes injection), `heartbeat_state.py` (parse `notes:` path) |
| 2 | **new** `config.py`; `agent.py`, `tools/core/{memory,history,scheduling}.py`, `heartbeat_state.py`, `tools/fitness/fitness_tools.py`, `DEVELOPMENT.md` |
| 3 | **new** `conftest.py`, `tests/fixtures/**`; `requirements.txt` |
| 4 | **new** `tests/test_{context_budget,checkpoint_weight,prompt_assembly,cache_prefix}.py`; `observability/usage.py`, **new** `scripts/cost_report.py` |
| 5 | `docs/plans/CONTEXT_HANDLING_PLAN.md` |

---

## 3. Open questions to settle before coding

1. **Daily-log cadence (Phase 1c).** Two shapes. (a) A dedicated end-of-day task
   (`every 24h | due: 22:00-23:59`) — clean, but if the service is down in that window the day's
   log is lost, and the writer must reconstruct the day from the injected chat slice +
   `get_notification_history`. (b) Write only when a task actually acted — cheaper on quiet
   days, but the log then lags the conversation it is supposed to narrate. **Lean (a)**, with
   the 2-tick window as the outage cushion.
2. **Is the daily log still worth its cost at all?** Its original job was cross-scope awareness,
   and it is no longer the sole bridge — live chat/notification slices are injected into both
   scopes directly (CLAUDE.md, "Live cross-scope awareness"). What remains is its value as a
   permanent narrative archive beyond the 50-message window, which is real (the
   `memory-index-audit` task treats daily logs as undeletable). Worth an explicit decision
   rather than inheriting the hourly rewrite by default.
3. **Cost report cadence (Phase 4).** Heartbeat task, cron, or manual? A heartbeat task costs an
   LLM turn to report on LLM spend, which is a little self-defeating; cron + Telegram push via
   the Outbox may be cleaner.
4. **Budget thresholds (Phase 4).** Seed at measured + headroom, or at a target? Seeding at
   current values locks in today's bloat as acceptable; seeding at a target means red on day 1.
   Recommend: seed at current, ratchet down as WS7 lands.
5. **CI.** Worth adding `.github/workflows/test.yml` (pytest + typecheck, no secrets, no deploy)
   once Phase 4 exists? Cheap, and it makes the tests gate merges. Deferred by default.

---

## 3b. Deferred to #20 — deterministic wake conditions

Earlier drafts made a deterministic Arbox "probe" the first phase. Deferred, but the analysis
is worth keeping — it is the seed of **#20 (on-demand/event-driven heartbeat)**, which the WS1
gating plan already lists as a non-goal deferred elsewhere.

**The general capability**, as framed by the owner: wake Jarvis on deterministic conditions —
*before practice* (T-minus a known event) or *on a diff* (fetched state changed). These are two
different mechanisms, and conflating them is what made the "probe" look simple:

- **T-minus before a known event** — needs an event already on the books; scheduler-shaped.
  Note `manage_reminder` + APScheduler already does exact-time wake with restart persistence and
  past-due handling; it just notifies the owner rather than waking the agent.
- **Diff on fetched state** — *discovers* events; poll-shaped.

This also reopens a decision the gating plan made deliberately (§2 of that plan): OpenClaw has
**two** mechanisms — heartbeat (fixed-interval poll) and cron (exact-time wake) — and Jarvis
chose windowed-poll only, omitting OpenClaw's `nextCheck` on those grounds. "Wake before
practice" reopens it. That deserves its own design, not a crossfit-shaped patch.

**Constraints discovered here that any #20 design must respect:**

1. **The probe cannot call `fetch_upcoming_arbox_classes()`.** It is not a read: it upserts the
   workouts DB and calls `_purge_dropped_arbox_classes`, which **deletes** the dropped rows and
   returns them to build the "Removed N class(es)" notice. That notice is **one-shot**. A probe
   that calls it, then wakes the LLM to call it again, gets no notice the second time — the
   dropped-class flow (delete stale reminder, tell the owner, re-check quota) silently never
   fires. Any probe needs a pure-read split.
2. **Probe state must be code-owned**, not `heartbeat/crossfit_check.md`. Same reasoning as WS1
   Phase 7 (`last_run` → `state.json`).
3. **Stamp-after-success, or a transient error becomes permanent.** Probe sees a new class →
   escalates → writes state → the LLM turn times out at 90s (`heartbeat.py`, this happens) →
   next tick sees no diff → skips forever; the class never gets a reminder. Reuse the existing
   discipline: only acted tasks advance state, after delivery settles.
4. **The <4h briefing is time-shaped, not diff-shaped.** A pure schedule diff never fires it —
   the schedule is unchanged, the clock moved.

---

## 4. What this does not fix

Phases 2–4 make drift *visible* and make the roadmap's structural changes *checkable*. They do
not by themselves reduce a single token. The token wins are Phase 1 (the leak, ~800k/day) and
then Phase 5's re-pointed roadmap — specifically WS7, which owns the 63% (§0.1). This plan's
value is that those get chosen on evidence, and that the next "verified in production" claim is
falsifiable.

---

## 5. Verification log

Deploys are manual (the owner restarts; see CLAUDE.md), so every "after" reading lands hours or
days after the commit. Record them here rather than trusting recall — WS1 was called "verified
in production" from memory, and §0.2 is what that was worth.

**Re-measure with** `observability.usage.summarize_usage(group_by="scope")` for the token rows;
join `turns.jsonl` ↔ `tool_calls.jsonl` on `turn_id` for the per-tool counts (`scripts/trace.py`
does the join for a single turn).

### Baseline — measured 2026-07-16, pre-deploy

| Metric | Value |
|---|---|
| heartbeat rows/day (model ticks) | 18 |
| heartbeat input tokens/day | 1.60M |
| LLM calls per tick | ~3.8 |
| worst no-op tick | 214,008 input / 5 calls |
| `get_chat_history` on heartbeat ticks | 1,229 / 1,290 |
| `read_memory` per tick | 3.1 |
| `write_memory` per tick | 2.3 |
| gate skip rate | 22% (35 skipped / 127 ran, 7d journal) |
| heartbeat share of all input | 83% (88.0M vs 17.9M user) |

### Phase 1a — drop the redundant `get_chat_history` instruction

Committed: `a8e1b70` · Deployed: 2026-07-20 16:05 UTC

- [x] ~~`get_chat_history` gone from heartbeat ticks~~ — **failed.** 5 of the 7 post-deploy
      ticks still call it (07-20 17:05, 18:05; 07-21 03:05, 04:05, 05:05).
- [x] daily log's `## Conversations (today)` still populated — yes, `daily_2026-07-20.md`
      has post-deploy entries (17:38).
- [ ] LLM calls/tick down from ~3.8 — no movement: 5.4 → 5.0 mean (n=7, error turns
      excluded). Mean input/tick 102.5k → 114.8k, i.e. no measurable win.

**The premise was wrong.** The checkbox above assumed the only thing asking for the call was
the tick-message instruction this phase removed, with in-context imitation as the fallback
explanation. Neither was the driver. `get_chat_history`'s own docstring carried
`Example: '2026-05-08T00:00:00Z' for today's conversations only` — bound into every tick via
`llm.bind_tools()`, advertising exactly the redundant use. The model's calls reproduce that
example's shape (`since='2026-07-21T00:00:00Z'`, today at midnight, `Z`-suffixed), not the
removed instruction's (`+03:00`, Israel midnight).

Two things this cost, both worth generalising:

- Prompt prose was edited to change tool-usage behaviour. CLAUDE.md already states tool usage
  is driven by docstrings; the phase was scoped without grepping the tool surface for what
  else mentioned the call. `grep -rn "get_chat_history" prompts/ agent.py heartbeat.py` returns
  nothing — the docstring was the *only* remaining driver, and was never in scope.
- The `Z` example was also a live correctness bug: days here are Israel time, so a UTC-midnight
  bound silently drops 00:00–03:00 Israel from every fold-in.

Fixed at the docstring instead (`tools/core/history.py`). Re-measure after the next deploy;
an explicit negative instruction in `heartbeat.md` remains unearned.

- [ ] `get_chat_history` per heartbeat tick, after the docstring fix (was 0.7/tick post-a8e1b70)
- [ ] mean input/tick vs the 114.8k reading above

### Phase 1b — inject due tasks' notes files

Committed: _pending_ · Deployed: _pending_

- [ ] `read_memory` per tick down from 3.1
- [ ] heartbeat prompt grew only a few hundred chars
- [ ] notes files still being *written* (step 3 unaffected — this removes reads only)

### Phase 1c — daily log off the hourly path

Committed: _pending_ · Deployed: _pending_

- [ ] `write_memory` per tick down from 2.3
- [ ] a full day's log still lands, with both `## Conversations (today)` and
      `## Heartbeat Activity` populated
