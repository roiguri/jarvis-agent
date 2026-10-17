# Review Summary — Context Redesign Plan

**Date:** 2026-10-06 · **Reviewed:** [../../CONTEXT_REDESIGN_PLAN.md](../../CONTEXT_REDESIGN_PLAN.md)
(uncommitted draft, after the Devin changes).
**Reviewers:** four independent agents, each started without this conversation's context.

| Review | Angle | Blockers | Major | Minor |
|---|---|---|---|---|
| [REVIEW_CODEBASE.md](REVIEW_CODEBASE.md) | Does the plan match the code? | 1 | 11 | 12 |
| [REVIEW_DESIGN.md](REVIEW_DESIGN.md) | Does it follow from the research; is the design sound? | 1 | 10 | 15 |
| [REVIEW_OPERATIONS.md](REVIEW_OPERATIONS.md) | Sequencing, rollback, deploy, failure visibility | 2 | 10 | 8 |
| [REVIEW_SIMPLICITY.md](REVIEW_SIMPLICITY.md) | Is it overbuilt for one user? | — | — | — |

**Overall:** every reviewer accepts the target model. The problems are at the seams between
slices, in rollback and failure visibility, and in places where the plan assumes code behaviour
that isn't there. One reviewer argues that two deterministic code changes deliver most of the
value. That needs a ruling from the owner (§2).

---

## 1. Findings raised by more than one reviewer

These are the most reliable: independent reviewers reached them separately.

| # | Finding | Raised by | Severity | Proposed response |
|---|---|---|---|---|
| X1 | **Git in `jarvis_memory/` can pull in `threads.sqlite*`** (≈170 MB, changes every turn). A `.git` deny-list keyed by filename prefix still lets `write_memory(".git/config")` through, which allows code execution through git config. The app's memory browser would list `.git`. | Codebase B1, Ops B1 | Blocker | Keep the repo outside: `$DATA_DIR/memory_git` with `jarvis_memory/` as its work tree. Commit named files only. Add a test that sqlite is never tracked. Turn off auto-gc and run gc nightly. |
| X2 | **S1 breaks prod's `daily-log` task**: its text calls `get_notification_history`, which S1 removes. | Codebase M8, Design M8, Ops | Major | Keep the old tool names as aliases until S5, or add the HEARTBEAT.md edit as an S1 deploy step. |
| X3 | **Writing episodes at turn end is a durability regression.** Today the owner's message is logged *before* the turn. A crash or restart mid-turn would lose it. Slash commands never reach `ask_jarvis`. | Codebase M3, Ops, Design (minor) | Major | Write the owner row at turn start and the rest at turn end. Record slash commands. The integrity check compares turn IDs, not just row counts. |
| X4 | **Compaction's lock and timing don't exist as written.** The owner lock is released before the reply is delivered, and holding it through the summary call would stall the next message. | Codebase M5, Ops | Major | Run the summary outside the lock, then re-check the checkpoint ID under the lock before applying. Trigger on the *removable* size, so a big tail doesn't re-trigger every turn. |
| X5 | **The S5 consolidator has no rollback or dry-run.** It auto-applies from night one, including a seeding pass over 90 days. `/memory undo` reverts only the last commit and breaks on dirty trees or later edits. It also collides with the existing `/memory <file>` command. | Ops B2, Codebase M11, Design (minor) | Blocker | **Shadow mode first**: ops proposed in the morning note, nothing committed. The seeding pass runs as an owner-approved dry run. Commits are tagged per night. Undo targets a specific night (and the command gets a new name). There's a `pre-s5` backup step. |
| X6 | **Moving daily logs to the nightly job leaves "today" empty.** The user scope injects today's log (`agent.py:445`) and a live heartbeat task reads it. The compaction summary can't replace it, because ticks that didn't notify never reach the owner thread. | Design B1/M7, Codebase M10 | Blocker (with X7) | Keep a cheap "today" view written by code (today's sends, tick outcomes and owner messages from L2). Or keep a lighter daily log written during the day. |
| X7 | **A daily forgetting boundary.** Compaction only covers rows since the last nightly run, and the consolidator drops short-lived context by design. A rule stated at 22:00 disappears at the next morning's compaction. Open requests from before the cursor fail the plan's own quote check. | Design B1 | Blocker | Set the compaction horizon to max(since cursor, last ~36h). Carried open requests are checked against the whole store and expire after N days. State what each scope injects after S5. |
| X8 | **S4's assumptions don't hold.** The ack, failure notes and turn bookkeeping all read the heartbeat checkpoint. A new thread ID per tick *grows* the DB. A tick's ack combines tasks, so "per-task last output" can't be derived. Notes files carry cross-task state that live tasks depend on. | Codebase M1/M2, Design M7 | Major | Compile a second graph without a checkpointer and return the ack in `TurnOutcome`. Keep `heartbeat/*.md` as each task's narrative state (inject the due tasks' notes) instead of a new per-task store. Fix the "read-only" wording, since ticks do write notes. |
| X9 | **The owner never sees a failure.** Every signal lands in telemetry nobody reads, and a missing morning note looks like a quiet morning. | Ops, Design M9 | Major | A dead-man's-switch note queued *before* the nightly run. A health footer in the note: episode rows vs turns, compaction outcomes, git state, free disk. |
| X10 | **§9 Readings can't show that a slice failed.** There are no thresholds, no quality rows (compaction outcomes, validator rejections, zero-promotion nights, re-activations after decay, `search_history` use), no S2/S6 columns, and no line for the new jobs' own LLM spend. Also, `record_llm_call` doesn't record anything outside a turn. | Design M9, Ops, Codebase M9 | Major | Add failure thresholds and quality rows. Make telemetry record calls made outside a turn. |
| X11 | **The mirror block and base64 media would be stored wrongly.** The mirror is a user-role message and would be stored as the owner speaking, which lets the consolidator's quote check accept Jarvis's own words. Media is still in the final state when it's written. | Codebase M7, Design (role taxonomy) | Major | Classify mirror messages by their header at write time. Strip media before writing. |
| X12 | **Disk and privacy.** "1.4 MB in three months" counted only final text. With full tool results it's roughly 50–150 MB a year. Backups keep every deploy tarball (34 so far). Health data and access codes would be stored unredacted forever. | Ops, Simplicity | Major | Set a retention policy for tool-result *bodies* (e.g. 90–180 days; metadata kept forever). Redact secrets on write. The store gets its own rotating backup. Fix `--prune`. |
| X13 | **Order.** Heartbeat input matches or exceeds user input. S4 doesn't depend on S3 and is easier to roll back, while S3 is the riskiest slice. | Ops, Simplicity | Major | Proposed order: S0 → S1a (store, written alongside the old logs) → S2 → T4 heartbeat evals → S4 → S1b/c → S3 → S5 → S6. |

## 2. Challenges to settled decisions — need your ruling

| # | Challenge | From | Argument | My take |
|---|---|---|---|---|
| R1 | **Replace LLM compaction with "turn collapse"** (decision 5, C1–C8) | Simplicity | Of the 54 messages in the owner window, only 5 are yours and 40 are tool messages. Collapsing finished turns to *your message + the final reply* (deterministic, in `_add_and_trim`) stretches the window from about 5 turns to about 25 with no LLM. Gemini's handling of stripped tool calls and thought signatures needs a staging check. | **Strong.** It's cheap and deterministic, and it removes the main reason for most compaction machinery. I'd make it S3's first step and keep LLM compaction behind an evidence gate: build it only if collapsed windows still lose things you notice. |
| R2 | **Unload skills after ~3h of silence instead of at compaction** (T1/C8) | Simplicity | Prod shows 0% cache hits after a 60+ minute gap, so unloading then costs no cache. It could ship in week one. | **Strong.** It's backed by measurement, independent of compaction, and an early win. |
| R3 | **Gate the consolidator on evidence** (decision 4) | Simplicity; Ops on timing only (X5) | No measured failure comes from today's hot-path writes. An automated writer for injected memory with no evals is a risk. | **Partly.** Ship S5 in shadow mode first (X5): it proposes and you approve. Switch to auto-apply after a few weeks of good proposals. That keeps your decision and removes the risk. |
| R4 | **`daily-log` once a day now**, via a HEARTBEAT.md edit | Simplicity | About 5 fewer ticks a day at roughly 85k input each, with no code change. | **Do it now.** But check X6 first: the user scope reads today's log during the day. |
| R5 | **Slim S1**: no backfill, no reader migration, simpler provenance | Simplicity | Search was called once in 15 days. | **Partly.** Keep provenance, since the consolidator needs it (X11). The backfill is cheap and makes history searchable from day one. Split the reader migration into S1c (Ops). |
| R6 | **Drop embeddings and weekly replay** | Simplicity | No FTS miss has been observed. | **Agree, as evidence gates.** They're already last (S6). Mark them "only if observed". |

## 3. Notable single-reviewer findings

- **`/clear` content comes back**: compaction rebuilds from rows since the cursor, so a cleared conversation reappears. `/clear` also runs outside the owner lock. (Codebase M4)
- **The backstop trim evicts the summary first**, exactly when compaction was skipped. (Design M1)
- **"Remember this" is guarded only by prose**: nothing in code ties a hot-path write to an owner request, so it's an injection path into USER.md. (Design M2)
- **The expiry rule matches every entry**, because of the "(observed YYYY-MM-DD)" suffix. (Design M5)
- **The quote check is a substring test**, so "yes" passes, and `stated`/`inferred` is the model's own label. (Design M6)
- **Dropped ops are never retried**, because the global cursor moves on. (Design M4)
- **A stale `.git/index.lock`** or a crash between writing and committing breaks later nights silently. (Ops)
- **The nightly job needs its own cron**: a 60s misfire grace skips the night on a restart. (Codebase, minor)
- **Staging is too thin to exercise S4/S5.** Add CLI dry-run entry points that run against a scratch copy of a prod backup with tools stubbed. (Ops)

## 4. Minimum viable redesign (Simplicity, verbatim summary)

1. `daily-log` once a day (HEARTBEAT.md edit, no code).
2. S0-lite: bound tools per call, due set per tick, one `countTokens` reading.
3. Episode store-lite: one table, FTS5, `search_history`.
4. Turn collapse in `_add_and_trim` (after a staging check on Gemini thought signatures).
5. Reset skills after ≥3h of owner silence.
6. Stateless ticks, lite version: a throwaway thread per tick, due tasks' notes injected.
7. S2 as written.
8. A `/memory` command showing recent commits, with undo behind it.
9. The weekly audit also prunes expired dated USER.md entries.
10. Evidence gates instead of slices for the consolidator, embeddings and LLM compaction.

## 5. Suggested next step

1. Rule on R1–R6.
2. I revise the plan to fold in X1–X13 and your rulings, then commit the plan, the Devin report and
   these reviews together.
