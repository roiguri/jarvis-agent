# Context Handling — Problem Inventory

**Date:** 2026-08-06.
**Companion:** [RESEARCH.md](RESEARCH.md) — reference-system comparison, measurement method, and the
code-level findings retained alongside this inventory.
**Sources:** `CONTEXT_HANDLING_PLAN.md` and `TEST_HARNESS_PLAN.md`, both **deleted 2026-08-06** and
recoverable from git history; the unmerged WS2 rewrite on PR #70 (`docs/ws2-time-grounding`); and a
fresh telemetry reading taken for this document (§0).
**Issue:** #18 — the "reduce token spend" umbrella, and the only live issue reference inherited from
the deleted plans (see RESEARCH.md §4).

**What this is.** Every problem those plans exist to address, stated as a problem and nothing else.
No designs, no workstreams, no sequencing, no proposed fixes. It exists so each issue can be
re-analyzed on its own merits rather than inherited from a solution that was already chosen for it.

**What this is not.** A plan. It deliberately says nothing about what to build, in what order, or
whether to build anything at all. Several entries below may be worth accepting rather than fixing;
that judgement is a separate exercise and is not made here.

**Reading the tags.**

| Tag | Meaning |
|---|---|
| `MEASURED` | A number was taken from live data, and the reading is recorded here or in a linked doc |
| `STALE` | Measured once, but the measurement predates changes that plausibly moved it |
| `ASSERTED` | Stated in a plan without a recorded reading, or carried over from a lost draft |
| `RESOLVED` | No longer true; kept because the roadmap is still written as though it were |

The distinction matters here more than usual: this roadmap's own central finding (§E) is that a
cost claim was believed for three days without anyone reading the instrument.

---

## 0. Fresh reading (2026-08-06)

Taken from `turns.jsonl` over the seven days to 2026-08-06, against the same window shape as the
plan's 2026-07-10..07-16 baseline. Recorded here because several entries below are ranked on the
older numbers.

| | Baseline (07-10..16) | Now (07-31..08-06) |
|---|---|---|
| heartbeat input / turn | 82.9k | **70.0k** |
| heartbeat LLM calls / turn | 4.50 | **4.36** |
| heartbeat tool calls / turn | ~7.3 | **~6.5** |
| heartbeat rows / day | 17.9 | **16.1** |
| heartbeat input / day | 1.60M | **~1.2M** |
| user input / turn | 46.7k | **78.6k** |
| heartbeat share of all input | 83% | **63%** |

Two shifts worth carrying into any re-analysis: per-tick input fell ~16% while **round-trips per
tick did not move at all**, and the user scope is growing fast enough that the heartbeat's share of
spend is no longer the ~83% the roadmap's ranking assumes.

---

## A. Structural spend

**A1 — The heartbeat dominates token spend.** `STALE`
83% of all input tokens at the 07-16 measurement (88.0M heartbeat vs 17.9M user). Now 63% (§0) —
not because the heartbeat got cheap, but because user-scope spend nearly doubled as skills were
added. The imbalance is real; the ratio the roadmap ranks on is not current.

**A2 — Per-tick cost rose after gating shipped, rather than falling.** `MEASURED`
~68k/tick in early July → 90–114k by 07-16, with a no-op tick at 214,008.

**A3 — A tick spends ~5 sequential LLM round-trips and ~9 tool calls to perform one 785ms unit of
real work.** `MEASURED`
Everything else in the trace is bookkeeping the harness asks for and then discards. Round-trips per
tick are essentially unchanged since (§0), so this is the least-moved item on the list.

**A4 — The full message history is re-sent on every call within a turn.** `MEASURED`
The `heartbeat` checkpoint row is 108,785 bytes ≈ ~27k tokens ≈ 63% of each call's input, paid
~5 times per tick.

**A5 — Tool results accrue inside a turn and are re-sent with every subsequent call.** `MEASURED`
A further ~18% of each call. With A4, ~81% of a call's input is material the model has already seen
in that same turn.

**A6 — Retrieving the notes files costs two orders of magnitude more than the notes contain.**
`MEASURED`
All eight `heartbeat/*.md` files together are 1,300 bytes (~325 tokens). A tick spends ~3
`read_memory` round-trips to reach them, each re-sending the full ~43k-token context.

**A7 — The daily log is rewritten on the hourly path.** `MEASURED`
An hourly check that finds nothing still reads and rewrites ~1.3KB of prose about finding nothing.

**A8 — The gate almost never closes, because of how tasks are defined rather than how the gate
works.** `MEASURED`
22% observed skip rate, against a mathematical ceiling of ~33%. One task carries a 16-hour window on
an hourly cadence, so it is due 16 times a day, every day. Zero gate errors in 7 days — the
mechanism is correct.

**A9 — One task wakes a full LLM turn to confirm that an empty list is still empty.** `MEASURED`
Its stored schedule is `[]`. It was the only due task on 7 of 12 sampled ticks; at ~114k input/tick
that is ~800k input tokens/day.

**A10 — The heartbeat prompt carries conversational identity a tick does not need.** `ASSERTED`
Full SOUL.md, USER.md and yesterday's daily log are injected to decide whether a class ended. The
size of this slice is measured (~10% of a call); that it is *unnecessary* is a judgement, and its
named risk — blander or wronger briefing text — has never been tested.

---

## B. Prompt and cache stability

**B1 — The model's "now" moves underneath it mid-turn.** `MEASURED`
`build_system_prompt` is called inside `_llm_node`, so the `[Current time: … HH:MM]` line is rebuilt
on every LLM call. Within one turn, call 1 can read `06:14` and call 4 `06:15` — including on turns
computing a reminder's `fire_at`. Nothing logs it and nothing tests it.

**B2 — A per-minute clock in the system prompt invalidates the whole request prefix, not just its
own line.** `ASSERTED`
The cache prefix spans system instruction + tools + history, so the clock sits upstream of the 63%
in A4. 64% of heartbeat turns cross a minute boundary mid-turn. **This finding comes from a design
pass whose draft was never committed and no longer exists**; it has not been re-verified against
current Gemini documentation. Treat as unconfirmed until it is.

**B3 — The 50-message cap breaks the history prefix independently of the clock.** `ASSERTED`
Both threads sit at `MAX_MESSAGES`, and the reducer hard-slices to exactly that on every state
update — after each model return *and* each tool return — so the head shifts several times inside a
single turn. Fixing B1/B2 alone would leave this untouched.

**B4 — Every reminder requires a hand timezone conversion.** `ASSERTED`
`prompts/AGENTS.md` requires `fire_at` in ISO 8601 UTC while every user-facing time is Israel-local,
so the model converts by hand on each one — and reminders originate mostly in user turns. *(Raised
only on PR #70; not in either committed plan.)*

---

## C. Context capacity, recall, and continuity

**C1 — Injected files are uncapped.** `MEASURED`
SOUL.md, USER.md, HEARTBEAT.md and the daily logs are injected whole via `load_or_blank`. One
verbose daily log inflates every turn for that day, and nothing bounds growth in any of them.

**C2 — Recall requires already knowing the filename.** `ASSERTED`
Memory is reachable only by exact name through the MEMORY.md index. "What did we decide about X last
month" has no path.

**C3 — Conversation history beyond the 50-message window is unreachable except by guessing a time
range.** `ASSERTED`
`get_chat_history(since=…)` is the only door, and it requires knowing roughly when.

**C4 — Nothing pushes back on memory-file growth.** `ASSERTED`
USER.md and MEMORY.md are injected into every prompt in both scopes, so every line added is a
permanent per-turn tax, with no pressure to curate between weekly audit passes.

**C5 — A message the heartbeat sent is not part of the conversation the user scope sees, so replies
to it land without an antecedent.** `RESOLVED`
The owner experiences this as conversations that feel glitchy and out of context. `notify_owner`
writes the sent text to `notifications.jsonl` only — it never enters the telegram thread's
checkpoint and is never appended to `chat_history.jsonl` under that thread. The user scope recovers
it as `_load_recent_heartbeat_notifications()` (`agent.py:343`): flat `[HH:MM] text` lines in the
*system prompt*. Four gaps follow, and they compound:

1. **The reply has no antecedent.** The owner answers a briefing, and the thread's message history
   begins with their answer — the assistant turn that prompted it is not in the window.
2. **It reads as context, not as its own prior utterance.** A system-prompt line presents as a fact
   about the world rather than as something Jarvis said and is accountable for.
3. **Truncation removes what the reply is about.** Each entry is capped at 240 chars, while the
   owner is typically responding to a specific detail — often the part that was cut.
4. **Silent action and yesterday's messages are invisible.** Only `event == "heartbeat"` rows from
   after Israel midnight are loaded, last 20. A tick that acted without notifying leaves no trace at
   all, and a 23:00 briefing is gone by 08:00.

The reverse direction has the same shape but is better served: the heartbeat scope receives today's
user chat with roles and timestamps (`_load_recent_user_chat`, 60 entries), which is nearer to a
transcript than to a summary — though still injected as prompt text rather than as history.

Resolved by the context plan's phases 1a–1c (PR #102, prod since 2026-08-25;
[../archive/CONTEXT_PLAN.md](../archive/CONTEXT_PLAN.md)): delivered notifications are mirrored
into the one `owner` thread as real history on the next user turn, and the prompt slice is gone.
Gaps 1–3 are closed outright; of gap 4, yesterday's messages now persist in the window, while a
tick that acted *without* notifying is still invisible — that remainder is filed as #106.

---

## D. State hygiene

**D1 — Heartbeat notes files are machine state hand-serialized as prose, and they drift.** `MEASURED`
Four different spellings of "when did this last happen" (`last_checked`, `last_checked_date`,
`last_sync_date`, `last_notified`); one file missing the header the others carry; three holding no
state at all; and two that already disagree with each other about the same fact. `last_run` was moved
out to code-owned `state.json` for exactly this reason — the rest stayed behind.

---

## E. Measurement and verification

This cluster was the test-harness plan's entire subject, and the roadmap's central finding.

**E1 — Nothing reads the instrument.** `MEASURED`
`observability/usage.py` exists, is correct, and nothing runs it. No test, no CI job, no alert, no
scheduled report. The gap is not missing instrumentation — adding more meters would not have helped.

**E2 — A metric regressed ~68% in plain sight for three days while the roadmap said "verified in
production."** `MEASURED`
Every number needed to catch it was already sitting in `turns.jsonl`.

**E3 — Verification claims get written from recall rather than from a reading.** `MEASURED`
The gating work's headline cost claim — "most hours: no LLM call at all" — was recorded that way and
was wrong. Deploys are manual, so every "after" reading lands hours or days after the commit, which
is exactly when recall substitutes itself.

**E4 — There is no test suite.** `MEASURED`
`pytest` is not in `requirements.txt`; no `tests/` tree exists. *(The related half of this — no way
to point the agent at a throwaway state tree — is `RESOLVED`: `JARVIS_ROOT` shipped with the staging
work, and `scripts/ci/check_paths.py` enforces it on every PR.)*

**E5 — There is no regression check on the metric the entire roadmap optimizes.** `MEASURED`
Per-call input, checkpoint weight, prompt size and cache ratio are all computable and none is
asserted anywhere.

**E6 — A gated tick is indistinguishable from a dead service.** `MEASURED`
Both produce an absent row in `turns.jsonl`. Fewer rows is the measurement, which makes the ambiguity
load-bearing rather than cosmetic.

**E7 — Per-turn telemetry cannot attribute a cache miss to a specific call.** `MEASURED`
`record_llm_call` sums into a turn accumulator, so a tick's ~5 calls report as one number — enough to
see that caching is poor, not enough to see what broke it. *(Raised only on PR #70.)*

**E8 — The baselines have been materially wrong twice.** `MEASURED`
The original per-tick baseline was ~2x off, and every dollar figure in the roadmap was 6.5x low until
`MODEL_PRICES` was corrected. Token and turn counts were always right; the derived figures were not.

**E9 — A target was set without checking the composition it targeted.** `MEASURED`
"Tick input 66k → ≤15k" was arithmetically unreachable by its own method: the system prompt is ~4.5k
of a ~42.8k call, so deleting all of it still leaves ~38k. The idea was sound; nobody checked the
denominator before committing to the number.

---

## F. How behaviour actually gets driven

Recorded from a single failed change. Kept because it is the most transferable material in either
document, and currently the least discoverable — it lives in an appendix.

**F1 — Tool-usage behaviour was steered by editing prompt prose, when the real driver was a tool
docstring.** `MEASURED`
The change was scoped without grepping the tool surface. A grep across `prompts/`, `agent.py` and
`heartbeat.py` returned nothing — the docstring was the only remaining driver, and it was never in
scope. The model's calls reproduced the docstring example's exact shape, not the removed
instruction's.

**F2 — A polluted checkpoint re-seeds its own behaviour and will not self-clear.** `MEASURED`
Three identical wrong-shaped calls sat in the 50-message window and kept the pattern alive after both
the prose and the docstring were corrected. In-context imitation was the last remaining driver, and
the window was feeding itself.

**F3 — A tool docstring's example was silently a live correctness bug.** `RESOLVED`
A UTC-midnight bound in an example dropped 00:00–03:00 Israel time from every fold-in. Found only
because something else was being investigated.

---

## G. Tracking

**G1 — Most inherited issue cross-references do not resolve.** `MEASURED`
The deleted plans cite #33, #54 and #20 for the umbrella, the compaction analysis and the
event-driven-heartbeat deferral. In this repository those numbers belong to unrelated issues (two of
them closed) — the plans were written against an earlier repository whose numbering does not map,
and only some citations were tagged as such. #18 is the only correct one. Full table in
[RESEARCH.md](RESEARCH.md) §4.

**G2 — The work that was ranked most valuable was never filed as an issue.** `MEASURED`
The heartbeat tick-ceremony analysis existed only inside a plan document. So did the compaction
analysis and the event-driven-heartbeat constraints — per G1 they are untracked, not filed elsewhere.

**G3 — The test-harness work carried "issue: TBD" from 2026-07-16 until deletion.** `MEASURED`
Noted in the document itself, for three weeks, without being filed.

**G4 — #18's own description no longer matches what was measured.** `MEASURED`
The umbrella still frames the problem as "most of ~720 turns/month render a full prompt only to
return `[NO_ACTION]`" and "no prompt/context caching". The gate shipped; the skip rate is 22%; and
the dominant cost turned out to be re-sent history rather than prompt reassembly (A4). Anyone
reading the issue rather than this file gets the pre-measurement picture.

---

## Issue map

The **only** place issue numbers appear in this file. Inline citations are what rotted in the
deleted plans — four numbers, cited in prose, all stale, unnoticed for weeks (G1). One table is one
thing to re-check.

Relationships are stated rather than implied, because "related" is what let those citations decay
without anyone testing them:

- **instance of** — the issue is a concrete observed case of a systemic entry here.
- **complement** — adjacent failures of one concern; neither contains the other, both must exist.
- **stated here** — this file holds the problem; the issue tracks the work and points back.
- **stated there** — the issue is the better record; the entry here is a pointer, not a restatement.
- **dependency** — not the same problem, but work on the entry has to touch it.

| Entry | Issue | Relationship |
|---|---|---|
| E4 | #1 | stated there — no automated verification exists |
| E5 | #5 | stated there — nothing gates a change on its way to production |
| E1, E5 | #55 | instance of — the instrument is unread, and misreports when read |
| E1, E6 | #36 | instance of, and wider — covers both scopes, and containment as well as legibility |
| C1 | #81 | complement — C1 is unbounded injection, #81 is unrecoverable truncation |
| C3 | #61, #60 | complement — content Jarvis saw once and cannot reach again |
| C5 | #50 | stated here — resolved; #50 closed on phases 1a–1c |
| B1, B4 | #24 | dependency — the Israel-time duplication any clock work must cross |
| — | #18 | the umbrella; points at this file rather than restating it |

Each mapped issue carries a one-line pointer back to its entry, so the link survives from either
end. #24 is mapped but not reframed: its title still names a fix rather than the duplication it
addresses, and tidying that is general backlog hygiene rather than context work.

Deliberately unmapped: **#12** (`threads.sqlite` disk footprint) is adjacent to A4 by subject and
unrelated by axis — disk bytes, not re-sent tokens. Listing it would be pattern-matching on
adjacency.

**#59 was folded into #36** on 2026-08-06. It described the same failure as #36's second incident,
down to the same `turn_id`, and had been split out on the grounds that it was the independently
shippable half. That is a plan decision shaping a problem record, which is the thing this file
exists to stop.

---

## Two observations from compiling this

**F1–F3 are not problems the roadmap set out to solve.** They are the post-mortem of one failed
change, retained as a case study. They are also the most reusable content in either plan and the
hardest to find.

**Roughly a third of this list is not about token cost.** C1–C5 and D1 are capability, continuity
and data hygiene. They sat inside a document whose title, premise and ranking were entirely about
spend, which is a substantial part of why that document was hard to act on.
