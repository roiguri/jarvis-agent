# Cost Bug, Testing & the Feedback Loop

**Issue:** TBD (new). Related: #33 (archive) = #18 (origin mirror) — "Reduce token spend" umbrella.
**Branch:** `fix/heartbeat-cost` (proposed).
**Companion:** [CONTEXT_HANDLING_PLAN.md](CONTEXT_HANDLING_PLAN.md) — this plan re-baselines it (Phase 6).
**Date:** 2026-07-16.
**Goal:** stop an active token leak, then build the loop that would have caught it. Today there
is no test suite, no CI, no way to run the agent without touching production state, and no
regression check on the metric the whole context roadmap optimizes. A workstream shipped, was
documented as "verified in production", and the number it targeted moved the wrong way for
three days unnoticed.

**Non-goals (deliberately deferred):** CD / auto-deploy on merge (the owner does every restart
by choice; the box is behind default-deny on an LXC — a self-hosted runner is real work and
blocks nothing). A second Telegram bot token + staging systemd instance (most of the context
roadmap's risk is structural and needs no live Telegram loop; the config seam in Phase 3 is the
prerequisite if we ever want it). LLM-judge eval frameworks (exactly one workstream needs a
judge — see §0.6).

---

## Manager summary

**Problem.** Two problems, one root cause. (1) **An active leak:** one misconfigured heartbeat
task forces a full LLM tick 16×/day to poll an Arbox schedule that is currently *empty* —
roughly 800k input tokens/day to confirm nothing changed (§0.3). (2) **No feedback loop:** every
test runs against the production agent, writing into prod memory, prod `threads.sqlite`, and
prod `turns.jsonl` — the last being the *measurement instrument* for the context roadmap, so
testing corrupts the data used to evaluate what's being tested. Nothing is automatically
checked, so drift is invisible. The root cause of (2) is that paths are frozen literals in 8
modules: nothing can point at a fixture directory, so no test can exist.

**What the research found.** Measuring instead of trusting the roadmap's baseline (§0) showed
the context roadmap is aimed at ~10% of per-call input, and its shipped workstream
underdelivered ~3x against a "verified in production" claim. §0 is the reason this plan exists
and should be read first.

**What ships.** Six phases. The money is Phases 1–2; the rest makes it stay fixed.

| Phase | What lands | Impact |
|---|---|---|
| 1 | Telemetry for gated ticks | One line. **The meter** — makes Phase 2 measurable |
| 2 | Deterministic Arbox poll — the crossfit fix | **The money.** ~800k input tok/day (§0.3) |
| 3 | `config.py` seam — env-driven paths, defaulting to today's literals | No behavior change; byte-identical prod. Unblocks 4–5 |
| 4 | pytest + fixture memory/data tree + conftest | A throwaway Jarvis that cannot touch prod |
| 5 | Four tests, in value order (budget ceiling, checkpoint weight, golden prompts, cache prefix) | The regression checks that would have caught the drift |
| 6 | Re-baseline `CONTEXT_HANDLING_PLAN.md` against §0 | The roadmap starts pointing at the real 63% |

**Ordering rationale.** Phase 1 before Phase 2 because gated ticks are currently invisible to
telemetry (§0.5) — without the meter, the fix can only be verified by grepping the journal by
hand. Phase 1 is one line and zero risk. Phases 3–5 follow the money rather than precede it:
the leak is costing tokens every hour, and the harness reduces zero tokens by itself (§4).

**Risk posture.** Phase 1 is additive telemetry. Phase 2 is the only phase that changes agent
behavior — it must fail open (an Arbox error runs the LLM, matching today's behavior) and is
one commit to revert. Phase 3 is a no-op refactor (defaults preserve every current literal).
Phases 4–5 add files only. Phase 6 is documentation.

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

The fix is named in the roadmap's **Cleanups** section, at the bottom, deferred: make the Arbox
poll deterministic and escalate to the LLM only when the schedule differs from
`last_known_schedule`. It is worth more than WS2+WS3+WS4 combined. It is Phase 2 here.

### 0.4 Consequences for spend

- Heartbeat is still **83% of all input tokens** (88.0M vs 17.9M user) — essentially unchanged
  from the 84% that motivated WS1.
- Per-tick input **did not fall** after WS1. It drifted up: ~68k avg in early July, ~90–114k
  recently, with a no-op tick on 2026-07-16 at **214k**.
- `turns.jsonl` totals: 1,307 heartbeat turns / 4,949 LLM calls / 88.0M input (35% cache-read);
  323 user turns / 795 calls / 17.9M input (23% cache-read).

### 0.5 Why nobody noticed — the loop is open

`heartbeat.py:59-60` returns **before** any telemetry is recorded:

```python
if not due:
    logger.info("Heartbeat: nothing due — skipping model turn")
    return
```

A gated tick leaves **no row in `turns.jsonl`**. The skipped ticks — the entire point of WS1 —
are invisible to the instrument used to verify WS1. Skip rate is only recoverable by grepping
the journal, which nothing does. `observability/usage.py` exists and is correct; it just isn't
run.

**This is the finding that orders this plan.** The gap isn't evals and isn't isolation. There is
no loop at all: no test, no CI, no alert, no scheduled report — and a metric regressed ~68%
while the doc said "verified".

### 0.6 Most of the roadmap's risk needs no LLM to evaluate

Worth stating, since "prompt changes need behavioral evals" was the opening premise. WS2 is a
reordering; its criterion is "the first N bytes are stable across calls" — an assert. WS4 is
truncation; "marker appears, normal files pass byte-for-byte" — an assert. WS3's risk is silent
content-drop — a golden snapshot diff. WS5's risk is "does the model call the tool" — a
structural assert over `tool_calls.jsonl`.

Exactly one thing needs a judge: **WS3's briefing voice quality** (the roadmap itself names
"blander/wronger notification text" as the failure mode). One narrow judge over a handful of
fixtures — not a framework. Build that last, if WS3 survives §0.1 at all.

---

## 1. Phases

### Phase 1 — Make the meter work (one line)

Gated ticks are invisible (§0.5), so Phase 2's win can't be measured. Fix that first.

- [ ] Record a telemetry row for **gated** ticks at `heartbeat.py:59`, before the `return`
- [ ] Include `llm_calls=0`, `input_tokens=0`, and the empty due list so a skip is
      distinguishable from a model tick in `turns.jsonl`
- [ ] Confirm `observability/usage.py` tolerates zero-call rows (no div-by-zero in averages)
- [ ] Verify: skip rate derivable from `turns.jsonl` alone, matching the journal grep
      (35/162 over the 7 days to 2026-07-16) — this is the **before** reading for Phase 2

### Phase 2 — Stop the bleed: deterministic Arbox poll (the money)

Escalate `crossfit-sync-and-remind` to the LLM only when the fetched schedule actually differs
from `last_known_schedule`. Design sketch — **§3.1 is an open question, settle before coding:**

Keep `heartbeat_state.any_due` pure (it reads `state.json` + parses markdown; no network). Add
the probe as an **additive filter in `heartbeat.py` after the gate returns due tasks**, where
timeout and error handling already live: if every due task has a probe and every probe reports
"unchanged", skip the model turn.

- [ ] Settle the design (§3.1) — probe-after-gate vs. task-grammar `probe:` field
- [ ] Deterministic probe calling the existing `fetch_upcoming_arbox_classes()`
- [ ] Compare against `last_known_schedule`; unchanged → task not due this tick
- [ ] **Fail open** — Arbox error/timeout runs the LLM (today's behavior; the gate already
      fails open per §0.2, keep the property)
- [ ] Preserve the escalation paths the task body defines: new class, class <4h away, dropped
      registration. A probe that only diffs the list must not swallow the "class is today,
      brief me" trigger — that is time-based, not diff-based
- [ ] Verify: skip rate (Phase 1's meter) rises from 22% toward the ~33% window ceiling, and
      further once the poll stops pinning the gate; per-day heartbeat input falls from ~1.6M
- [ ] Verify: register for a class in Arbox → next tick escalates and briefs correctly

### Phase 3 — Config seam (no behavior change)

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

### Phase 4 — pytest + fixtures

- [ ] Add `pytest` to `requirements.txt` (none of pytest/mock/coverage is currently present)
- [ ] `tests/fixtures/memory/` — small, stable `SOUL.md`, `USER.md`, `MEMORY.md`, `HEARTBEAT.md`, `daily/`
- [ ] `tests/fixtures/data/` — `logs/`, `heartbeat/state.json`
- [ ] Root `conftest.py` — set `JARVIS_*` env + dummy `GOOGLE_API_KEY` **before** any import
      (module-level constants bind at import; conftest top-level runs first)
- [ ] Verify: `pytest` collects and runs with prod dirs provably untouched (stat mtimes before/after)

### Phase 5 — The tests that matter, in value order

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

### Phase 6 — Re-baseline the roadmap

Rewrite [CONTEXT_HANDLING_PLAN.md](CONTEXT_HANDLING_PLAN.md) against §0. It currently contains
claims measured to be false, and sequencing derived from them.

- [ ] Replace the "Measured baseline" section with §0's figures (66k → real numbers)
- [ ] Restate WS1's status honestly: gate correct, 22% skip, **not** "most hours"; the
      remaining spend was the task definition in §0.3, fixed in Phase 2 here
- [ ] Record Phase 2's before/after in the roadmap — the deterministic Arbox poll graduates from
      *Cleanups* to shipped
- [ ] Unpark **WS7** and promote it — it owns the 63% (§0.1)
- [ ] Correct or drop **WS3** — its target is unreachable by its method (§0.1)
- [ ] Re-scope **WS2/WS4** honestly: real but ~10%-slice wins; WS2 stays cheap and worth doing
- [ ] Cross-link this plan from both #18 and archive #33

---

## 2. Files touched per phase

| Phase | Files |
|---|---|
| 1 | `heartbeat.py`, `observability/telemetry.py` (if a zero-call row needs a helper) |
| 2 | `heartbeat.py`, `tools/fitness/fitness_tools.py` (probe entry point), possibly `heartbeat_state.py` (§3.1), `/app/jarvis_memory/HEARTBEAT.md` |
| 3 | **new** `config.py`; `agent.py`, `tools/core/{memory,history,scheduling}.py`, `heartbeat_state.py`, `tools/fitness/fitness_tools.py`, `DEVELOPMENT.md` |
| 4 | **new** `conftest.py`, `tests/fixtures/**`; `requirements.txt` |
| 5 | **new** `tests/test_{context_budget,checkpoint_weight,prompt_assembly,cache_prefix}.py`; `observability/usage.py`, **new** `scripts/cost_report.py` |
| 6 | `docs/plans/CONTEXT_HANDLING_PLAN.md` |

---

## 3. Open questions to settle before coding

1. **Probe placement (Phase 2).** Additive filter in `heartbeat.py` after the gate (keeps
   `any_due` pure, puts network I/O where timeouts already live) vs. a `probe:` field in the
   HEARTBEAT.md task grammar (more general, but makes the gate do network I/O and requires a
   grammar + validation change in `manage_heartbeat_task`). **Recommend the former** — narrower,
   and generalizable later if a second task needs it.
2. **Does the probe belong to the fitness skill or the heartbeat?** The diff logic is
   Arbox-specific; the skip decision is heartbeat-generic. Suggest: probe function lives with
   the fitness tools, heartbeat calls it through a small registry.
3. **Cost report cadence (Phase 5).** Heartbeat task, cron, or manual? A heartbeat task costs an
   LLM turn to report on LLM spend, which is a little self-defeating; cron + Telegram push via
   the Outbox may be cleaner.
4. **Budget thresholds (Phase 5).** Seed at measured + headroom, or at a target? Seeding at
   current values locks in today's bloat as acceptable; seeding at a target means red on day 1.
   Recommend: seed at current, ratchet down as WS7 lands.
5. **CI.** Worth adding `.github/workflows/test.yml` (pytest + typecheck, no secrets, no deploy)
   once Phase 5 exists? Cheap, and it makes the tests gate merges. Deferred by default.

---

## 4. What this does not fix

Phases 3–5 make drift *visible* and make the roadmap's structural changes *checkable*. They do
not by themselves reduce a single token. The token wins are Phase 2 (the leak, ~800k/day) and
then Phase 6's re-pointed roadmap — specifically WS7, which owns the 63% (§0.1). This plan's
value is that those get chosen on evidence, and that the next "verified in production" claim is
falsifiable.
