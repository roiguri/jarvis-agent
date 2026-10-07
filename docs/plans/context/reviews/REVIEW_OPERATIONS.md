# Review — Context Redesign Plan: sequencing, verification, rollout, operational risk

**Reviewer angle:** SRE / delivery. **Plan reviewed:** `docs/plans/CONTEXT_REDESIGN_PLAN.md` (2026-10-06).
**Evidence gathered read-only on 2026-10-06** from the prod and staging trees, the venvs and `df`.

## Verdict

The plan is designed well and its dependencies are mostly correct. Its weakness is the operational
half: S2, S3 and S5 each add a writer or an LLM job to the box, and the plan gives none of them a
rollback, a dry-run or shadow mode, a disk budget, or any failure signal the owner would actually
see. Fix the two blockers (`.gitignore` and add-by-path for the memory repo; a dry-run or shadow
phase plus a tested multi-night undo for S5). Then split S1 and move S4 ahead of S3, and the
sequence becomes safe to ship one slice at a time.

---

## Findings

### BLOCKER-1 — The `jarvis_memory/` git repo will sweep in `threads.sqlite*` and refill the disk

- **Where:** S2 "`jarvis_memory/` becomes a git repo; every memory-tool write is a commit. `.git` is
  deny-listed in `_get_safe_path`".
- **Evidence:** `/app/jarvis_memory/threads.sqlite` is 97 MB and `threads.sqlite-wal` is 65 MB (they
  live in the same tree by design). Both change on every turn. The plan deny-lists `.git` for the
  memory tools, but it never says what the repo itself excludes, or whether commits add only the
  written path. A `deploy_state` tarball compresses these files to about 2.5 MB, so a git blob per
  version costs about 2–3 MB.
- **Failure scenario:** the first implementation uses `git add -A` or `commit -a` (the obvious
  choice when "every write is a commit" includes deletes and the consolidator's multi-file runs).
  Each memory write then commits a new sqlite blob. At dozens of writes a day (heartbeat notes
  alone are written most ticks), that is roughly 50–100 MB/day of `.git/objects`. On a 16 GB root
  that was at 100% recently, it fills again within weeks. Once it does, every git commit fails,
  then the JSONL logs and sqlite writes fail.
- **Fix:** S2 checklist items: (1) a committed `.gitignore` written at `git init` time:
  `threads.sqlite*`, `*.tmp`, the lock file, `*-superseded-*`. (2) Commits stage explicit paths
  only, never `-A` or `-a`. (3) A test that writes a memory file under a scratch root that also
  holds a dummy `threads.sqlite`, and asserts it is untracked. (4) `gc.auto=0`, with `git gc` run
  from the nightly job, so an auto-gc never runs inside a user turn.

### BLOCKER-2 — S5 rewrites core memory on night one with no dry-run, no shadow period, and an undo that only covers one run

- **Where:** S5 "First run: … one bounded seeding pass over the backfilled history (owner-reviewed
  note)"; "`/memory undo` reverts the last consolidation commit"; §5 "auto-applied".
- **Evidence:** the backfill covers 90 days. The prod `chat_history.jsonl` holds about 1,200 owner
  messages (1.4 MB). "Owner-reviewed note" does not say whether the owner reviews before or after
  the ops are applied. The memory repo also takes hot-path commits (every `write_memory`,
  including hourly heartbeat notes), and those interleave with consolidation commits. Code rollback
  (`deploy/rollback.sh`) reverts code only. DEPLOY.md says so explicitly.
- **Failure scenario:** the seeding pass, or a prompt or validator bug that slips past staging
  (staging has no realistic data; see MAJOR-8), applies a large rewrite of USER.md and topic files.
  The owner notices on night 3. `/memory undo` reverts only night 3. Reverting nights 1–2 by hand
  conflicts with the hot-path commits made in between. Rolling back the code does not touch the
  files. The core memory injected into every call stays degraded until someone does manual git
  surgery on prod.
- **Fix** *(challenges settled decision #4, on timing only; auto-apply stays the end state)*:
  1. Ship the consolidator first with `apply=false`. For 5–7 nights it computes ops, validates
     them, and sends the morning note as a proposed diff, with no commit and no cursor advance.
     Switch to apply only after the owner has read those notes.
  2. Run the seeding pass as a dry-run that writes a diff. The owner approves it, and then it is
     applied as its own tagged commit.
  3. Tag each consolidation commit (`consolidation-YYYY-MM-DD`). Make `/memory undo [n|date]`
     revert a specific tagged commit, not HEAD, and report any conflict instead of forcing it.
  4. Add a `deploy/backup_state.sh pre-s5` step to the S5 deploy steps.
  5. Write the "how to revert N nights" procedure into DEPLOY.md before S5 ships.

### MAJOR-1 — The prod `daily-log` task breaks the day S1 removes `get_notification_history`

- **Where:** S1 "`search_history` … replaces `get_chat_history` and `get_notification_history`".
  The deploy steps name only the S5 and S6 HEARTBEAT.md edits.
- **Evidence:** the prod `HEARTBEAT.md` `daily-log` task tells the model to call
  `get_notification_history(limit=30)`. That task keeps running until S5 removes it.
  `docs/architecture/HEARTBEAT.md:241`, `MEMORY.md:102` and `RUNTIME.md:41,77` also name both tools.
- **Failure scenario:** from the S1 deploy until S5, every 3-hourly daily-log tick asks for a tool
  that is no longer bound. The tool layer returns error strings, so telemetry shows `status: ok`
  (TESTING_AND_EVALS_PLAN §1). Daily logs silently lose the proactive-sends section for weeks, and
  daily logs are the input that S3 and S5 readings are compared against.
- **Fix:** add an S1 deploy step that edits the prod `daily-log` task text to `search_history` (or
  keep the old tool names as aliases for one release). Grep `prompts/`, `tools/*/SKILL.md`, prod
  and staging `HEARTBEAT.md`, and `heartbeat/*.md` as part of the S1 checklist.

### MAJOR-2 — S1 bundles four changes, and two of them are hard to reverse

- **Where:** S1 checklist: the store, backfill, tool swap, reader migration, and "then the file is
  retired".
- **Evidence:** `chat_history.jsonl` has three readers today: `agent._load_recent_user_chat` (the
  heartbeat's already-handled detection), `scripts/trace.py`, and `get_chat_history`. A
  `rollback.sh` to a pre-S1 tag restores code that reads `chat_history.jsonl`.
- **Failure scenarios:**
  - **(a) Rollback after retirement.** The file has a gap from the retirement date. The rolled-back
    heartbeat sees no "today's chat", so it re-sends briefings the owner already handled. This is
    the failure the chat slice exists to prevent.
  - **(b) Backfill twice.** Backfill runs on deploy, then rollback, then redeploy. Unless the
    backfill is idempotent, every history row is doubled. Search results double, and S5's seeding
    reads duplicates as "frequency".
  - **(c) Tool swap without an eval.** Replacing two tools is a docstring and behaviour change (F1).
    No eval harness exists, and the plan's own §7 rule says a docstring change ships with a
    staging behaviour check. S1 does not list that check.
- **Fix:** split S1 into three:
  - **S1a:** store, write path and integrity counter. Dark: no prompt or tool change, dual-write
    with `chat_history.jsonl`.
  - **S1b:** the `search_history` tool and the reader migration, with a staging behaviour check.
  - **S1c:** retire `chat_history.jsonl`, in its own deploy at least one stable release later,
    marked `[format-change]` so `rollback.sh` prints the restore instruction.

  Make the backfill an explicit, idempotent script (unique key on source file plus ts plus a
  content hash; provenance `backfill`), not startup code.

### MAJOR-3 — Writing at the end of the turn loses the owner's message on a kill, which today's log does not

- **Where:** §3 "At the end of every turn, `ask_jarvis` appends the messages the turn added, read
  from the final state … Abnormal terminations write what landed."
- **Evidence:** today `main.py:56` appends the owner's message to `chat_history.jsonl` before the
  turn runs. Every deploy ends in an owner-run restart, and DEPLOY.md says "a restart drops
  in-flight turns". SIGTERM shutdown is still open (#33). End-of-turn code does not run on SIGKILL,
  on `TimeoutStopSec=30` expiry, or on OOM.
- **Failure scenario:** the owner sends a message, then restarts the service mid-turn (or it
  crashes). Once `chat_history.jsonl` is retired, the message exists nowhere. S3 re-derives its
  summary from the store, so the request also drops out of compaction. "Every message of every
  turn, recorded by code" is then false in exactly the cases where it matters.
- **Fix:** write the inbound owner row at turn start (where `append_chat_log` is called today), and
  the rest from the final state. Have the integrity check compare `turn_id`s in `turns.jsonl`
  against `turn_id`s in the store, not only row counts per day.

### MAJOR-4 — Disk: the "forever" store is multiplied by backup tarballs that are never pruned

- **Where:** §3 "Retention. Forever. Volume is small (chat history was 1.4 MB over three months)";
  Decision 9 ("full tool results; all ticks").
- **Evidence:**
  - The 1.4 MB figure is owner and final text only. Prod `tool_calls.jsonl` shows about 137 tool
    calls a day (12,612 over 92 days).
  - Decoded checkpoints give an average tool result of about 0.5–1.3 KB. The largest is 37.7 KB
    (owner thread: 40 tool messages, 51 KB).
  - Content plus an FTS5 index comes to roughly 50–150 MB a year, compared with 1.4 MB.
  - `deploy/backup_state.sh` tars all of `jarvis_data` on every deploy, and `--prune` keeps every
    deploy-tagged tarball forever. `/app/backups` already holds 34 tarballs, with 6 deploys on
    2026-10-03..05 alone.
  - The root disk is 16 GB (47% used today). It has been at 100% before.
- **Failure scenario:** after 6–12 months, each deploy tarball carries a 15–40 MB compressed
  episode store, and the tarballs are never pruned. `/app/backups` grows by hundreds of MB to GBs,
  and staging grows its own copy. The disk fills during a `deploy.sh` snapshot, or worse, at
  runtime. The design never fails a turn on a store write error, so the store then silently stops
  recording.
- **Fix:** none of this changes the "forever" decision.
  - Change `--prune` to keep only the last N deploy-tagged tarballs (for example 10), plus any
    tarball for a `[format-change]` deploy.
  - Give the episode store its own rotating backup (sqlite `.backup`, weekly, keep 4) instead of
    copying it into every deploy tarball.
  - Use an external-content FTS5 table so text is not stored twice.
  - Set `journal_size_limit` and `wal_autocheckpoint` on `episodes.sqlite`. Today
    `threads.sqlite` is 97 MB plus a 65 MB WAL for about 5 one-checkpoint threads (#12/#24), and
    the new store should not repeat that.
  - Add a row to §9 for store size in MB and free disk.

### MAJOR-5 — The owner never sees a failure

- **Where:** S1 "Store-integrity check in telemetry"; S3 "`compaction` telemetry event"; S5 "the
  note says so"; §9.
- **Evidence:** every failure signal in the plan lands in `turns.jsonl`, `tool_calls.jsonl` or the
  journal, and nobody reads those daily. The morning note is queued by the nightly job. If that
  job crashes, the scheduler misfires (service down at 03:00, for example during a late deploy),
  or git is locked, then no note is queued, and a missing note looks the same as a quiet morning.
- **Failure scenario:** a stale `.git/index.lock` (MAJOR-6) or ENOSPC stops every memory commit, or
  the episode writer fails on every turn, or compaction runs on its deterministic fallback for a
  week. All of this is logged, and none of it is seen.
- **Fix:**
  1. A dead-man's switch: queue the morning-note trigger before the nightly run, with the body
     "consolidator did not finish (last success: …)". The successful run replaces it.
  2. A fixed health footer in the morning note, composed by code:
     - episode rows written vs. turns in `turns.jsonl` yesterday;
     - compactions ok / fallback / skipped;
     - `jarvis_memory` git state (clean, dirty, locked) and the last commit age;
     - free disk.
  3. Optionally a `/health` slash command over the same data (zero context cost, per the
     "no always-on tools for rare actions" rule).
  4. A defined misfire policy for the nightly job (run at startup if the last run is over 24 h
     old).

### MAJOR-6 — Crash and partial-apply semantics for files plus git are not specified

- **Where:** S2 "A file lock around memory writes"; S5 "validates and applies them in one git commit;
  cursor advances after the commit … checks that no file it read changed since".
- **Evidence:** owner turns (`main._owner_turn_lock`) and heartbeat turns (`heartbeat.TURN_LOCK`)
  are separate locks and run concurrently, and both write memory. S5 reads "today's hot-path writes
  as a git diff".
- **Failure scenarios:**
  - **(a) Killed between apply and commit.** The consolidator is killed after writing files but
    before committing (restart, `TimeoutStopSec`). The next night, the uncommitted changes show up
    in the working-tree diff and are read as owner "remember this" writes, so the consolidator
    treats its own half-applied ops as evidence.
  - **(b) Stale `index.lock`.** A kill or ENOSPC mid-commit leaves `.git/index.lock`. Every later
    commit fails. If commit failure is non-fatal for `write_memory` (it must be), this is silent.
  - **(c) Lock-file lock.** If the S2 lock is a lock file (O_EXCL), not `flock`, a crash leaves it
    held for good.
- **Fix:** specify the following.
  - `fcntl.flock` on an fd, so the kernel releases it on death.
  - Each consolidator file written via temp plus `os.replace`.
  - A startup reconciliation: a dirty working tree is committed as `recovered: uncommitted
    changes at startup`, and a stale `index.lock` older than N minutes is removed and logged.
  - The hot-path diff computed from commit authorship (hot-path commits carry their own author or
    trailer), not from the working tree.
  - All three states surfaced in the MAJOR-5 health footer.

### MAJOR-7 — Order: S4 delivers more, sooner, with less risk than S3, and T4 should come before both

- **Where:** Slices preamble ("Order follows dependency …").
- **Evidence:**
  - Prod `turns.jsonl` for 2026-09-30..10-06 shows heartbeat input of 0.7–1.1 M tokens a day.
    That equals or exceeds user-scope input (0.13–1.65 M a day, from 2–17 owner turns a day).
  - S4 needs only per-task state in `triggers.json` and, for "why did it nudge me", S1a. It does
    not depend on S3.
  - Its rollback is benign. Old code starts an empty `heartbeat` thread, and `triggers/store._write`
    dumps the whole dict, so new per-task keys survive a rollback.
  - S3 is the riskiest behaviour change in the plan (summary placement, skill decay, latency), and
    it can only be verified by reading transcripts.
  - TESTING_AND_EVALS_PLAN T4 (about 10 heartbeat cases, default-deny tool recorder) is "next" and
    needs only T1. It is exactly the instrument S4's "same ticks act on the same days" check lacks.
- **Fix:** proposed order: **S0 → S1a → S2 → T4 → S4 → S1b/c → S3 → S5 → S6.**
  - S2 (git) goes early: it is cheap, and it gives every later slice a data rollback.
  - S4 is verified with T4's heartbeat cases plus a prod shadow week (see MAJOR-8).
  - S3 lands only after S1 has been integrity-clean for a week, because compaction re-derives from
    the store. Add a guard: if store rows for the span are missing, fall back to a plain trim plus
    marker instead of summarising a gap.

### MAJOR-8 — Staging cannot exercise S3, S4 or S5 as written

- **Where:** S3 "Verify (staging)…"; S4 "Verify: … compare a week of acks"; §10 "S3–S5 are verified
  on staging by reading transcripts".
- **Evidence:**
  - Staging is thin: 1,058 chat lines, 31 notifications, 493 turns.
  - Staging's `HEARTBEAT.md` has drifted from prod: four tasks are `paused`, `running-*` tasks are
    absent in prod, and prod-only tasks are `reading-list-suggestion` and `step-challenge-tracker`.
    Staging `USER.md` and `MEMORY.md` also differ from prod.
  - The heartbeat is off unless the owner adds a systemd drop-in and restarts.
  - `crossfit-sync-and-remind` is gated on real Arbox (a staging booking is real).
  - Claude cannot restart staging. Every iteration costs an owner round trip.
- **Failure scenario:** S4 and S5 pass staging on a task set and memory that prod does not have, and
  the first real reading is in prod. S4's "compare a week of acks" compares against a different
  week: tasks change (the step challenge started 2026-10-06) and so does what the owner said.
  There is no counterfactual.
- **Fix:**
  1. Build compaction, the stateless tick and the consolidator as pure functions with a CLI entry
     point (`scripts/` or `evals/`) and a `--root <scratch>` option. Run each against a scratch
     extraction of a prod `backups/state-*.tar.gz`, never extracted into the staging root, where
     `triggers.json` would fire. Use T4's default-deny tool recorder, so Arbox, Outbox and memory
     writes are stubbed.
     - Claude can run these with no service restart, given an exported `GOOGLE_API_KEY` (testing
       plan D8: never read `.env`). This lets the plan's S3 and S5 "Verify" items run on real data.
  2. Shadow mode in prod for S4. For one week, run the fresh-context tick next to the live tick
     with the recorder tools and no delivery. Diff `acted_tasks` and `notify` per tick, and record
     the agreement rate as the S4 verification number.
  3. Shadow mode in prod for S5, as in BLOCKER-2.

### MAJOR-9 — Compaction under the owner-turn lock can make the owner wait

- **Where:** §4 "a post-turn job under the owner-turn lock, after the reply is delivered. It applies
  only if the checkpoint is unchanged since it read it."
- **Evidence:** "under the lock" and "applies only if unchanged" contradict each other unless the
  lock is released during the model call. If the lock is held for the whole call, then on a travel
  day (re-derivation reads every row since 03:00, including tool results of up to about 38 KB
  each) a Flash call of 10–30 s blocks the owner's next message, which `run_owner_turn` queues
  behind it. §9 has no latency row.
- **Fix:**
  - Read the checkpoint under the lock, release it, call the model with a hard timeout, then
    re-acquire the lock and compare-and-swap.
  - Add owner-turn `duration_ms` p50/p95 (already in `turns.jsonl`) and compaction tokens per day
    to §9.
  - Cap the re-derivation input (C3's merge-mode fallback) with a token number from S0.

### MAJOR-10 — The readings cannot detect the changes they are meant to judge

- **Where:** §9 table and "over full Israel days with no paused tasks".
- **Evidence:**
  - The last 7 prod days had 2–17 owner turns a day, 0 heartbeat ticks on 09-28 and 09-29, and a
    new 4-hourly task starting 10-06. `health_status` replaced `check_biometrics` on 10-05.
  - The table has no S2 column, although injecting MEMORY.md adds input to every call. It has no
    S6 column.
  - The table has no rows for the costs the plan adds: compaction calls and tokens per day,
    nightly tokens, latency, fallback counts, store MB.
- **Fix:**
  - Use at least 7 full days per window, with medians per call, not means.
  - Stamp each window with a hash of the active `HEARTBEAT.md` task set (S0 already records the
    due set per tick) and the deploy tag.
  - Add the S2 and S6 columns and the rows listed above.
  - State a pass threshold per slice. For example, S4: heartbeat input per day −50% at ≥ 95% ack
    agreement in shadow.

### Minor

1. **Git author identity.** The service runs as `jarvis_user`, whose global git config is the
   owner's personal name and email. Code commits would look like the owner's hand edits. Pass
   `-c user.name=jarvis-hotpath` (or `jarvis-consolidator`) explicitly, and assert there is no
   remote.
2. **Git keeps "forgotten" content forever.** Removing something the owner asks to forget would
   take a history rewrite. Document the procedure (`git filter-repo` plus the episode-row delete)
   next to `/memory undo`.
3. **FTS5 and sqlite-vec availability are confirmed.**
   - Both prod and staging venvs have SQLite 3.40.1 with FTS5, and `enable_load_extension` works.
   - `sqlite-vec==0.1.7` is already in `requirements.txt` and installed in both venvs, so S6 needs
     no new dependency.
   - About 8% of owner messages (102 of 1,214) contain Hebrew, where `unicode61` has no stemming.
     Consider adding the `trigram` tokenizer as a second index.
4. **Episode rows from the Outbox.** The gateway may not import the app (channel-agnostic guard).
   Write Outbox episode rows through the existing injected `log_sink`, not with a direct import.
   The guard would catch it, but it is better planned than discovered in CI.
5. **Today's daily log disappears from the user scope.** User scope currently injects today's daily
   log. After S5, no daily log exists for today until night, so the user scope's cross-scope view
   rests entirely on S3's summary. S5 should state the `build_system_prompt` change. Rolling back
   S5 code also needs the `daily-log` task re-added to prod `HEARTBEAT.md` by hand; list that in
   the rollback notes.
6. **Malformed telemetry rows.** Line 1 of prod `turns.jsonl` is malformed (retention trim cut it
   mid-line). Reading scripts must skip bad lines rather than abort.
7. **`[format-change]` markers.** Mark the S1c, S4 (`triggers.json` per-task state) and S5 commits
   `[format-change]`, so `rollback.sh` prints the restore tarball.
8. **Caps do not trip on day one.** Prod USER.md is 1,359 chars (cap 4,000) and MEMORY.md is 31
   lines (cap 200), so S2 rejects nothing at first. Its "over-cap write is refused" check needs a
   synthetic test, not a prod reading.

---

## Things the plan gets right

- S0 comes first, and three open questions are settled by measurement rather than opinion.
- A store write failure never fails a turn. The 50-message reducer stays as the backstop, so a
  skipped compaction degrades to today's behaviour.
- The cursor advances only after the commit. The consolidator drops ops for files changed since it
  read them. A deterministic fallback and a thrash guard cap the cost of compaction failures.
- The gate, `due:` windows, stamp-after-delivery and Outbox delivery are explicitly left unchanged
  in S4, and S4's code rollback is benign (verified: `triggers/store._write` preserves unknown keys).
- The morning note is composed by code, not paraphrased by the model, and undo is a zero-context
  slash command.
- Prod `HEARTBEAT.md` edits are named as deploy steps. That list is incomplete (MAJOR-1), but it
  exists.
