# Review — Is the Context Redesign Overbuilt?

**Reviewer angle:** simplicity / cost-benefit, deliberately adversarial. **Date:** 2026-10-06.
**Reviewed:** [../../CONTEXT_REDESIGN_PLAN.md](../../CONTEXT_REDESIGN_PLAN.md) at the uncommitted draft of 2026-10-06.
**Ground truth used:** prod `turns.jsonl` and `tool_calls.jsonl` (09-15..10-06), a read-only count of
message types in the latest prod `owner` and `heartbeat` checkpoints, and byte sizes of prod
`jarvis_memory/`. No message content was read.

## Verdict

The plan correctly finds the two problems that matter (the owner window is mostly tool traffic and
forgets within a handful of turns, and every user call carries ~18k tokens of skills nobody is using),
but it answers them with machinery built for multi-user products and coding agents: an LLM compactor
with six validators and a thrash guard, a nightly op-based consolidator with quote verification, a
weekly replay, embeddings, and four new model prompts to maintain. Two deterministic changes inside
code that already exists (collapse finished turns in the reducer that already strips media, and reset
skills at an idle gap where the cache is already cold) deliver most of S3's value with no model call,
and the measured case for S5/S6 is close to nil at a 1.4 KB USER.md and one `get_chat_history` call in
two weeks. Ship S0, a slim S1, S2, the deterministic S3 and a slim S4; put S5 and S6 behind evidence
gates that may never open.

---

## What the data says (the numbers this review argues from)

| Fact | Reading | Source |
|---|---|---|
| Spend | $0.21–0.85/day on quiet days (09-30..10-06); **$0.5–2.9/day on trip days** (09-15..09-27), almost all user scope | `turns.jsonl`, `MODEL_PRICES` |
| User input per LLM call | **~34k, flat** across 1-call, 3-call and 10+-call turns | 314 user turns since 09-22 |
| Of that: tools + skill rules | ≈18.4k (≈50%), active set = 10 namespaces and never shrinks | TOOL_AND_SKILL_CONTEXT.md |
| Owner window right now | **54 messages: 5 human, 9 AI, 40 tool.** Tool bodies are 51 KB of ~64 KB (80%) | prod checkpoint, counts only |
| Heartbeat window right now | 52 messages: 5 human, 20 AI, 27 tool; ~27 KB (≈7k tokens of replay per call) | prod checkpoint |
| Cache after an idle gap | 1-call user turns: **32% cached if <5 min since last turn, 0% after ≥60 min** (n=103 / 17) | `turns.jsonl` since 09-01 |
| History-search demand | `get_chat_history` in user scope: **1 call in 15 days** | `tool_calls.jsonl` |
| Hot-path memory writes | `write_memory` user scope 55 / heartbeat 183 in 15 days | `tool_calls.jsonl` joined to turns |
| Curated memory size | USER.md 1,359 B (34% of the proposed cap); MEMORY.md 1,886 B (~30 lines of a 200-line cap); all non-daily memory ~27 KB | prod `jarvis_memory/` |
| Heartbeat | ~10–17 ticks/day, 80 of 101 ticks `no_action`, ~80–90k input/tick since the triggers deploy | `turns.jsonl` |

Three consequences the plan does not draw:

1. **"The window forgets" is a tool-traffic problem, not a summarization problem.** Five human turns
   fit in the window because forty slots are tool messages. Remove the tool traffic of finished turns
   and the same 50-message window holds ~25 turns.
2. **The cache argument against skill churn disappears at an idle gap.** After an hour of silence the
   cache is already 0%, so changing the tool block at that moment costs nothing. The plan ties decay to
   compaction to protect a cache that does not exist across the owner's real usage gaps.
3. **The dollar case is small.** The whole redesign can save perhaps $0.2–0.5/day on quiet days and
   more on trip days. Every slice has to justify itself by owner-visible behaviour, not spend, and
   several cannot.

---

## Ranked simplification proposals

### 1. Replace LLM compaction (S3) with deterministic turn collapse in `_add_and_trim` — *challenges settled decisions 5 and C1–C8*

- **Cut:** the post-turn compaction job, the JSON summary schema and prompt, the substring/date/secret/
  size validators, the corrective retry, the deterministic fallback record, the thrash guard, the
  checkpoint-staleness check, the golden-tested framing prefix, the `compaction` telemetry event.
- **Replace with:** at turn start, the reducer (which already strips media blobs from existing
  messages, `agent.py:_add_and_trim`) rewrites every *finished* turn to its `HumanMessage`(s) plus the
  final `AIMessage` text, optionally with one stub line (`[tools used: manage_itinerary ×12, web_search]`).
  The full turn is in the episode store (S1), so nothing is lost. Keep `MAX_MESSAGES` as the single
  knob: 50 for coverage (~25 turns), or lower it for cost.
- **What is gained for free:** window coverage 5 → ~25 turns; history bytes per call down ~80% at
  today's composition; F2 (a polluted checkpoint re-seeding its own call shape) disappears at the root,
  because old tool calls no longer sit in context; no old `functionCall` remains for an undeclared tool,
  which is what makes proposal 2 safe.
- **What is lost:** a model-written "open requests / decisions / learned" record of turns that fall off
  the end of a ~25-turn window (on quiet days that is several days back, where the episode store and
  memory files already answer), and in-context examples of good tool calls from earlier turns (the
  docstrings carry those, per F1).
- **Saved:** the single largest and riskiest component in the plan, and its maintenance forever: one
  more prompt that can drift with a model upgrade, a JSON schema, six validators, a concurrency rule.
- **Must check on staging:** Gemini 3 thought signatures. Dropping intermediate model turns from
  *previous* turns is believed to be accepted (strict validation is on the current turn), but this
  has to be one staging run before anything else is built on it.
- **Confidence:** high on value, medium on the Gemini detail (a one-hour staging check settles it).

### 2. Decay skills at an idle gap, not at compaction — *refines settled decision T1*

- **Cut:** decay's dependency on S3, the "unused in the kept tail and for ~24h" rule, the
  "skills used today" section of a summary, and the staging question about orphaned calls.
- **Replace with:** at the start of a user turn, if the previous owner turn ended ≥ N hours ago
  (say 3h), reset `active_skills` to empty (or to skills called in the last K turns). The measured
  cache share after a ≥60-minute gap is 0%, so the tool-block change costs nothing. With proposal 1,
  old turns hold no tool calls, so there is nothing to orphan. Re-activation is one round-trip, already
  how a cold skill works today.
- **What is lost:** a travel-planning day split by a long lunch re-activates travel once.
- **Saved:** ≈5–8k+ tokens on most user calls (≈15k when the set collapses to core plus one skill), with
  no coupling to any other slice. This is probably the single largest token saving in the plan and it
  can ship in the first week.
- **Confidence:** high.

### 3. Defer the nightly consolidator (S5) behind an evidence gate — *challenges settled decision 4*

- **Cut:** the nightly job, the 8-op schema, per-run handles, quote verification, the 25%-loss rule,
  the read-set conflict check, the cursor, the seeding pass over backfill, the code-composed morning
  note and its one-shot trigger, `/memory undo`, the file lock, and the narrowing of hot-path writes.
- **Why:** the plan names no owner-visible failure that hot-path writing has caused. The research
  found one stale line in USER.md (a July vacation). USER.md is 1.4 KB, 34% of the cap the plan sets.
  The consolidator exists to curate a file that takes months to grow, and it introduces a new failure:
  once AGENTS.md says "only write when asked", a missed or failed nightly run loses the fact silently,
  and "promoted 0 because…" is the model grading its own recall. Auto-writing an every-call injected
  file from a Flash call with no eval harness (§10 admits S3–S5 are verified by reading transcripts)
  is the highest-blast-radius change in the plan.
- **Replace with:** keep hot-path writes as they are; S2's caps give the pressure the plan wants; git on
  `jarvis_memory/` (S2) gives history and undo for every write, hot-path included; fold "expired dated
  entries and near-duplicates in USER.md" into the existing weekly `memory-index-audit` task's text. A
  `/memory` slash command that shows `git log --since=yesterday` covers "every memory change in one
  place" at zero context cost.
- **Gate to build it:** two or more owner-reported cases in a month of something said in chat that
  should have been remembered and wasn't, or USER.md reaching ~75% of its cap with stale content.
- **Lost:** automatic promotion of preferences mentioned in passing.
- **Saved:** the second-largest component, a second always-running model job, a daily owner-facing
  message that can become noise (the plan already plans for a separate channel "if it gets noisy").
- **Confidence:** medium-high.

### 4. Fix the daily log this week with a one-line HEARTBEAT.md edit

- **Cut:** waiting for S5 to fix A7.
- **Replace with:** `daily-log | every 1d | due: 22:30-23:30`. At `every 3h` over 05:00–23:30 it is due
  ~6 times a day, a large share of today's ~10–17 ticks at ~80–90k input each. Then ask whether
  injecting today's daily log into the user scope still earns its place once proposal 1 makes the
  window cover the day itself.
- **Lost:** an intraday narrative refreshed every 3h, which the window will already hold.
- **Saved:** on the order of 5 ticks/day, likely the largest heartbeat saving available, for zero code.
- **Confidence:** high on cadence arithmetic; the exact tick share should be read from S0's due-set field.

### 5. Slim S4 to "fresh thread per tick + inject the due tasks' notes" — *keeps settled decision 1, drops its machinery*

- **Cut:** code-owned per-task state (last outcome, last output text) as a new store; skill warm-start.
- **Replace with:** run each tick on a throwaway thread (or keep zero prior turns), and inject the due
  tasks' `notes:` files (all eight are ~1.3 KB) into the tick prompt. That removes the ~7k-token replay
  per call *and* the ~2 `read_memory` round-trips per tick (206 heartbeat reads in 101 ticks), which A6
  measured as the bigger waste. For "last output", read the task's last row from `notifications.jsonl`.
  Bind a fixed heartbeat core plus `fitness`/`google_health` (unchanged in 101 of 101 ticks) and mark
  the fitness writers `scopes=("user",)`, which the registry already supports.
- **Lost:** D1's cosmetic cleanup of prose-state spellings (no measured misbehaviour attributed to it).
- **Saved:** a new state schema, a warm-start mechanism, and a migration.
- **Confidence:** high.

### 6. Slim S1 to "one table, one writer, one tool"

- **Keep:** `episodes.sqlite` written from the turn's final state, full tool results, FTS5,
  `search_history` replacing both history getters (a net core-schema reduction). Proposal 1 needs it.
- **Cut:** the backfill and the reader migration off `chat_history.jsonl` (let the JSONL age out on its
  90-day rotation; keep writing it until then, it is a few lines); the six-value provenance taxonomy
  beyond what a reader needs (`thread_id` + `role` cover it); Outbox/mirror rows (they are already in
  `notifications.jsonl` and, once mirrored, in the owner turn); the integrity check as a new telemetry
  stream (a row-count line in the existing usage report is enough).
- **Lost:** searchable history older than the store's start date for ~90 days.
- **Saved:** a migration with three readers, and a data-model debate.
- **Confidence:** medium — the backfill is cheap, but the reader migration is not, and demand for
  history search is measured at one call per fortnight.

### 7. Drop S6 embeddings and weekly replay indefinitely

- **Embeddings:** a second ranking, a vector extension, an embedding model choice, fusion logic, for a
  few MB of one person's text where FTS5 has not yet been shown to miss anything. Cut until an FTS
  miss is observed. **Confidence high.**
- **Weekly replay:** a second consolidator. Goes with proposal 3. **Confidence high.**
- **Media getter consolidation:** media saw 8 calls in two weeks. Once decay (proposal 2) works, idle
  media costs zero tokens, and rewriting 22 docstrings risks F1-style behaviour change for nothing.
  **Cut. Confidence high.**
- **Keep:** the schema budget in CI (cheap, mechanical) and duplicate-rule removal (small, but each
  edit needs the staging behaviour check the plan already requires).

### 8. Trim S0 to what decides something

- Keep the bound-tool list per call and the due set per tick, and one `countTokens` reading. These
  settle real questions.
- The cache-before/after-tool-change experiment is now unnecessary for the user scope (proposal 2 changes
  tools only when the cache is already cold). Drop it.
- **Confidence:** medium.

### 9. S2 as written is fine, minus the lock

- Caps, MEMORY.md injection (~470 tokens) and git are cheap and useful. The caps are no-ops for months
  at today's sizes; that is fine. The file lock exists only for the consolidator; drop it with
  proposal 3.

---

## Where the plan is too thin

1. **In-turn cost on heavy days is not addressed.** Trip days cost 3–10× quiet days, driven by
   multi-call travel turns that re-send ~34k per call. Compaction is post-turn, so nothing in S1–S5
   reduces the cost of turn 1's 26th call except decay. If spend matters, the levers are decay
   (proposal 2) and travel's 1.4k-token `manage_itinerary` docstring, which the plan explicitly
   leaves alone.
2. **Gemini history validity is asserted, not checked.** The plan rewrites history (compaction,
   tool-result clearing) and changes declarations mid-thread without a staging check for thought
   signatures on rewritten model turns. This is the first thing to test whichever compaction design ships.
3. **Cache reasoning ignores real idle gaps.** The plan and reports treat the tool block as a long-lived
   cached prefix ("accidentally cache-friendly"). The data says cross-turn caching exists only within
   bursts of a few minutes. Several design choices (decay only at compaction, tool-result clearing "only
   at compaction, never per turn") are protecting a cache that is cold at the moments they would act.
4. **No eval, but automated writers to injected memory.** §10 defers the eval harness while S5 starts
   auto-writing USER.md nightly. If S5 is ever built, a small fixed set of synthetic transcripts with
   expected ops is a prerequisite, not a follow-up.
5. **The episode store keeps everything forever, unredacted.** Full tool results include health data,
   fetched pages and whatever access codes live in memory files (`people_and_connections.md` is
   indexed as holding "logistical access codes"). The plan has a secret scan for the summary but none
   for the store, no retention for tool-result bodies, and no word on backups. A 90-day cap on tool
   bodies (keep owner/Jarvis text forever) costs one `DELETE` and removes most of the exposure.
6. **Heartbeat cadence is never questioned.** The plan rebuilds the tick's context but leaves
   `daily-log` every 3h and `step-challenge-tracker` every 4h. Fewer ticks beat cheaper ticks.
7. **Hot-path narrowing has no failure story.** "Write only when asked" plus a failed nightly run
   means silent loss; the plan's morning note would say "promoted 0", which is indistinguishable from
   "nothing worth promoting". (Moot if proposal 3 is taken.)
8. **Maintenance load is not counted.** The plan adds four model prompts (compaction, consolidator,
   daily-log prose, weekly replay), each a golden-test target and a model-upgrade regression risk, to a
   one-person project. That cost belongs in the decision table next to the token savings.

---

## Minimum viable redesign (≈80% of the value)

1. **HEARTBEAT.md edit now:** `daily-log` → once a day at ~23:00. No code.
2. **S0-lite:** log bound tool names per call and the due set per tick; one `countTokens` reading.
3. **Episode store-lite:** one SQLite table written from final state (full tool results, 90-day body
   retention), FTS5, `search_history` replacing the two history getters.
4. **Turn collapse:** `_add_and_trim` reduces finished turns to owner message + final reply (+ a
   tools-used stub). Staging-check Gemini thought signatures first.
5. **Idle-gap skill reset:** after ≥3h of owner silence, `active_skills` resets at the next turn.
   Update RUNTIME.md and the AGENTS.md "deactivate" line.
6. **Stateless ticks-lite:** throwaway thread per tick, due tasks' notes injected, fixed heartbeat
   skill set with fitness writers scoped to user.
7. **S2 as written** (caps, MEMORY.md injected, git on `jarvis_memory/`), no lock.
8. **`/memory` slash command** showing recent memory commits; undo via `git revert` behind it.
9. **Weekly audit task text** extended to prune expired dated entries in USER.md.
10. **Evidence gates, not slices:** build the consolidator only on repeated owner-reported memory misses;
    embeddings only on an observed FTS miss; LLM compaction only if collapsed windows still lose
    things the owner notices.
