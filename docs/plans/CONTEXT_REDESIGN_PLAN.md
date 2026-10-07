# Context Redesign — Plan

**Date:** 2026-10-07 · **Status:** decisions made, reviewed; no slice started.
**Problems addressed:** [context/PROBLEMS.md](context/PROBLEMS.md) A4, A5, A6, A7, A10, C1, C2, C3,
C4, D1, F2, plus #81 and #106, and the tool/skill cost the research measured (about half of every
user call).
**Research:** [LAYERED_MEMORY.md](context/reports/LAYERED_MEMORY.md) (the target model and why),
[COMPACTION_AND_DREAMING.md](context/reports/COMPACTION_AND_DREAMING.md) (procedures, prompts, op
schema), [TOOL_AND_SKILL_CONTEXT.md](context/reports/TOOL_AND_SKILL_CONTEXT.md) (tool and skill
cost), [DEVIN_MEMORY.md](context/reports/DEVIN_MEMORY.md) (Cognition's 2026-10-05 launch, checked
against this plan).
**Reviews:** four independent reviews of the first draft, summarized in
[context/reviews/SUMMARY.md](context/reviews/SUMMARY.md). Finding IDs (X1–X13) and the owner's
rulings (R1–R6) are cited where they changed the plan.

This plan states what is built and in what order. The reports hold the evidence and the full prompt
skeletons, and are not repeated here.

---

## Slices

**Branch flow (owner, 2026-10-07):** each slice is a PR against `feat/context-redesign`, which
merges to `main` only when all slices are done. The one exception is S0: it is measurement only, so
it goes to `main` early and gives real prod baselines; `main` is then merged back into the redesign
branch. Later slices are verified on staging and with dry runs, and read in prod after the final
merge.

Each slice ships and is verified on its own, with a before/after reading (§9). Design
detail is in §3–§8. Order: cheap wins first, then the store everything else depends on, then the
largest remaining cost (the heartbeat), then the two pieces that change behaviour most
(compaction, consolidation). Nothing discards or rewrites context until every turn is durably
recorded (S2).

**S0 — Instrument.**
- [ ] Per-call telemetry (E7), carried inside the existing `turns.jsonl` row as a `calls` list (no
      new log file): input, cache-read, output, reasoning, and the call's composition in chars
      (system prompt, tool schemas, history from earlier turns, this turn's messages so far).
- [ ] Per turn: the bound tool names. Per tick: the due-task set.
- [ ] A `job` telemetry context so LLM calls outside a turn (compaction, consolidator) are recorded
      in the same row shape, tagged by job.
- [ ] Staging measurements: Gemini `countTokens` on the bound declarations; cached-token share
      before and after a forced tool-set change; cached-input price for the Flash model in use.
- [ ] Before-reading recorded in §9.

**S1 — Quick wins.** No new subsystems.
- [ ] Skills reset after ~3h of owner silence, at the next turn's start (R2). Prod shows 0% cache
      hits after a 60-minute gap, so the reset costs no cache. RUNTIME.md's deferral note and the
      AGENTS.md "deactivate when no longer needed" line updated.
- [ ] Fitness writer tools scoped to user turns (they are bound on every tick and never called there).
- [ ] **Deploy step:** prod `HEARTBEAT.md` `daily-log` → once a night (R4). Until S2's "today" view,
      the chat side doesn't see silent heartbeat actions during the day; delivered messages still
      arrive through the mirror.
- [ ] Verify: skill-schema tokens per user call drop after idle gaps; a needed skill is re-activated
      within one round-trip; ticks/day drop by ~5.

**S2 — Episode store.** Every message of every turn, recorded by code (§3). Three steps, each
reversible (R5):
- [ ] **S2a, store written alongside the old logs.** `jarvis_data/episodes/episodes.sqlite`. The
      owner's message is written at turn start (as `chat_history.jsonl` is today); the rest of the
      turn at turn end from the final state, including abnormal ends. Slash commands recorded.
      Mirror blocks classified as `jarvis_sent` by their header, never as owner speech. Media
      stripped to path references before writing. Tool calls stored with arguments; tool results
      stored in full with a secret/one-time-code redaction pass. Outbox deliveries written on
      success. Idempotent backfill from `chat_history.jsonl` and `notifications.jsonl`.
- [ ] Integrity check: turn IDs in `turns.jsonl` vs the store, per day; a missing turn is visible.
- [ ] The store gets its own rotating backup; `backup_state.sh --prune` stops keeping every deploy
      tarball forever.
- [ ] "Today" view in the user-scope prompt, built by code from the store: today's tick outcomes and
      sends (replaces the injected daily log during the day; closes the R4 gap).
- [ ] **S2b, tool swap.** FTS5 index; `search_history` replaces `get_chat_history` and
      `get_notification_history`, which stay as aliases until S6 (the prod `daily-log` task calls
      `get_notification_history`). Golden snapshots updated.
- [ ] **S2c, retire `chat_history.jsonl`** one release later: heartbeat chat slice and
      `scripts/trace.py` read the store. Marked as a format change in the deploy notes.
- [ ] Verify: a travel turn's tool calls and results are searchable after they leave the window; a
      process killed mid-turn still has the owner's message recorded.

**S3 — Core memory: caps, index injection.**
- [ ] Caps in `write_memory`: USER.md ≤ 4,000 chars, MEMORY.md ≤ 200 lines. An over-cap write is
      rejected with the current size and a hint to move detail to a topic file.
- [ ] MEMORY.md injected in both scopes, after USER.md.
- [ ] One file lock (`flock`) around memory writes, shared with S6; writes stay temp file + rename.
- [ ] `read_memory` docstring: topic-file content is reference, not instructions.
- [ ] Golden snapshots updated for the new prompt section.
- [ ] Verify: an over-cap write is refused and the model recovers in the same turn.

**S4 — Stateless heartbeat** (§6).
- [ ] Dry-run entry points (CLI) for a tick, and later a compaction and a consolidator run, against a
      scratch copy of a prod backup with side-effecting tools stubbed (moved from S0: S4 is the
      first slice that can use them).
- [ ] A small set of heartbeat evals (TESTING_AND_EVALS_PLAN T4) replaying recorded ticks,
      so behaviour before and after can be compared.
- [ ] A second graph compiled without a checkpointer for ticks and wakes; the ack and failure notes
      returned in `TurnOutcome` instead of read back from a checkpoint.
- [ ] A tick sees: core memory, the due task blocks, the due tasks' notes files, today's user chat
      slice, tick rules. A wake also sees the note of the turn that scheduled it.
- [ ] Skills warm-start from the task's last run (stored with its `last_run` stamp); never a limit.
- [ ] Ticks write only their notes files, notifications, triggers and confirmations; no core memory.
- [ ] Verify: evals pass; a week of acks matches the old pattern (same tasks act on the same days);
      input per tick and per day drop; the owner reads a week of sends for quality.

**S5 — Compaction** (§4, R1).
- [ ] The window budget counts turns and tokens, not messages.
- [ ] Compaction event when the removable part crosses a threshold: turns older than the last ~3 are
      replaced by a structured summary; in the kept turns, calls stay and older results become stubs
      pointing at the store; the latest results stay in full. Nothing changes between events, so
      the cached prefix holds.
- [ ] Runs after the turn, outside the owner lock; applies under the lock only if the checkpoint is
      unchanged.
- [ ] Horizon: rows since the last consolidation or the last ~36h, whichever is longer (X7). Carried
      open requests re-verified against the whole store; they expire after a few days.
- [ ] `/clear` respected: rows before a clear are never re-derived; `/clear` runs under the owner
      lock.
- [ ] The backstop trim keeps the summary message (it is pinned, not the first thing evicted).
- [ ] Code validation, one corrective retry, deterministic fallback record, thrash guard.
- [ ] Skills unused in the kept tail and for ~24h deactivated at compaction too.
- [ ] Golden test on the summary framing prefix; staging probe that the model doesn't read the
      summary as owner speech.
- [ ] Verify (dry-run harness + staging): a long travel day compacts and Jarvis still answers about
      the morning; an open request survives; a finished one doesn't come back; tools keep being
      called after the summary; a cleared conversation stays cleared.

**S6 — Nightly consolidator** (§5, R3).
- [ ] Its own cron entry (~03:00 Israel) with a misfire grace that survives a restart.
- [ ] **Shadow mode first:** ops computed and validated, sent as a proposed diff in the morning
      note, nothing committed. The owner approves (all, or by number). Switches to auto-apply after
      ~2 weeks of good proposals.
- [ ] Seeding pass over the backfilled history as a dry run the owner approves before applying.
- [ ] Validation as designed, plus the review fixes: the expiry rule reads dates inside the entry
      text, not the "(observed …)" suffix; owner quotes must meet a minimum length and contain the
      entry's substance; USER.md ops need `kind: stated` backed by such a quote; ops dropped for a
      concurrent edit or a limit are retried next night; a forget request is exempt from the loss
      limit; the loss limit also applies per week.
- [ ] "Remember this" writes from the day are candidates like any other: validated, not exempt.
- [ ] Before applying, each file it changes is copied to `jarvis_data/memory_snapshots/<night>/`;
      `/dream undo [night]` restores a chosen night (the existing `/memory` command keeps its
      meaning). "Changed since read" is a content-hash check; the day's "remember this" writes come
      from the episode store's `write_memory` calls.
- [ ] A dead-man's-switch note queued before each run, cancelled by a successful one; a health
      footer in the morning note (store rows vs turns, compaction outcomes, free disk).
- [ ] No model call on a night with nothing new; the note says so. The note lists the previous
      day's "remember this" writes.
- [ ] Daily log written by the nightly job as a separate call (never promotion evidence).
- [ ] Hot-path writes narrowed: AGENTS.md "write to memory proactively" → "write when the owner
      asks you to remember something".
- [ ] **Deploy step:** a `pre-s6` backup of `jarvis_memory/`; a written multi-night revert procedure.
- [ ] Verify: in shadow mode, a stated preference appears as a proposed op with an observed date; a
      changed preference proposes a supersede; a forced invalid run writes nothing and says why; a
      killed run leaves no partial state.

**S7 — Last, and gated.**
- [ ] Embeddings over the episode store (sqlite-vec, fused with FTS5); keyword results still
      returned when embeddings are unavailable.
- [ ] One home per rule: tool rules in docstrings; duplicates removed from AGENTS.md, heartbeat.md
      and SKILL.md bodies; worked examples kept.
- [ ] Schema budget per namespace and for core, checked in `tests/golden`.
- [ ] Consolidate the media getters; split fitness read/write.
- [ ] *Gated:* weekly replay (re-read 7 days for cross-day patterns; replaces `memory-index-audit`)
      only if nightly runs visibly miss such patterns.
- [ ] *Gated:* flag topic files no turn has read for ~60 days, if stale files become a problem.
- [ ] **Deploy step:** remove the `get_*_history` aliases once no task text names them.

---

## 1. Problem

Jarvis's storage already has the shape the reference systems converged on: markdown files, a small
injected core, topic files read on demand, skills as procedural memory. What it lacks is the
machinery around that storage:

- **The window fills with tool traffic and forgets silently.** The 50-message cap counts each tool
  call and result as a message; in the live owner window 40 of 54 messages were tool messages and 5
  were the owner's. Old turns are dropped with no summary, and tool results and heartbeat turns
  have no durable record at all.
- **No search.** Memory is reachable by filename; past chat by guessing a time range, in
  200-character stubs.
- **No designated writer.** The agent writes memory ad hoc mid-conversation, a 3-hourly task
  rewrites the daily log, and nothing bounds the injected files.
- **A heartbeat that replays itself.** Ticks run on a persistent thread that re-sends prior ticks
  on every call (78.5k input per tick, 29 of 33 ticks with no action, prod 10-03..05).
- **Tools that never leave.** Skills activate and stay: about 15.5k schema tokens plus 2.9k of
  skill rules on every user call, about half the input (`deactivate_skill`: 2 calls against 90
  activations).

## 2. Target model

| Layer | Holds | Written by | Reaches the model |
|---|---|---|---|
| **L1 Working context** | The recent turns + a compaction summary | Code (compaction) | Every call |
| **L2 Episode store** | Every message of every thread | Code, as turns happen | `search_history`; the "today" view |
| **L3 Core memory** | SOUL.md, USER.md, MEMORY.md index | Owner; "remember this"; the nightly job | Injected every call (capped) |
| **L3 Topic files** | Detail, read on demand | Agent on request; the nightly job | Memory tools |
| **L4 Consolidation** | Ops against L3; the daily log | One nightly job | Never directly |
| **L5 Background work** | Ticks and wakes | Their own notes files only | Fresh context per run |

Kept as is: per-call prompt rebuild (hot reload), the owner thread spine, the user-role pending
mirror (checked in prod 2026-10-06: no sign of the hermes #118863 failure), the gate and its stamps,
the Outbox, the SOUL.md confirmation flow.

## 3. Episode store (S2)

**Location.** `jarvis_data/episodes/episodes.sqlite`: tool-opaque, so it belongs in `jarvis_data/`
by the placement principle.

**Row.** `id`, `ts` (UTC), `turn_id`, `thread_id`, `scope`, `role` (`owner | jarvis | tool_call |
tool_result | jarvis_sent | system_record`), `provenance` (`owner_chat | heartbeat | wake | mirror |
compaction | backfill | slash_command`), `tool_name`, `content` (full text, redacted; media as a
path reference), `channel`.

**Write path.** Owner row at turn start; the rest at turn end in one transaction; Outbox deliveries
on success. A failed store write never fails the turn; the integrity check makes it visible.

**Search.** FTS5 over `content`, bm25 with a recency tiebreak. `search_history(query, since, until,
thread, provenance, limit)` returns full rows grouped by turn, with a size cap per result. The
schema leaves room for a vector table keyed by row id (S7).

**Retention.** Forever (decision 9). Expected size with full tool results: roughly 50–150 MB a year;
tracked in the health footer.

## 4. Compaction (S5)

Design, prompt skeleton, JSON state and validation: COMPACTION_AND_DREAMING.md "A compaction design
for Jarvis". Every reference system summarizes; most also clear old tool results first. This plan
does both, at one event:

- **Budget** in turns and tokens. A tool-heavy turn no longer crowds out the conversation.
- **At a compaction event:** turns older than the last ~3 become one structured summary. Code fills
  the anchored sections (the owner's own messages verbatim, identifiers, live reminders and wakes
  from the trigger store, skills used today); the model fills JSON (open requests, done, owner
  rules, learned, decisions, lookup hints) that code checks. In the kept turns, calls stay and older
  results become stubs (`[result cleared — 14 KB, searchable as E2041]`).
- **Between events** the window only grows, so the cached prefix holds.
- **Placement:** one user-role message at the head of the window, framing prefix in `prompts/`,
  golden-tested; pinned so the backstop trim never evicts it first.
- **Horizon:** since the last consolidation or the last ~36h, whichever is longer, so a late-evening
  rule survives the night.
- **Failure:** a deterministic record built by code plus a marker; a thrash guard.

## 5. Nightly consolidator (S6)

Design, prompt skeleton, op schema and validation: COMPACTION_AND_DREAMING.md "A consolidator design
for Jarvis". It is not a summarizer: it proposes specific edits to long-term memory, each quoting the
owner, and code validates them.

- **Inputs:** owner and Jarvis rows from interactive threads since the cursor (heartbeat turns, tool
  results, mirrors, compaction records and daily logs are not evidence); USER.md, the index and
  agent-written topic files with per-run handles; the day's "remember this" writes; expiry
  candidates. SOUL.md read-only.
- **Output:** ops (`add | update | merge | supersede | remove | index_upsert | index_remove |
  flag`); entry text ≤ 240 chars ending "(observed YYYY-MM-DD)".
- **Scope:** USER.md, the index, existing agent-written topic files. Never creates files (flags
  them); never touches SOUL.md, HEARTBEAT.md, daily logs or skill-owned data.
- **Inferences:** never written to USER.md; asked as questions in the morning note.
- **Application:** shadow mode first (R3). Then: invalid ops dropped and retried next night; a file
  that would break a hard limit has its ops rejected; no append fallback; old values replaced in
  place, with the night's snapshot and the store keeping history; the cursor advances after the
  write completes.
- **Note:** composed by code, sent in the morning window through the Outbox, with a health footer.
- **Model:** Flash; upgrade only if readings show missed or wrong proposals.

## 6. Stateless heartbeat (S4)

- No persistent `heartbeat` checkpoint: ticks and wakes run on a graph without a checkpointer. Ticks
  are recorded in L2, so "why did it nudge me" stays answerable.
- Continuity: the due tasks' `heartbeat/*.md` notes (their narrative state, which live tasks already
  depend on), the task's `last_run`, and today's user chat slice.
- Skills warm-start from the task's last run; the tick can activate anything.
- Unchanged: the gate, `due:` windows, gated tasks, `heartbeat_respond`, Outbox delivery,
  stamp-after-delivery.
- This reverses the documented "mixed tick history dilutes the in-context pattern" choice: the
  replayed thread is the largest slice of every tick (A4) and the mechanism behind F2.

## 7. Tools and skills

- **Idle reset** (S1) and **decay at compaction** (S5) are the main levers.
- **Warm start** on ticks (S4).
- **One home per rule, schema budget in CI, media/fitness consolidation** (S7). Docstrings drive
  behaviour (F1), so any docstring change ships with a staging behaviour check.

## 8. Decisions

Settled with the owner on 2026-10-06 and 2026-10-07. Options and evidence are in the reports and
the review summary.

| # | Decision | Choice |
|---|---|---|
| 1 | Heartbeat context | Stateless ticks; skills warm-start, never restricted |
| 2 | Pending mirror | Keep user-role (checked clean in prod) |
| 3 | MEMORY.md | Injected, capped at 200 lines |
| 4 | Who writes core memory | Hybrid: "remember this" immediately, everything else nightly; shadow mode, then auto-apply with snapshots, morning note, undo (R3) |
| 5 | Old context | Compact: summary + last ~3 turns, old tool results stubbed, budget in turns and tokens (R1) |
| 6 | Consolidator model | Flash, nightly; upgrade on evidence |
| 7 | Search and history | FTS5 first, embeddings last; git for memory postponed (2026-10-07) |
| 8 | Caps | USER.md 4,000 chars; MEMORY.md 200 lines; SOUL.md uncapped; over-cap writes rejected |
| 9 | Episode store | Replaces `chat_history.jsonl` in three steps (R5); forever; full tool results; all ticks |
| 10 | Daily logs | Kept; once a night now (R4), by the nightly job from S6 |
| 11 | Consolidator visibility | Note every morning, "promoted 0 because …" when nothing changed |
| R2 | Skill unloading | After ~3h of owner silence, plus at compaction |
| R6 | Weekly replay | Only if nightly runs miss cross-day patterns |
| C1–C8 | Compaction | Recommendations in COMPACTION_AND_DREAMING.md, as amended in §4 |
| D1–D9 | Consolidation | Recommendations in COMPACTION_AND_DREAMING.md, as amended in §5 |
| T1–T6 | Tools | Decay; warm start; measure first; one home per rule; schema budget; consolidate outliers |

**Settled by measurement, not by asking:** the compaction thresholds, Flash vs Pro for the
consolidator, and whether tool-set changes cost enough cache to matter (S0).

## 9. Readings

Before/after per slice from prod, over full Israel days with no paused tasks in the window (E10).
User traffic is light (2–25 turns a day), so windows are at least 5 days and confounders (new tasks,
tool changes) are noted beside each reading.

| | Before | S1 | S2 | S3 | S4 | S5 | S6 |
|---|---|---|---|---|---|---|---|
| user input / call | | | | | | | |
| user input / turn | | | | | | | |
| heartbeat input / tick | | | | | | | |
| heartbeat input / day | | | | | | | |
| cache-read share | | | | | | | |
| tool + skill share of a user call | | | | | | | |
| new jobs' LLM spend / day | — | — | — | — | — | | |
| compaction p95 latency | — | — | — | — | — | | |
| episode store size / day | — | — | | | | | |

**Quality rows**, with the threshold that means the slice failed:

| Row | Slice | Failure if |
|---|---|---|
| Missing turns in the store (integrity check) | S2 | any, for 2 days running |
| Skill re-activations within 1 turn of a reset | S1, S5 | > 30% of resets |
| Tick acks diverging from the old pattern | S4 | a task stops acting when it should |
| Compaction outcome `fallback` | S5 | > 1 in 5 events |
| Revived finished tasks after compaction (staging probe) | S5 | any |
| Validator rejections per night | S6 | > half of proposed ops for a week |
| Nights with zero proposals while the owner chatted | S6 | 5 in a row |
| `search_history` calls per week | S2b | informational |

## 10. Not decided here

- **Git for `jarvis_memory/` — postponed (owner, 2026-10-07).** Not required by this redesign:
  nightly snapshots cover undo, and the episode store covers "what changed". If revived: the git dir
  must live outside the memory dir (`threads.sqlite*` sits there; a `.git` inside is writable by the
  memory tools), commits by code after every write path with explicit file paths only, and a
  reconcile step that commits the owner's hand edits (review X1, CODEBASE B1, OPERATIONS B1).

- **Open for the owner:** whether memory entries carry their episode ID
  (`(observed 2026-10-05; E2041)`). Default: no; the morning note and the night's snapshot record each op's evidence.
- **Open for the owner:** retention of tool-result *bodies*. Decision 9 says forever; two reviewers
  suggest 90–180 days for disk and privacy (metadata and calls kept forever). Revisit with the S2
  size readings.
- Which embedding model, and whether it runs via the API or locally (S7).
- Telemetry storage: stays separate from the episode store (owner, 2026-10-07: production content
  and telemetry don't share a database). Whether telemetry itself moves from JSONL to its own
  SQLite is not decided here.
- Event-conditioned "standing intents" in the triggers system.
- Cleanup of stale checkpoints in `threads.sqlite` (#12).
- Re-check Devin's launch around 2026-10-20 for the first user failure reports (DEVIN_MEMORY.md).
