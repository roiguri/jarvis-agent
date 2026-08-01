# Context Handling Roadmap

**Issue:** #18 (origin mirror) = #33 (archive) — "Reduce token spend" umbrella. Also touches archive #54 (compaction).
**Date:** 2026-07-09 · **re-baselined 2026-07-30** against measurements taken 2026-07-16.
**Inputs:** telemetry (`observability/usage.py`), OpenClaw docs + source dive (see [HEARTBEAT_GATING_PLAN.md §2](archive/HEARTBEAT_GATING_PLAN.md)), Nous Research `hermes-agent` docs, archive issue #54 staged analysis.
**Companions:**
- [HEARTBEAT_GATING_PLAN.md](archive/HEARTBEAT_GATING_PLAN.md) — WS1 below; shipped & verified 2026-07-13, archived.
- [TEST_HARNESS_PLAN.md](TEST_HARNESS_PLAN.md) — the **verification instrument** for this roadmap. Three of its four tests are the acceptance criteria for WS2, WS4 and WS7; unstarted.

---

## Re-baseline note (2026-07-30)

This doc's original baseline was measured over the 14 days to 2026-07-09 and was **roughly 2x
off** on per-tick spend. A re-measurement on 2026-07-16 (live host, 1,630 turns, the checkpoint
DB, the service journal) changed three of its conclusions:

1. **WS2/WS3/WS4 all target ~10% of per-call input.** The 63% is message history; another 18% is
   tool results accruing within the turn. That is WS7 — which this doc had **parked**.
2. **WS3's stated target is unreachable by its method**, not merely ambitious (§0.1 arithmetic
   below). Rescoped, not deleted.
3. **WS1's "most hours: no LLM call at all" did not hold** — the gate is correct and never failed
   open, but the observed skip rate is **22%**, because one task's window pins it open. The
   remaining spend was a task definition, plus per-tick ceremony that no workstream owned. That
   ceremony is now **WS8**.

The measurements, the heartbeat-tick work they motivated, and the test harness they argued for
were previously held in a single doc named `TESTING_AND_FEEDBACK_LOOP_PLAN.md` — a name that
described only its unstarted third, and which this roadmap never linked. Its measurement and
heartbeat-cost material is folded in here; its harness material is
[TEST_HARNESS_PLAN.md](TEST_HARNESS_PLAN.md).

**Dollar figures.** All `$` amounts in this doc were recomputed after `MODEL_PRICES` was found to
price `gemini-3-flash-preview` 6.5x too low (see
[COST_TELEMETRY_PLAN.md](archive/COST_TELEMETRY_PLAN.md) slice A). Token and turn counts were
always correct. Any pre-2026-07-29 cost figure quoted elsewhere is wrong; re-derive with
`summarize_usage` rather than copying one forward.

---

## Manager summary

**Problem.** Jarvis's context handling is structurally expensive and has no recall beyond exact-name
file reads. Heartbeat remains **83% of all input tokens** (88.0M vs 17.9M user, to 2026-07-16), and
WS1 did not move per-tick input — it drifted *up*, from ~68k in early July to 90–114k, with a
no-op tick at **214k**. Inside a tick, 63% of each call is re-sent message history and 18% is
accrued tool results; the whole system prompt is 10%. A representative tick spends **9 tool calls
and 5 sequential LLM round-trips to make one 785ms API call** — the rest is bookkeeping the harness
asks for and then discards. Separately, the system prompt's first line is a per-minute timestamp
rebuilt on every LLM call, so a turn's own notion of "now" moves mid-reasoning — a correctness
problem before a cost one (WS2, rewritten 2026-08-01; the cache loss it also causes is ~5% of input,
not the 64%-of-turns figure this paragraph previously carried). Memory recall still requires knowing
a file's exact name.

**Plan.** Eight workstreams, ordered below by **measured impact**. WS1 is done. WS8 and WS7 own the
spend and lead; WS2 is cheap and multiplies everything; WS3 is rescoped to what its method can
actually deliver; WS5/WS6 are a capability track.

| Rank | # | Workstream | Attacks | Size | Expected effect |
|---|---|---|---|---|---|
| 1 | **WS8** | **Heartbeat tick ceremony** | round-trips per tick | small (2 slices) | ~3.8 → ~1 LLM call/tick, on **every** task; 1a shipped & GREEN |
| 2 | **WS7** | **Conversation compaction** — *unparked* | the 63% + 18% | medium (cheap half is ~20 lines) | Per-call input; owns the largest single line item |
| 3 | WS2 | **Time grounding** + cache-stable prompt | a turn's "now" moving mid-turn; the sliding 50-cap | small-medium | Correctness first (stable per-turn clock). Its cost case is **item 4, trim hysteresis** — ~$6.65/mo; the clock itself is ~$1/mo. See WS2 §2.1 |
| 4 | WS4 | Bootstrap context budget | unbounded prompt growth | small | Backstop, not a win: caps injected copies |
| 5 | WS5 | Memory & history search | recall capability | medium | "What did we discuss last week?" becomes answerable |
| 6 | WS6 | Memory size pressure | long-term prompt creep | small | USER.md/MEMORY.md stay curated |
| 7 | WS3 | Heartbeat light context — **rescoped** | ~10% slice, heartbeat only | small | A few % per call; **not** the 4x its original target claimed |
| — | WS1 | Heartbeat gating + windows + self-authoring | the 83% | 9 phases | **Done 2026-07-13.** Gate correct, 22% skip — see WS1 below |

**Sequencing.** WS8 first — pure deletion plus injection, no new mechanism, and its win is readable
in existing telemetry. Then WS7's cheap half (tool-result pruning). WS2 is no longer "any time": it
shares `_add_and_trim` with WS7 and its clock work needs re-verification first (see WS2). WS4 can
land any time. WS5/WS6 are independent. WS3 last, if at all.

**Risk posture.** Everything is additive or a reordering, independently shippable and revertable,
and checkable against `turns.jsonl` (per-turn input/output/cache-read by scope). **Checkable is not
checked:** the previous round of this roadmap was called "verified in production" from memory and
was wrong for three days in plain sight (§0.2, §0.5). Record before/after readings in the PR, and
prefer an assert in [TEST_HARNESS_PLAN.md](TEST_HARNESS_PLAN.md) over a claim.

---

## 0. Measured baseline (2026-07-16, live host)

Supersedes the original 14-day baseline. All figures measured against `turns.jsonl` (1,630 turns),
`threads.sqlite`, and the service journal.

### 0.1 Where a heartbeat call's input actually goes

Per-call breakdown for a no-op tick on 2026-07-16 (214,008 input over 5 LLM calls ≈ 42.8k/call):

| Component | Tokens | Share | Measured how |
|---|---|---|---|
| **Message history** (50-msg checkpoint) | **~27k** | **~63%** | `heartbeat` row in `threads.sqlite` = 108,785 bytes |
| Tool results accruing within the turn | ~7.5k | ~18% | residual (observed/call − prompt − schemas − history) |
| System prompt | ~4.5k | ~10% | `build_system_prompt("heartbeat", set(), due_tasks=[])` = 18,118 chars |
| Tool schemas (12 bound tools) | ~3.8k | ~9% | serialized `args_schema` over `get_tools(scope="heartbeat")` = 15,045 chars |

**WS2, WS3 and WS4 all target the system prompt** — the ~10% slice. The 63% + 18% is WS7.

**This is why WS3 is rescoped.** WS3 aimed at "66k → ≤15k per tick". A tick is ~5 calls. Deleting
the *entire* system prompt — SOUL, AGENTS, USER, framing, skills, everything — leaves ~38k/call
≈ 190k/tick. The target cannot be hit by the method proposed, at any level of execution quality.

### 0.2 Totals and trend

- Heartbeat: 1,307 turns / 4,949 LLM calls / **88.0M input** (35% cache-read).
- User: 323 turns / 795 calls / **17.9M input** (23% cache-read).
- Heartbeat share: **83%** — essentially unchanged from the 84% that motivated WS1.
- Per-tick input **did not fall** after WS1: ~68k avg in early July → 90–114k, worst no-op 214k.
- For reference, the original 14-day window (to 2026-07-09, dollars restated 2026-07-29): 408 turns,
  27.1M input, 682.6k output, **$11.47** — of which heartbeat was **$9.57** (83%) with 88%
  `[NO_ACTION]`. Roughly $25/month on `gemini-3-flash-preview`; the same workload on
  `gemini-3.6-flash` lands near $75/month before any growth.

### 0.3 WS1's gate is correct; the task definitions don't let it skip

Service journal, 7 days to 2026-07-16:

```
skipped ticks (gate fired): 35
ran the model:             127
gate errors (fail-open):     0
```

**A 22% skip rate, not "most hours."** The gate works and never failed open. The cause is one task:

```
crossfit-sync-and-remind | every 1h | due: 06:00-22:00
```

WS1 Phase 6 shipped as "all 8 tasks windowed", and technically that is true — but **a 16-hour
window on an hourly cadence is due 16 times a day, every day.** The gate's mathematical ceiling is
the 8 night hours (~33%); observed is 22% because other tasks straddle the night edge.

What that tick does, per `heartbeat/crossfit_check.md`: `last_known_schedule: []` — **the schedule
is empty.** It wakes a ~5-call LLM turn to fetch an empty list from Arbox and conclude nothing
changed. On 2026-07-16 it was the **only** due task on 7 of 12 sampled ticks; at ~114k avg
input/tick that is **~800k input tokens/day spent confirming an empty list is still empty.**

The gating plan's own §0 predicted this — *"Because two tasks are `every 1h`, something is due every
hour — so a cadence-only gate skips almost nothing with this mix"* — and Phase 6's windows were the
designated fix. For `crossfit-sync` the window is too wide to be one. Narrowing the **gate's**
open-rate is deferred to #20 (§4 below); WS8 makes an open gate cheap instead, which is smaller,
safer, and helps all eight tasks.

### 0.4 Why nobody noticed — the data was there; nothing reads it

`heartbeat.py` returns before recording telemetry, so a gated tick writes no row to `turns.jsonl`.
That is **not** an instrumentation gap: fewer rows *is* the measurement. The gate's arrival is
plainly legible in heartbeat rows/day —

```
07-08: 24    07-09: 24    07-10: 21  ← WS1 ships
07-11: 18    07-15: 18               ← ~6 ticks/day now skipped
```

— and agrees with the journal grep. Total heartbeat input/day never fell, which is equally legible.
Every number needed to catch this sat in `turns.jsonl` the whole time. `observability/usage.py`
exists, is correct, and **nothing runs it**.

The gap isn't instrumentation, evals, or isolation: nothing ever *reads the instrument* — no test,
no CI, no alert, no scheduled report. A metric regressed ~68% in plain sight while this doc said
"verified". That finding is the entire argument for
[TEST_HARNESS_PLAN.md](TEST_HARNESS_PLAN.md); adding more meters would not have helped.

*(Narrow residual gap, hygiene not blocker: an absent row can mean "gate skipped" or "service was
down" — indistinguishable. Worth tagging a gated tick eventually; it belongs with the harness's
reporting work.)*

### 0.5 Most of this roadmap needs no LLM to evaluate

Worth stating, since "prompt changes need behavioral evals" was the opening premise. WS2 is a
reordering — its criterion is "the first N bytes are stable across calls", an assert. WS4 is
truncation — "marker appears, normal files pass byte-for-byte", an assert. WS3's risk is silent
content-drop — a golden snapshot diff. WS5's risk is "does the model call the tool" — a structural
assert over `tool_calls.jsonl`. WS7's criterion is a bounded checkpoint — an assert.

Exactly one thing needs a judge: **WS3's briefing voice quality** (its named failure mode is
"blander/wronger notification text"). One narrow judge over a handful of fixtures — not a
framework. Build that last, if WS3 survives §0.1 at all.

---

## Research digest: what the references do

Full OpenClaw source-dive lives in [HEARTBEAT_GATING_PLAN.md §2](archive/HEARTBEAT_GATING_PLAN.md).
The context-relevant mechanisms across both references, against Jarvis today:

| Mechanism | OpenClaw | hermes-agent | Jarvis today |
|---|---|---|---|
| Cache-stable prompt | Explicit stable-prefix/dynamic-suffix boundary; **timezone only, no live clock** in prompt (time via tool) | Three tiers (stable → context → volatile); ephemeral material goes in the **user message**, never system prompt; memory injected as **frozen snapshots** | Timestamp is the first line, rebuilt per call — cross-turn cache ≈ 0 |
| Injected-file budgets | 20k chars/file, 60k total, truncation warnings; file intact on disk | 20k chars, 70/20 head/tail split + injection-scan | Uncapped (SOUL, USER, daily logs, HEARTBEAT.md) |
| Heartbeat cost | Only-due-task injection, skip-if-nothing-due, `lightContext` (~100k → 2–5k), `isolatedSession` | Cron jobs in isolated sessions; **cron tools disabled inside cron runs** | Due-only injection shipped (WS1); per-tick ceremony remains (→ WS8) |
| Memory recall | `memory_search`: hybrid vector + BM25 over chunked files | Bounded in-context memory + **FTS5 full-text search over all session history** (~20ms, no LLM) | `list_memory` + `read_memory` by exact name; no search |
| Memory size control | Curated MEMORY.md + opt-in "dreaming" consolidation | **Hard caps** (MEMORY.md ~2,200 chars, USER.md ~1,375) + `[67% full]` gauge in prompt + write-errors that force same-turn consolidation | No size pressure anywhere |
| History compaction | Summarize-old near context limit + **pre-compaction memory flush** (silent save-to-memory turn); separate lighter **tool-result pruning** (head+tail kept, cache-TTL-aware) | Head/torso/tail: protect last ~20 messages / ~20k tokens, summarize the middle at 50% of window | Hard trim at 50 messages (`_add_and_trim`), older context discarded |

---

## WS8 — Heartbeat tick ceremony (rank 1)

**Why.** §0.1's tick is *9 tool calls and 5 sequential LLM round-trips to make one 785ms API call*
(`scripts/trace.py`):

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

Not crossfit-specific. Across **1,290** heartbeat turns calling the Arbox sync — **86.8M input
tokens, 98% of all heartbeat spend** — the per-tick averages were:

| Tool | Calls/tick | Verdict |
|---|---|---|
| `read_memory` | 3.1 | notes files + daily log — **injectable** |
| `write_memory` | 2.3 | notes file + daily log — daily log is **hourly for no reason** |
| `get_chat_history` | 0.95 | **redundant** — the data is already in the prompt |
| `fetch_upcoming_arbox_classes` | 1.0 | the actual work |

Each of those is a *sequential* round-trip re-sending the full ~43k context. Target: **~3.8 → ~1
LLM call per tick**, on every task. Every slice is a deletion or an injection — no new mechanism,
no new state, no capability lost, each independently revertable.

### WS8a — Drop the redundant `get_chat_history` instruction — **SHIPPED, GREEN**

`build_system_prompt` already injects `--- Today's chat with Roi ---` (`_load_recent_user_chat`:
**60** messages, 240-char cap, heartbeat-thread excluded). The tool returned **50** messages at a
**200**-char cap with **no thread filter** — strictly *less* data, mixed with heartbeat noise.

- [x] `heartbeat.py` — instruction removed from the tick message; unused `today_start` dropped
- [x] `prompts/heartbeat.md` — daily-log rule points at the injected section
- [x] Root cause was the **tool's own docstring**, not the prompt prose — see the verification log
      in [TEST_HARNESS_PLAN.md](TEST_HARNESS_PLAN.md) for the full episode and its lesson
- [x] **Verified 2026-07-28:** 1 call / 36 ticks = **0.03/tick** vs a 0.95 baseline. Daily log's
      `## Conversations (today)` still populated, now from the injected slice

### WS8b — Inject the due tasks' notes files

All eight `heartbeat/*.md` files together are **1,300 bytes (~325 tokens)**. The tick spends ~3
`read_memory` round-trips at ~43k tokens each to read them. **The data is ~100x cheaper than asking
for it.** HEARTBEAT.md task headers already name each file (`notes: heartbeat/crossfit_check.md`),
and the prompt already injects only the due tasks' blocks — so this is mechanical.

- [ ] Parse the `notes:` path from each due task header (`heartbeat_state.py`; the grammar already carries it)
- [ ] Inject due tasks' notes content next to the filtered HEARTBEAT.md blocks — **due tasks only**
      (`prompts/heartbeat.md` forbids reading not-due tasks' notes; injecting them would contradict it)
- [ ] `prompts/heartbeat.md` step 1 — "read its notes file" becomes "its notes are below"
- [ ] Writes stay tool-driven: step 3 still calls `write_memory`. This removes **reads** only
- [ ] Verify: prompt grows by a few hundred chars; `read_memory`/tick falls from 3.1 (2.36 as of 07-28)

### WS8c — Move the daily-log write off the hourly path

`prompts/heartbeat.md` says *"After the task work, update today's daily log… If the file already
exists, read it first."* Not once a day — **every tick**. An hourly check that finds nothing still
rewrites 1.3KB of prose about finding nothing.

- [ ] **Settle the open question first** (below): dedicated end-of-day task vs. write-only-when-acted
- [ ] Remove the unconditional daily-log rule from `prompts/heartbeat.md`
- [ ] Ensure the day's heartbeat activity still reaches the log — the heartbeat scope does **not**
      get today's notifications injected (only the user scope does), so an end-of-day writer needs
      `get_notification_history`: one call/day, not two round-trips/tick
- [ ] Verify: `write_memory`/tick falls from 2.3 (1.97 as of 07-28); a full day's log still lands
      with both `## Conversations (today)` and `## Heartbeat Activity` populated

**Open questions — settle before coding WS8c.**

1. **Daily-log cadence.** (a) A dedicated end-of-day task (`every 24h | due: 22:00-23:59`) — clean,
   but if the service is down in that window the day's log is lost, and the writer must reconstruct
   the day from the injected chat slice + `get_notification_history`. (b) Write only when a task
   actually acted — cheaper on quiet days, but the log lags the conversation it narrates.
   **Lean (a)**, with the 2-tick window as the outage cushion.
2. **Is the daily log still worth its cost at all?** Its original job was cross-scope awareness, and
   it is no longer the sole bridge — live chat/notification slices are injected into both scopes
   directly (CLAUDE.md, "Live cross-scope awareness"). What remains is its value as a permanent
   narrative archive beyond the 50-message window, which is real (`memory-index-audit` treats daily
   logs as undeletable). Worth an explicit decision rather than inheriting the hourly rewrite.

**Baseline to measure WS8b/WS8c against** (2026-07-16, pre-WS8; 07-28 readings in parentheses):

| Metric | Value |
|---|---|
| heartbeat rows/day (model ticks) | 18 |
| heartbeat input tokens/day | 1.60M |
| LLM calls per tick | ~3.8 |
| worst no-op tick | 214,008 input / 5 calls |
| `get_chat_history` per tick | 0.95 → **0.03 (07-28, GREEN)** |
| `read_memory` per tick | 3.1 → 2.36 (07-28) |
| `write_memory` per tick | 2.3 → 1.97 (07-28) |
| gate skip rate | 22% (35 skipped / 127 ran, 7d journal) |
| heartbeat share of all input | 83% (88.0M vs 17.9M user) |

**Note on the notes files themselves** (out of scope here; in scope for #20 or a redesign): they are
machine state hand-serialized as prose — four spellings of "when did this last happen"
(`last_checked`, `last_checked_date`, `last_sync_date`, `last_notified`), one file missing the `#`
header the others have, three carrying no state at all. They already drift: `running_prep.md` and
`readiness_check.md` disagree about session 7. WS1 Phase 7 moved `last_run` out to code-owned
`state.json` for exactly this reason; the rest stayed behind.

**Risk.** Low. WS8a/b/c only remove instructions or add injected content; each reverts in one commit.

---

## WS7 — Conversation compaction (rank 2; **unparked 2026-07-30**)

**Why it moved.** This workstream was parked pending *qualitative* evidence — recurring duplicate
briefings, the 50-cap evicting useful context, "Jarvis forgot what we discussed" complaints. None
appeared, so it stayed parked. But its gating criteria never asked the cost question, and on cost it
is the **largest single line item**: the `heartbeat` checkpoint row is 108,785 bytes ≈ ~27k tokens
≈ **63% of every call's input**, re-sent on each of a tick's ~5 calls. Tool results add ~18%.
Together ~81%, against the ~10% WS2/WS3/WS4 divide between them. Archive #54's evidence gate is
superseded by measurement.

**Do the cheap half first — tool-result pruning, not summarization** (OpenClaw session-pruning). In
`_add_and_trim` (`agent.py:80`), before counting toward the 50-cap, replace the *content* of
ToolMessages older than the last ~3 exchanges with head+tail excerpts (`[tool result pruned]`).
Jarvis averages ~7 tool calls per turn-pair; old tool payloads are the bulk of window weight. This
is a ~20-line reducer change — no LLM summarizer, no reentry-guard problem — and it may defer real
summarization indefinitely. Note it composes with WS8 rather than overlapping: **WS8 removes
round-trips; WS7 shrinks each one.**

**If summarization is still needed after pruning**, adopt the flush-then-summarize order (OpenClaw
`memoryFlush`): a silent "save anything durable to memory files" turn before the lossy summary, then
Hermes's head/torso/tail split (protect the last ~20 messages, summarize the middle into one
SystemMessage) — matching #54's sketch, which stays valid.

**Verify.** The harness's **checkpoint-weight** test is the acceptance criterion; it fails today at
108,785 bytes. Plus per-call input in `turns.jsonl`.

**Risk.** Pruning: low (bounded, revertable, no new state). Summarization: medium — deferred behind
pruning's measured result.

Stage 4 (thread unification) remains parked per #54 — WS1's structured tick-ack and WS2's per-scope
stable prefixes reduce the coordination pressure that motivated it.

---

## WS2 — Time grounding + cache-stable prompt (rank 3; **rewritten 2026-08-01**)

> **Two prior sketches are withdrawn.** The original ("move the clock to the last line of the
> system prompt") was overturned by a 2026-07-13 design pass whose draft was never committed. That
> pass's own conclusion — "timezone only, no clock anywhere" — is *also* now superseded: it was
> reconstructed from memory, and a fresh read of the reference implementation shows it moved on.
> What follows is measured against production on 2026-08-01 and verified against OpenClaw at
> HEAD `aa743c9f` (2026-08-01), not recalled.

**This workstream is now justified on correctness first and cost second.** The clock is rebuilt on
*every* LLM call (`build_system_prompt` is called inside `_llm_node`, `agent.py:483`), so within one
turn call 1 reads `06:14` and call 4 reads `06:15`. The model's "now" moves underneath it
mid-reasoning, on the same turn that may be computing a reminder's `fire_at`. Nothing would ever
flag that. The cache win is real but smaller than §0.1 implied (below).

### 2.0 Measured, 2026-08-01 (prod, 7-day window to 2026-08-01 06:45Z)

| | heartbeat | user |
|---|---|---|
| turns / LLM calls | 129 / 612 | 30 / 141 |
| calls per turn | 4.74 | 4.70 |
| input | 9.99M | 4.21M |
| `cache_read / input` | **35.5%** | **49.5%** |
| cost at current `MODEL_PRICES` | $16.65/mo | $6.23/mo |

Cached input bills at `$0.05/M` against `$0.50/M` — a **90% discount**, so cache rate is a larger
lever than this doc previously credited. For a 5-call turn the ceiling is roughly (N−1)/N ≈ 80%;
heartbeat sits at 35.5%. Closing that to ~70% is worth **~$6.65/mo** of a **$22.87/mo** total.

**The clock is not what is costing us that.** The old correction 2 rested on "64% of heartbeat turns
cross a minute boundary mid-turn". Re-measured: median heartbeat turn **12.6s** (mean 13.7s, **zero**
turns ≥60s), median user turn 8.9s. Expected minute-boundary crossings ≈ **23%**, not 64% — WS8a's
removal of `get_chat_history` shortened ticks. Ceiling on the clock's cost: 23% of turns losing one
call's prefix out of ~4.7 calls ≈ **5% of input ≈ $1/mo**.

**Two observations that do not fit the clock hypothesis**, and are the reason for the instrument in
§2.5: user scope gets 49.5% cache against heartbeat's 35.5% at *identical* calls/turn, though both
rebuild the same first line; and `read_memory`/tick has drifted 2.36 → 2.73 without a matching cache
change.

### 2.1 What the measurements change — item 4 is the main event

The old correction 3 said the 50-message cap "breaks the history prefix every turn". It is worse:
`_add_and_trim` is the **reducer** on `messages` (`agent.py:164`), so it runs on *every state update*
— after each `_llm_node` return and each `_tool_node` return. Both threads sit at the cap (heartbeat
43 messages, telegram 48, after the HumanMessage-boundary advance at `agent.py:142`), and a heartbeat
tick adds ~11.5 messages (6.8 tool + 4.7 LLM). So the window head slides **several times inside a
single turn** — precisely the only window where caching can pay.

**Therefore: item 4 (trim hysteresis) owns the cost case; items 1–3 (time grounding) own the
correctness case.** They are separable and should be judged separately. Item 4 is also
doc-independent — a stable history head helps regardless of how Gemini's TTL or prefix-span
semantics actually work, so it does not wait on the re-verification below.

**Still unverified against current Gemini docs** (carried forward, do not treat as established): that
the cache prefix spans the whole request (`system_instruction` + tools + history) rather than the
system prompt alone; and that implicit-cache TTL is short enough that hourly ticks never hit each
other's cache. Both were recorded from the lost 2026-07-13 draft.

### 2.2 Reference implementation — OpenClaw at HEAD `aa743c9f` (read 2026-08-01)

Four distinct mechanisms, and **no live clock anywhere in the system prompt** (zero hits for
`Current time` in `system-prompt*.ts`):

1. **System prompt carries a coarse date + timezone + a pointer** (`src/agents/system-prompt.ts:443`):
   `## Temporal Context` / `Current date: YYYY-MM-DD` / `Time zone: <TZ>` / *"For the exact current
   time, use `session_status`."* `formatDateStamp` is documented as *"a **stable** YYYY-MM-DD
   stamp"* — **day resolution**, so it breaks the cache once per day, not once per minute.
2. **Per-message stamps applied at the LLM boundary** (`attempt.llm-boundary.ts:385`), format
   `[DOW YYYY-MM-DD HH:MM TZ] `, derived from **each message's own `timestamp` field, never
   wall-clock `now`**. Storage stays bare. Their comment states the invariant: *"the SAME message
   produces byte-identical bytes whether it is sent as the current turn or replayed as history. That
   stability is what lets full-resend transports cache the prefix."* (issue #3658)
3. **Cron/heartbeat appends a two-line block to the tick body**, not the system prompt
   (`heartbeat-runner-execution.ts:658`): `Current time: <local> (<TZ>)` + `Reference UTC: <ISO> UTC`,
   computed from `startedAt` — the tick's start, not `now`.
4. **A tool** (`session_status`) returns that same line on demand.

**Evolution worth noting.** A 2026-07-13 copy of the same tree had `## Current Date & Time` with
**timezone only**; by 2026-08-01 it had become `## Temporal Context` with a `Current date:` line and
the tool pointer added. They moved *toward* putting time back in the system prompt — at day
resolution. The "timezone only, no clock" position this doc previously recorded is a snapshot of a
design that has since been refined.

**Two details they paid for in bugs, worth adopting free:**

- **Day-of-week is deliberate** (`agent-timestamp.ts:47`): *"3-letter DOW: small models (8B) can't
  reliably derive day-of-week from a date, and may treat a bare 'Wed' as a typo. Costs ~1 token."*
- **Issue #44993 — stale-clock leak.** Because heartbeat prompts flow through their helper
  repeatedly, a previously-injected `Current time:` block could survive and go stale; the fix
  *refreshes* an existing block rather than appending a second, behind a deliberately strict
  two-line regex so it never eats a user-authored line beginning `Current time:`. Plus double-stamp
  guards: never stamp text already carrying an envelope or a cron marker.

### 2.3 Design

1. **Envelope becomes date + scope, not a clock.** `[Current date: 2026-08-01]` (day resolution)
   replaces `[Current time: … HH:MM …]` at `agent.py:407`. One cache break per day, at Israel
   midnight = 21:00/22:00 UTC — inside the overnight stretch where the gate reports `nothing due —
   skipping model turn`, so effectively free. **No timezone line is needed**: `prompts/AGENTS.md:1`
   already carries it (*"Roi lives in Tel Aviv (Asia/Jerusalem, UTC+3 in summer / UTC+2 in
   winter)"*), and duplicating it costs bytes for nothing.
2. **Stamp each turn's input, from that turn's own fixed time.** Prefix
   `[Sat 2026-08-01 09:14 +03] ` — day-of-week included per the note above; **numeric offset, not
   `%Z`** (see §2.4). Applied in `ask_jarvis` (`agent.py`), which is the single entry to a turn and
   already sets up the ContextVars and telemetry — so the four call sites (Telegram, heartbeat tick,
   confirmation-outcome replies, `ask_jarvis_once`) cannot each forget. Lands on both message shapes,
   since `user_input` becomes either a bare string or `content[0]["text"]` (`agent.py:657`).
   `main.py:58` writes the raw text to `chat_history.jsonl` *before* calling `ask_jarvis`, so the
   chat log stays clean and unstamped — it has its own `ts`.
3. **Heartbeat tick body also gets a `Reference UTC:` line.** This is a correctness win independent
   of caching: `AGENTS.md:1` requires `manage_reminder`'s `fire_at` in ISO 8601 UTC while everything
   user-facing is Israel time, so the model performs a timezone conversion by hand on every reminder.
   Handing it both removes the step.
4. **Trim hysteresis in `_add_and_trim`** — trim to a low-water mark only once past a high-water mark
   (e.g. 70/50) instead of slicing to exactly `MAX_MESSAGES` on every state update. The head then
   holds still for many calls and many turns. **This is the item that owns the cost case** (§2.1).
5. **Reorder the remaining system prompt stable → volatile.** Stable: `[Active scope]` → SOUL.md →
   AGENTS.md → USER.md → scope framing (+ `prompts/heartbeat.md`) → skill list. Volatile tail: date,
   HEARTBEAT.md / due-task blocks + injected notes, daily log, live chat/notification slices.
6. **`[Channel: …]` is volatile** (`agent.py:417`) — it varies by origin, so it belongs in the tail;
   otherwise alternating channels cross-invalidate. It is constant *within* a thread, so this is
   cheap insurance rather than a live problem.
7. **Keep hot-reload.** Per-turn re-reads are fine: SOUL/AGENTS/USER change rarely, and when they do
   the miss is deserved.

**Not adopting: a `session_status`-equivalent time tool.** `manage_reminder` already echoes the real
current time in its create confirmation (`tools/core/scheduling.py:127`), which covers the one path
where precision bites. WS8a's lesson is that whether the model calls a tool is driven by docstring
wording and is unreliable; "sometimes forgets to check the time" is a worse failure than "off by the
turn's duration". Revisit if drift shows up.

**What we accept:** the model's "now" becomes **turn-start** time, not per-call time. Measured, that
is a sub-15s error on the median turn either scope, with a ~90s worst case bounded by the heartbeat
timeout. Below anyone's threshold for "remind me tomorrow at 9", and self-correcting inside the turn
via the `manage_reminder` echo.

**Phase order: stamps land before clock removal.** Removing the clock first leaves the model
temporally blind for a deploy window. Two commits, since deploys are cheap and the intermediate state
(time present twice) is harmless.

### 2.4 Timezone & day-edge correctness

Israel runs **UTC+2 in winter (IST) and UTC+3 in summer (IDT)**, transitioning at 02:00 local — 2026:
spring forward **2026-03-27**, fall back **2026-10-25**. The tick lattice is **UTC** (`CronTrigger`,
`TICK_INTERVAL_HOURS`), while every `due:` window is Israel-local, so the two shift relative to each
other twice a year. Verified behaviours, measured 2026-08-01 against the live code:

- **Midnight always exists.** Both transitions happen at 02:00, so `00:00` is never a nonexistent or
  ambiguous wall time. Everything keyed to start-of-Israel-day is therefore safe:
  `_today_israel_start_utc` (`agent.py`), `_today_israel` and the `daily_YYYY-MM-DD.md` filenames,
  and the chat/notification slice filters. Checked on both 2026 transition dates.
- **Windows shift by exactly one UTC tick across DST.** Measured for all four windowed tasks — e.g.
  `morning-readiness-check` (`08:00-10:00` IL) is in-window at UTC ticks `{5,6}` in summer and
  `{6,7}` in winter; `crossfit-sync-and-remind` moves `{3..18}` → `{4..19}`.
- **A 24h task locked on the *last* in-window tick loses exactly one occurrence at spring-forward.**
  Simulated across every possible lock position through both 2026 transitions using the real
  `any_due`/`stamp` path: a task stamped at 07:00Z (last winter tick) misses 2026-03-27 entirely,
  then self-heals the next day. This is the DST half of the §2b `heartbeat-assert` risk, now with a
  date and a direction.
- **Production is currently on the safe side of both transitions.** `morning-readiness-check` is
  stamped at 06:00Z, which is in-window in *both* offsets, so it survives spring-forward and gains
  margin at fall-back. Its `ticks left: 0` flag remains a real **dropped-tick** fragility; it is
  **not** an imminent DST loss. Left as-is by owner decision 2026-08-01.
- **`%Z` is unsafe for the stamp.** Python yields `IST` for Israel winter — which also reads as India
  Standard Time. Use the numeric offset (`%z` → `+0300`, rendered `+03`), which is unambiguous and
  survives locale changes.
- **Latent, not live: windows inside 01:00–03:00 local.** A `due:` window at 02:30 falls in the
  spring-forward gap (Python resolves it to the pre-transition offset), and 01:30 is ambiguous at
  fall-back (`datetime.combine` takes `fold=0`, the first occurrence). No current task has a window
  in that band. Worth a validation warning in `manage_heartbeat_task` rather than gate logic.

**Constraints on the new stamp specifically:** it must be computed from the turn's own fixed
timestamp and never recomputed (that is the whole byte-stability property); it must carry a numeric
offset so a stamp written in IDT is still unambiguous when read back in IST; and the double-stamp
guard matters for us too, since the injected chat slice already renders `[HH:MM] message` — a stamped
message must not be re-stamped when it later flows through that path.

### 2.5 The instrument (land before or with item 4)

Per-turn telemetry cannot attribute a cache miss to a call: `record_llm_call` sums into the turn
accumulator (`observability/telemetry.py:112`), so all ~5 calls report as one number. Add
`llm_calls.jsonl` — one row per LLM call, joining `turns.jsonl` by `turn_id`, exactly the
`tool_calls.jsonl` pattern: `call_index`, `input_tokens`, `cache_read_tokens`, `output_tokens`,
`msg_count`, and a hash of `messages[0]` (the window head). Register it in `main.py:168`'s trim tuple
for the 90-day retention.

With the clock already out of the prompt, the remaining question is binary and the head hash answers
it: **head changed → the trim is costing us the discount (item 4 is the fix); head unchanged and
`cache_read` still flat → it is Gemini's implicit cache, and no change of ours recovers it.** That
second outcome is the only one that says *do not build item 4* — and today we would discover it only
after shipping. ~750 LLM calls/week in prod, ~200 bytes/row: negligible.

### 2.6 History — this reverts a deliberate decision

`main.py` originally prepended a `time_ctx` to `inbound.user_text`, i.e. the arrival-stamp design
above. It was removed on purpose: `docs/plans/archive/ARCHITECTURE_PLAN.md:98` lists it as
architecture problem #4 (*"Time context is jammed into the user message … instead of into the prompt
envelope"*), line 964 directs *"drop `time_ctx` prepending"*, and that phase's verification step 5 is
*"Confirm time context is in the prompt envelope, not prepended to user text."* **The stated
rationale was structural tidiness — nothing about correctness, and nothing about caching**, which was
not yet a consideration. Recorded here so the reversal is a decision rather than an accident.

**Verify.** The harness's **cache-prefix invariant** test is the acceptance criterion (`xfail` today
— `TEST_HARNESS_PLAN.md:91` notes "the clock is line 1"); it must compare *whole requests*, not just
system prompts. Plus `llm_calls.jsonl` per-call `cache_read` (§2.5), read as an **intra-turn** metric.
Do not expect hourly ticks to hit each other's cache. Prompt-content changes also need a read-through
of an assembled prompt in each scope, and `docs/architecture/MEMORY.md:115` must be updated — it
documents the envelope shape.

**Risk.** Items 1–3 are a behavioral change (temporal grounding moves to the message stream), not a
reordering; the mitigation is the two-commit order and the day-resolution date, which keeps the model
from ever being date-blind. Item 4 changes window mechanics and trades some input volume for cache
rate — at a 90% cache discount that is a good trade, but it is a trade, and it conflicts textually
with WS7's tool-result pruning in the same function. Land them together or sequence deliberately.
Every item reverts on its own.

---

## WS4 — Bootstrap context budget (rank 4; a backstop, not a win)

**Why.** SOUL.md, USER.md, HEARTBEAT.md, and daily logs are injected whole with no cap
(`load_or_blank`, `agent.py:198`). One verbose daily log inflates every turn that day. Both
references cap injected copies while leaving files intact on disk. Per §0.1 this is inside the ~10%
slice — it earns its place as growth insurance, not as a spend reduction.

**Design.**

1. `load_or_blank(path, max_chars: int | None = None)` — when over budget, keep 70% head + 20% tail
   (Hermes split) with a `[... truncated N chars — file intact on disk ...]` marker between.
2. Budgets (generous — backstops, not diets): SOUL/USER/AGENTS 20k chars each; daily log 10k;
   HEARTBEAT.md 10k; live slices already capped (60×240 / 20×240 — unchanged).
3. Log a warning when truncation fires, so growth is noticed rather than silent (OpenClaw's
   `bootstrapPromptTruncationWarning`).

**Verify.** The harness's **context-budget ceiling** test. Plus: seed an oversized scratch daily log,
confirm marker + warning; confirm normal files pass untouched byte-for-byte (prefix stability for WS2).

**Risk.** Very low.

---

## WS5 — Memory & history search (rank 5; capability, from hermes-agent)

**Why.** Recall today requires knowing the exact filename via MEMORY.md; chat history older than the
50-message window is unreachable except by time-window (`get_chat_history(since=…)`). "What did we
decide about X last month?" has no path. Hermes solves this with plain FTS5 over raw history — no
embeddings, no new infra — and it is the reason its in-context memory can stay tiny.

**Design.** New core tool `search_memory(query, days=90)` in `tools/core/`:

- **v1: linear scan, no index.** 90 days of JSONL + the memory dir is a few MB; a regex/substring
  scan is milliseconds at this scale. Sources: `chat_history.jsonl`, `notifications.jsonl` (both
  already Jarvis-readable via history tools), and `/app/jarvis_memory/**/*.md|txt` (through the
  existing `_get_safe_path` sandbox). Returns top ~10 hits: source, date/filename, ±1 line of
  context, per-hit truncation (reuse `history.py`'s per-entry caps).
- **v2 (only if v1 latency or ranking disappoints): SQLite FTS5** index at
  `/app/jarvis_data/search/index.sqlite` (tool-opaque state per the placement principle), rebuilt
  incrementally by mtime.
- Docstring guidance: search first, then `read_memory` the specific file — mirrors the existing
  list→read pattern. **The docstring is the behavioral contract, not prose in `AGENTS.md`** (WS8a's
  lesson); word it deliberately.
- `prompts/AGENTS.md` gets one line: when the user references something not in context, search
  before saying you don't remember.

**Verify.** Ask Jarvis about a topic from >50 messages ago; confirm it searches, finds, answers.
Structural assert over `tool_calls.jsonl` that the tool is actually used — and that it is not
over-called on every turn.

**Risk.** Low. Additive tool; sandbox already enforced.

---

## WS6 — Memory size pressure (rank 6; from hermes-agent)

**Why.** Nothing pushes back when USER.md or MEMORY.md grows; every added line is a permanent
per-turn tax on both scopes (USER.md is in every prompt). Hermes's cap + gauge + error-driven
consolidation keeps the agent responsible for curation continuously, instead of a weekly audit task.

**Design.**

1. Caps in `tools/core/memory.py` for the two always-injected files: USER.md 6k chars, MEMORY.md 8k
   (roughly 3–4x current sizes — check actuals before fixing values).
2. `write_memory` to a capped file that would exceed its cap → **error** returning the current
   content and the overage, instructing the agent to consolidate and retry. No silent truncation;
   SOUL.md exempt (user-curated, confirmation-gated already).
3. Gauge line appended to the injected copy: `[USER.md: 4,120/6,000 chars]` — Hermes shows this
   nudges pruning before the hard stop. **Caveat:** it is derived from content, so it only changes
   when the file changes — which is exactly when the cache breaks there anyway. Safe for WS2.
4. Extend the weekly `memory-index-audit` heartbeat task to also distill daily-log material into
   MEMORY.md and prune stale entries — the audit becomes the slow loop, write-time pressure the fast one.

**Verify.** Script a `write_memory` that busts the cap → error with current content; confirm gauge
renders; confirm SOUL.md unaffected.

**Risk.** Low-medium: a too-tight cap could make the agent thrash (consolidate every write). Start
generous; `tool_calls.jsonl` will show retry loops.

---

## WS3 — Heartbeat light context (rank 7; **rescoped 2026-07-30**)

**Original claim, withdrawn.** WS3 promised "tick input 66k → ≤15k". §0.1 shows that is unreachable
by its method: the system prompt is ~4.5k of a ~42.8k call, so deleting all of it still leaves
~38k/call. The idea was sound; the arithmetic behind the target was not.

**What survives.** A tick doesn't need Jarvis's full conversational identity to check whether a class
ended. Dropping full SOUL.md (replaced by a 3–5 line identity digest — voice matters for briefing
text), yesterday's daily log, and USER.md from the heartbeat prompt is worth a few percent per call
and reduces noise. Keep: terse framing, `prompts/heartbeat.md`, due-task blocks + their injected
notes (WS8b), today's chat slice (cheap, and what prevents duplicate briefings), skill list.

**Order of operations.** Last, and only after WS8 and WS7 have landed — at that point the remaining
per-call composition is worth re-measuring before spending quality risk on a few percent. It is
entirely reasonable to **drop this workstream** if WS7 has already shrunk the call.

**Verify.** Golden prompt snapshots (the harness's third test) catch silent content drops. Quality is
the real risk and the only thing here needing a judge (§0.5): a week of watching briefing text, whose
failure mode is blander/wronger notifications. The daily-log instruction already tells the agent to
source today's chat from the injected slice, so the log keeps its inputs.

**Risk.** Medium-low, and now clearly out of proportion to the measured payoff. Revert is one commit.

---

## WS1 — Heartbeat gating, windows, self-authoring (done 2026-07-13)

Shipped 2026-07-10..13; fully specified in
[HEARTBEAT_GATING_PLAN.md](archive/HEARTBEAT_GATING_PLAN.md) (9 phases, incl. the Hermes-derived
create-in-heartbeat-scope guard and ack-primary delivery).

**Honest status.** The mechanism is correct and shipped: due-only prompt injection, cadence + window
gate, code-owned `state.json`, stamp-after-delivery, self-authoring via `manage_heartbeat_task`. The
**cost claim was wrong.** "Most hours: no LLM call at all — verified in production" was written from
memory; measured, the skip rate is **22%** with zero gate errors (§0.3), and per-tick input rose
rather than fell. The gate does what it was built to do; the task definitions never let it close, and
the per-tick ceremony it sat on top of was nobody's workstream until WS8.

---

## 4. Deferred to #20 — deterministic wake conditions

Earlier drafts made a deterministic Arbox "probe" the first phase of the heartbeat-cost work, and
this doc's *Cleanups* section named it too. It is deferred to **#20** (on-demand/event-driven
heartbeat) — because §0.3 shows the gate's open-rate is not what makes a tick expensive. Fix the cost
of an open gate first (WS8); it is cheaper, safer, and helps all eight tasks.

**The general capability**, as framed by the owner: wake Jarvis on deterministic conditions — *before
practice* (T-minus a known event) or *on a diff* (fetched state changed). These are two different
mechanisms, and conflating them is what made the "probe" look simple:

- **T-minus before a known event** — needs an event already on the books; scheduler-shaped. Note
  `manage_reminder` + APScheduler already does exact-time wake with restart persistence and past-due
  handling; it just notifies the owner rather than waking the agent.
- **Diff on fetched state** — *discovers* events; poll-shaped.

This also reopens a decision the gating plan made deliberately (§2 of that plan): OpenClaw has **two**
mechanisms — heartbeat (fixed-interval poll) and cron (exact-time wake) — and Jarvis chose
windowed-poll only, omitting OpenClaw's `nextCheck` on those grounds. "Wake before practice" reopens
it. That deserves its own design, not a crossfit-shaped patch.

**Constraints discovered here that any #20 design must respect:**

1. **The probe cannot call `fetch_upcoming_arbox_classes()`.** It is not a read: it upserts the
   workouts DB and calls `_purge_dropped_arbox_classes`, which **deletes** the dropped rows and
   returns them to build the "Removed N class(es)" notice. That notice is **one-shot**. A probe that
   calls it, then wakes the LLM to call it again, gets no notice the second time — the dropped-class
   flow (delete stale reminder, tell the owner, re-check quota) silently never fires. Any probe needs
   a pure-read split.
2. **Probe state must be code-owned**, not `heartbeat/crossfit_check.md`. Same reasoning as WS1
   Phase 7 (`last_run` → `state.json`).
3. **Stamp-after-success, or a transient error becomes permanent.** Probe sees a new class →
   escalates → writes state → the LLM turn times out at 90s (this happens) → next tick sees no diff →
   skips forever; the class never gets a reminder. Reuse the existing discipline: only acted tasks
   advance state, after delivery settles.
4. **The <4h briefing is time-shaped, not diff-shaped.** A pure schedule diff never fires it — the
   schedule is unchanged, the clock moved.

---

## Cleanups (fold into whichever workstream touches the file first)

- ~~**Stale comments** in `agent.py` claiming `active_skills` doesn't filter the bound tool set~~ —
  **done**; the comments now describe the shipped `_visible`/`get_tools` gating correctly.
- ~~**Deterministic Arbox poll**~~ — promoted out of *Cleanups* to §4, deferred to #20.
- **Issue hygiene**: origin mirror #18 and archive #33 are the same umbrella — cross-link this doc
  from both. #54's Stage 3 sketch is superseded by WS7 above (comment on the issue rather than
  editing it), and its evidence gate is superseded by §0.1. The heartbeat-cost work now in WS8 was
  never filed as an issue; file one or track it here.

---

## Sequencing & measurement

```
WS8a ✓ ──► WS8b ──► WS8c ─────────────────────►  (rank 1: the round-trips)
     WS7 pruning ──► WS7 summarization? ───────►  (rank 2: the 63% + 18%)
        └─ shares _add_and_trim ─┐
     WS2 stamps ──► WS2 clock removal ────────────►  (time grounding: correctness, no Gemini re-verify needed)
     llm_calls.jsonl (§2.5) ──► WS2 item 4 hysteresis ──►  (the cost half; instrument decides if it is worth building)
        └─ re-verify Gemini caching only gates item 4's *interpretation*, not items 1–3
     WS4 ──────────────────────────────────────►  (backstop, any time)
     WS5 ──► WS6 ──────────────────────────────►  (capability track, independent)
                              WS3 ·············►  (last; drop if WS7 sufficed)
     TEST_HARNESS_PLAN Phase 3 ──► Phase 4 ────►  (the instrument; alongside WS2/WS7)
```

Every workstream's effect is checkable in `turns.jsonl` (per-turn input/output/cache-read by scope) —
run `summarize_usage(group_by="scope")` over a comparable window before and after each landing, and
record both readings in the PR description.

**The discipline §0.4 bought:** "verified in production" without a recorded reading is worth nothing.
Three of this roadmap's workstreams have an assert waiting for them in
[TEST_HARNESS_PLAN.md](TEST_HARNESS_PLAN.md) — checkpoint weight (WS7), cache prefix (WS2),
context-budget ceiling (WS4) — and a golden-snapshot diff guards WS3. Prefer landing the relevant
test with the workstream over asserting the win in prose.
