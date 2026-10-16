# Context Redesign — Plan

**Date:** 2026-10-07 · **Status:** S0 in prod since 2026-10-10 (#151); before-readings recorded
2026-10-16 (§9); cache question answered; S1 next.
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

**Timing (owner, 2026-10-07):** S0 waited for #135 (triggers validation) and #129 (turn-budget
readings), because S0 replaces the `/usage` instrument #129 read. Both closed on 2026-10-09/10.

Each slice ships and is verified on its own, with a before/after reading (§9). Design
detail is in §3–§8. Order: cheap wins first, then the store everything else depends on, then the
largest remaining cost (the heartbeat), then the two pieces that change behaviour most
(compaction, consolidation). Nothing discards or rewrites context until every turn is durably
recorded (S2).

**S0 — Instrument.** Telemetry moves from JSONL to its own SQLite store and records every LLM
call (§2a). Measurement only: no behaviour change. Goes to `main` early. **Shipped:** #151, in prod
since 2026-10-10 (`deploy-2026-10-10-3`).
- [x] `jarvis_data/observability/telemetry.sqlite` with the tables in §2a; one writer module
      (`observability/telemetry.py`), never raising into a turn.
- [x] `_llm_node` records each call: tokens, latency, finish reason, composition in chars, the
      prompt's hash; the bound tool set per turn. Tool calls link to the LLM call that issued them.
- [x] Heartbeat turns record their due tasks; wakes their trigger.
- [x] `telemetry.job(name)` records LLM calls made outside a turn (used from S5/S6).
- [x] One-time import of the last 180 days of `turns.jsonl` and `tool_calls.jsonl` (idempotent);
      ran on both instances, then removed (#150).
- [x] Readers moved to the store: `/usage` (`observability/usage.py`), `scripts/trace.py`,
      `scripts/context_report.py` (rewritten as queries; adds per-call composition and cost per
      due-task set), `scripts/ci/check_command_replies.py`, tests that read `turns.jsonl`.
- [x] Writing `turns.jsonl` and `tool_calls.jsonl` stops; the files stay on disk until they age
      out. `chat_history.jsonl` and `notifications.jsonl` are content, not telemetry: unchanged
      until S2.
- [x] Retention: 180 days for every table, applied at startup with the existing log trim.
- [x] Backups: the store is copied with SQLite's online backup, not tarred live.
- [x] `scripts/measure_context.py`, run by the owner on staging (it needs the API key): real token
      cost of core tools and of each skill, and the cache test (same request ×3, then one skill
      added ×3; run twice). Ran 2026-10-10; results in §9 "Staging measurement".
- [x] Docs: OBSERVABILITY.md (store, tables, readers), CLAUDE.md data tree.
- [x] Tests: each table written with the right links; composition split at the turn start; a job
      row; telemetry failure doesn't fail a turn; import is idempotent; readers return the same
      numbers as the old JSONL path on imported data.
- [x] Verify (staging, then prod): `/usage` matches the pre-change numbers for the imported window;
      a staging turn and a prod turn produce complete rows.
- [x] Before-reading recorded in §9 (2026-10-11..15).
- [x] The cache question: answered by `measure_context.py --cache` (§9, 2026-10-16).

**S1 — Quick wins.** No new subsystems.
- [x] Skills reset after ~3h of owner silence, at the next turn's start (R2). Prod shows 0% cache
      hits after a 60-minute gap, so the reset costs no cache. RUNTIME.md's deferral note and the
      AGENTS.md "deactivate when no longer needed" line updated.
- [x] Verify: skill-schema tokens per user call drop after idle gaps; a needed skill is re-activated
      within one round-trip. Staging 2026-10-16: no reset within 3h; after a 4h50m gap the turn
      started with no skills ("Skills reset after idle on owner: ['google_health']") and switched
      `google_health` back on in the same turn.

Dropped from S1 (owner, 2026-10-10): scoping the fitness writer tools to user turns. Ticks do call
them (`manage_fitness_plan` as recently as 10-07), and it would restrict the heartbeat's tools,
which decision 1 rules out. The R4 `daily-log` change moved to S5 (see there).

**S2 — Episode store.** Every message of every turn, recorded by code (§3). One PR (owner,
2026-10-16): the store, the today view and the search tool together; retiring `chat_history.jsonl`
waits for the final merge.
- [x] `jarvis_data/episodes/episodes.sqlite` (package `episodes/`). Turn input written at turn start
      (owner's raw message, attachments as paths; tick/wake instructions and confirmation results as
      `system`); the turn's model steps (reply text, tool calls with arguments, tool results) at turn
      end from the final state, including failed turns. Delivered sends via the Outbox log sink;
      slash commands with their replies. The forwarded-notification block is not written (each send
      already has its row).
- [x] Content: narrow credential scrub (API keys, GitHub tokens, Bearer, JWT, password/key/token
      fields); a row over 200k chars is cut; kept forever (§10 retention: forever for now, revisit
      only if size grows — ~80 KB of tool results a day in 2026-10).
- [x] One-time import of `chat_history.jsonl` and `notifications.jsonl` (`TODO(#157)`).
- [x] `search_history` (FTS5; keywords or newest-in-range; `kind` chat/notifications/background/all;
      grouped by turn) replaces `get_chat_history` and `get_notification_history` outright, no
      aliases: S2 reaches prod only with the whole redesign.
- [x] Today view in the user-scope prompt: today's heartbeat ticks that acted or messaged the owner,
      and wakes, one line each from `heartbeat_respond` (latest 20). Heartbeat activity only; the
      daily log keeps covering the conversation recap until S5.
- [x] Integrity check in `scripts/context_report.py` (telemetry turns missing from the store, per
      day); the store is backed up with SQLite's online backup.
- [x] Docs (MEMORY.md "Episode store", RUNTIME.md, CLAUDE.md), goldens, tests.
- [ ] Verify on staging (steps in the PR): rows for a tool turn; today view after an acting tick;
      `search_history` finds an earlier day; a full day passes the integrity check.

**Final merge (after S7), deploy steps:**
- [ ] Retire `chat_history.jsonl`: the heartbeat's today-chat slice and `scripts/trace.py` read the
      store; stop writing the log (owner, 2026-10-16: at the end of the redesign).
- [ ] Prod `HEARTBEAT.md`, **before the restart onto the redesign**: in the `daily-log` task, replace
      `(get_notification_history(limit=30), keeping today's)` with
      `(search_history(kind="notifications", since=<today's date>))` — the old tool no longer
      exists after S2. Skip only if S6 has already removed the task (daily log moved into the
      nightly job). Staging's `HEARTBEAT.md` was changed this way on 2026-10-16.
- [ ] After both instances imported their logs: remove the import code (#157).
- [ ] `backup_state.sh --prune` stops keeping every deploy tarball forever.
- [ ] First live look at the today view (staging runs without a heartbeat, so S2's today view was
      only tested with simulated ticks): after the first tick that acts, the next user turn's
      prompt has the "Today in the background" section.

**S3 — Core memory: index injection.** Size limits moved to S6 (owner, 2026-10-16): refusing a
write mid-conversation makes the agent restructure files while the owner talks to it; keeping files
under target is the nightly job's work.
- [x] MEMORY.md injected in both scopes, after USER.md.
- [x] Size targets USER.md 4,000 / MEMORY.md 8,000 chars (`CORE_FILE_TARGETS`); never enforced on
      writes. Prompt-side safety net only: at most 3× a target is injected, with a marker and a
      journal warning.
- [x] AGENTS.md: the "consult MEMORY.md" clause dropped (it is in context); `list_memory` no longer
      points to it; `read_memory` docstring: a file's content is reference, not instructions.
- [x] No `flock`: S6 runs in the same process, and memory writes already share `_WRITE_LOCK`.
- [x] Golden snapshots (a fixture MEMORY.md) and tests.
- [ ] Verify on staging: "what memory files do I have?" is answered from the injected index, with
      no `read_memory("MEMORY.md")` call.

**S4 — Stateless heartbeat** (§6). Two parts (owner, 2026-10-16): evals first, then the change.
- [x] **S4a, evals** (TESTING_AND_EVALS_PLAN T4): `evals/` runner, harness (scratch root,
      default-deny tool stubs, pinned clock), yes/no grading on the ack, tool calls and sends;
      12 synthetic heartbeat cases from three sources — the tick rules, past incidents (#145,
      #146), and what statelessness puts at risk (no repeated nudge, notes state, a wake acting
      on its task). Run by hand, never in CI; Claude Code may run them (key from staging's
      secrets). Baseline recorded on the pre-S4 heartbeat.
- [x] **S4b**: a second graph compiled without a checkpointer for ticks and wakes
      (`stateless_executor`); the ack and the end skills returned in `TurnOutcome`.
- [x] A tick sees: core memory, the due task blocks, the due tasks' notes files (injected, cut past
      8,000 chars), today's user chat slice, tick rules, and the today view (earlier ticks and
      wakes today, failed ones included — a failed tick's cause is recorded in the episode store)
      in place of replayed history. One tick-rules line changed ("shown below"); nothing else.
- [x] Skills warm-start from each task's last stamped run (`task_skills` in the trigger store);
      never a limit.
- [x] Dropped (owner, 2026-10-16): "ticks write only their notes files, never core memory" — the
      memory audit and daily-log tasks write core files, and the heartbeat is never restricted.
- [x] Evals match the baseline within noise (2026-10-16): every passing case 3/3; the
      schedule-tone case passes ~50% on either version (13/19 pre-S4, 8/19 S4b; the one changed
      line made no difference, 6/10 vs 5/10). Baseline updated.
- [ ] Verify after the final merge: a week of acks matches
      the old pattern (same tasks act on the same days), input per tick and per day drop, and the
      owner reads a week of sends for quality.

**S5 — Compaction** (§4, R1). Design settled with the owner on 2026-10-16. Two parts: conversation
evals first (S5a), then compaction (S5b).
- [x] **S5a, evals:** user-scope support in `evals/` (a seeded owner conversation in the checkpoint
      and the episode store, filler turns pushing the morning out of the window; an optional
      compaction step before the follow-up message; checks on the reply). 6 cases in
      `evals/cases/chat/`. Baseline on pre-S5 code (plain trim): morning fact 3/3 and open request
      3/3 (both found by `search_history`), finished task not revived 3/3, tools used 3/3; a chat
      rule followed 0/3 and the morning's request remembered 0/3 — what compaction targets.
- [x] **Trigger:** a post-turn job compacts when the earlier conversation in the window passes about
      20k tokens; the last 3 turns stay verbatim. The 50-message cap stays as a backstop only and
      keeps the summary (pinned). The job runs outside the owner lock and applies under it only if
      the checkpoint is unchanged.
- [x] **Summary:** one user-role message at the head of the window, framed as reference ("everything
      was handled except 'Open requests'; tools remain available; full messages are searchable").
      Code fills: header (span covered, when compacted), the owner's own messages verbatim (newest
      first, size budget), identifiers (URLs, codes, amounts), live reminders and wakes from the
      trigger store, skills used today. The model fills JSON (structured output): open requests
      (owner's exact quote, when asked), done (dated facts), owner rules (quotes), learned,
      decisions, lookup hints. Built fresh each time from the episode store over the last ~36h
      (later: since the last nightly run) — not stacked on the previous summary. Carried open
      requests are re-checked against the store and expire after ~3 days.
- [x] **Timestamps:** every transcript line the summarizer reads is tagged with its Israel date and
      time; the header states the covered span; items carry absolute dates; an item with a relative
      date word ("today", "tomorrow", "yesterday", "last week") is dropped by code. Israel time
      throughout, plus the owner's local time in `/tz` away mode, as the turn stamp does.
- [x] **Validation:** every quote must be a substring of an owner row in the span; dates parse and
      are not in the future; secret scrub; the summary is smaller than what it replaces. A failing
      item is dropped; a failing record gets one corrective retry, then a deterministic code-only
      record. A thrash guard after a fallback.
- [x] In the kept turns, calls stay and older results become stubs pointing at the store
      (`[result cleared — 14 KB, searchable]`); the newest results stay in full. Only at a
      compaction, never per turn (cache).
- [x] Skills with no call in the kept turns are switched off at the compaction.
- [x] Model: Flash (~2–4 cents per compaction); upgrade only if the evals show bad summaries. Its own
      settings: 180s limit (36h of transcript outlasts a turn's 60s), temperature 0, and an 8k
      output-token cap, so a degenerate repeating answer (seen on a corrective retry) ends in
      seconds and falls back instead of running to the limit.
- [x] Silent to the owner; a telemetry job row per compaction (`telemetry.job("compaction")`: sizes
      before/after, outcome ok/retried/fallback/skipped_stale, latency). Slash commands: `/context`
      shows the current summary; `/compact` runs one by hand (also the live test on staging).
- [x] `/clear` respected: rows before a clear are never re-derived; a compaction prepared before a
      `/clear` finds the thread changed and is skipped (`skipped_stale`), so `/clear` needs no lock.
- [x] No nightly compaction (owner, 2026-10-16): the summary's items are dated, so it stays correct
      across days; revisit with S6 if mornings feel cluttered.
- [ ] **Deploy step:** prod `HEARTBEAT.md` `daily-log` → once a night (R4). Until this slice the 3-hourly
      log is the chat side's only record of conversation that fell out of the window (closed by
      this slice's summary). Saves ~5 ticks/day.
- [x] Golden test on the summary framing; tests for each part (`tests/test_compaction.py`).
- [x] Verify, evals (2026-10-16, real summaries): all 6 chat cases 3/3 — a chat rule followed 0→3
      and the morning's request remembered 0→3; the morning fact and the open request now come from
      the summary with no `search_history` call (before: found by searching). Baseline updated.
- [ ] Verify, staging: `/compact` then a question about something only in the summary, and
      `/context` shows it.

**S6 — Nightly consolidator** (§5, R3).
- [ ] Its own cron entry (~03:00 Israel) with a misfire grace that survives a restart.
- [ ] Size management: when USER.md or MEMORY.md is over its target (`CORE_FILE_TARGETS`), propose
      moving detail into topic files with a pointer left behind (shadow mode, then auto-apply like
      every op). Both files' sizes in the morning note's health footer. Moved from S3.
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
| **L3 Core memory** | SOUL.md, USER.md, MEMORY.md index | Owner; "remember this"; the nightly job | Injected every call (size targets kept by the nightly job) |
| **L3 Topic files** | Detail, read on demand | Agent on request; the nightly job | Memory tools |
| **L4 Consolidation** | Ops against L3; the daily log | One nightly job | Never directly |
| **L5 Background work** | Ticks and wakes | Their own notes files only | Fresh context per run |

Kept as is: per-call prompt rebuild (hot reload), the owner thread spine, the user-role pending
mirror (checked in prod 2026-10-06: no sign of the hermes #118863 failure), the gate and its stamps,
the Outbox, the SOUL.md confirmation flow.

## 2a. Telemetry store (S0)

Telemetry records what happened and what it cost. Conversation content (messages, tool arguments and
results) belongs to the episode store (§3), never here. The one exception is the system prompt,
stored once per distinct text, because "what exactly did the model see" is the first question when
debugging. Retention for every table: 180 days (owner, 2026-10-07).

**Location.** `jarvis_data/observability/telemetry.sqlite`, WAL mode. A separate file from the
episode store (owner: production content and telemetry don't share a database).

| Table | One row per | Columns (beyond `id`, `ts`) |
|---|---|---|
| `turns` | Turn or job | `turn_id`, `thread_id`, `scope` (`user | heartbeat | job`), `job`, `channel`, `trigger_id`, `due_tasks` (JSON list), started/ended/duration, `llm_calls`, `tool_calls`, token totals (input, cache-read, output, reasoning, total), `model`, `active_skills_start/end`, `no_action`, `outcome`, `error`, `budget` (JSON) |
| `llm_calls` | LLM call | `turn_id`, `call_index`, `started_at`, `latency_ms`, input / cache-read / output / reasoning tokens, `finish_reason`, `model`, `prompt_hash`, `prompt_chars`, `schema_chars`, `bound_tool_count`, `history_chars` (messages from earlier turns), `turn_chars` (this turn's messages so far), `history_messages`, `turn_messages` |
| `tool_calls` | Tool call | Today's `tool_calls.jsonl` fields (`tool`, `namespace`, `destructive`, `duration_ms`, `status`, `args_size`, `error`, `traceback`) plus `turn_id`, `llm_call_index` (the call that issued it), `result_size` |
| `bound_tools` | Turn × tool | `turn_id`, `tool`, `namespace`, `schema_chars`, `first_call_index` |
| `prompts` | Distinct system prompt | `prompt_hash` (SHA-256), `scope`, `chars`, `text`, `first_seen`, `last_seen` |

**Composition.** Measured in chars, split at the current turn's start (the same boundary the window
trim uses): system prompt, serialized tool declarations, history from earlier turns, this turn's
messages. Chars are shares; tokens are cost. Together they split a call's input into prompt, schemas,
replayed history (A4) and the growing tool-result tail (A5).

**Writes.** One short transaction per event; a failed write is logged and dropped, never raised into
a turn. Turn rows are inserted at start and updated at end, so a crash leaves a visible open row.

**Size.** Roughly 1–3 MB a month at current volume (prompts deduplicate; most rows are numbers).

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
| 3 | MEMORY.md | Injected (S3); size target 8,000 chars kept by the nightly job |
| 4 | Who writes core memory | Hybrid: "remember this" immediately, everything else nightly; shadow mode, then auto-apply with snapshots, morning note, undo (R3) |
| 5 | Old context | Compact: summary + last ~3 turns, old tool results stubbed, budget in turns and tokens (R1) |
| 6 | Consolidator model | Flash, nightly; upgrade on evidence |
| 7 | Search and history | FTS5 first, embeddings last; git for memory postponed (2026-10-07) |
| 8 | Size limits | Targets USER.md 4,000 / MEMORY.md 8,000 chars, SOUL.md none; writes never refused; the nightly job moves detail down; prompt cut at 3× a target as a safety net (owner, 2026-10-16) |
| 9 | Episode store | Replaces `chat_history.jsonl` in three steps (R5); forever; full tool results; all ticks |
| 10 | Daily logs | Kept; every 3h until S5, then once a night (R4), by the nightly job from S6 |
| 11 | Consolidator visibility | Note every morning, "promoted 0 because …" when nothing changed |
| R2 | Skill unloading | After ~3h of owner silence, plus at compaction |
| R6 | Weekly replay | Only if nightly runs miss cross-day patterns |
| C1–C8 | Compaction | Recommendations in COMPACTION_AND_DREAMING.md, as amended in §4 |
| D1–D9 | Consolidation | Recommendations in COMPACTION_AND_DREAMING.md, as amended in §5 |
| T1–T6 | Tools | Decay; warm start; measure first; one home per rule; schema budget; consolidate outliers |

**Settled by measurement, not by asking:** the compaction thresholds, Flash vs Pro for the
consolidator, and whether tool-set changes cost enough cache to matter (S0).

## 9. Readings

Before/after per slice from prod (the telemetry store from S0), over full Israel days with no paused tasks in the window (E10).
User traffic is light (2–25 turns a day), so windows are at least 5 days and confounders (new tasks,
tool changes) are noted beside each reading.

| | Before | S1 | S2 | S3 | S4 | S5 | S6 |
|---|---|---|---|---|---|---|---|
| user input / call | 47.5k | | | | | | |
| user input / turn | 128k | | | | | | |
| heartbeat input / tick | 105k | | | | | | |
| heartbeat input / day | 1.35M | | | | | | |
| cache-read share | 62% | | | | | | |
| tool + skill share of a user call | 38% | | | | | | |
| new jobs' LLM spend / day | — | — | — | — | — | | |
| compaction p95 latency | — | — | — | — | — | | |
| episode store size / day | — | — | | | | | |

**Before (prod, 2026-10-11..15, 5 full Israel days, telemetry store).** 81 turns: 17 user, 45
heartbeat ticks, 19 wakes; 322 model calls; $2.25 (heartbeat $1.73, user $0.52). User: 47.5k input
per call, 128k per turn, 2.7 calls per turn. Heartbeat: ticks 108k per tick, wakes 98k; 12.8 turns
and 1.35M input a day; 40 of 45 ticks sent nothing. Cache-read share 62% in both scopes (79% between
calls of one turn). What a call's input is made of (chars, averaged per call):

| | prompt | tool declarations | earlier turns | this turn | tools bound |
|---|---|---|---|---|---|
| user | 20.0k | 65.6k (38% of tokens) | 62.8k (27% of tokens) | 5.6k | 73 |
| heartbeat | 21.1k | 36.1k (35% of tokens) | 21.6k (20% of tokens) | 3.4k | 34 |

Confounders: light user traffic (1–8 turns a day); the owner thread had every skill active all week,
so no call changed its tool set mid-turn (the cache question below gets no answer from these days);
the step-challenge task drove most wakes.

**Staging measurement (2026-10-10, `scripts/measure_context.py`).** About 4.3 chars per token for
prose and tool declarations alike, so the store's char composition converts by ÷4.3. Tokens per call:
system prompt (staging memory, no skills) 2,490; core tools 2,571; travel 4,488; fitness 3,881; the
media family ~2,900 (radarr 1,079, sonarr 1,104, the rest small); collections 1,972; github 1,066;
google_health 924; web 613; every skill active ~17,900. A staging "what's in memory" turn carried
five unused skills, ~13.9k tokens per call, more than core and the prompt together.

**Answered: a tool-set change costs the whole cache for one call (2026-10-16).** The first cache
test was inconclusive (its cost pass pre-warmed the request; at ~5–9k tokens Gemini's ~2k-token cache
blocks capped even identical repeats at 40–45%), and prod could not settle it (no mid-turn tool-set
change in 2026-10-11..15). `measure_context.py --cache` then sent a ~22k-token request (unique
marker, ~20k tokens of filler conversation) three times to warm it (91% cached), and changed it one
way at a time, twice over:

- tools removed, prompt unchanged: 0 cached, both rounds. Gemini's cached prefix puts the tool
  declarations before the system prompt, so a tool change invalidates everything.
- tools and the skill's rules removed: 0 cached.
- a skill added: 0 and 8k cached (the unchanged declarations ahead of the new ones can survive;
  the conversation never does).

Cost: one cache break makes the next call full price, ~1.3 cents for a typical user call (47.5k
input, 62% cached). Dropping ~15k tokens of unused declarations saves ~0.3 cents a call, so a reset
pays back in ~4 calls. Consequences: the S1 idle reset is free (the cache has expired after ~3h
anyway); S5's reset at compaction is free (compaction rewrites the window); a mid-turn activation
costs one full-price call; what to avoid is churn, skills going off and on within an active
conversation.

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
- Event-conditioned "standing intents" in the triggers system.
- Cleanup of stale checkpoints in `threads.sqlite` (#12).
- Re-check Devin's launch around 2026-10-20 for the first user failure reports (DEVIN_MEMORY.md).
