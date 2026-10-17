# Review — CONTEXT_REDESIGN_PLAN vs. the actual code

**Reviewer angle:** feasibility against the code as of `12c2012` (staging tree). Read: `agent.py`,
`main.py`, `heartbeat.py`, `heartbeat_state.py`, `triggers/`, `pending_mirrors.py`,
`gateway/outbox.py`, `gateway/commands/handlers.py`, `gateway/apps/memory.py`, `tools/registry.py`,
`tools/core/{memory,history,heartbeat,activate_skill}.py`, `observability/telemetry.py`, `tests/`,
`scripts/`, `docs/architecture/*`, and the prod `HEARTBEAT.md`.

## Verdict

The layering fits the code. Most slices can be built on seams that already exist: the reducer
already handles `REMOVE_ALL_MESSAGES` (langgraph 1.2.7), there is a single owner-turn lock, the
Outbox log sink is injected, and FTS5 is compiled into the venv's sqlite 3.40.1. The plan does
underestimate four things:

1. Where git would live. Putting `.git` inside `jarvis_memory/` sits beside a 100 MB checkpoint DB
   and its 68 MB WAL. A deny-list modelled on `threads.sqlite*` would leave `.git/config` writable
   by `write_memory`.
2. How much code reads the heartbeat checkpoint. The ack, failure notes and telemetry snapshots
   all depend on it.
3. What `chat_history.jsonl` does that "write from the final state" does not. It is written
   before the turn runs, and it records slash commands, including `/clear`.
4. Media blobs and the mirror block. Both are present in the turn's final messages, and the
   episode writer has to handle them explicitly.

None of these is fatal. But S1 and S4 cannot ship as written without the fixes below, and S1 also
needs a prod `HEARTBEAT.md` edit that the deploy steps do not list.

---

## Findings

### BLOCKER

**B1. Git inside `jarvis_memory/` versions the checkpoint DB and opens a code-execution path**
(plan S2 lines 51–52; §8 decision 7)

- *Evidence:*
  - `/app/jarvis_memory/` holds `threads.sqlite` (101 MB) and `threads.sqlite-wal` (68 MB). Both
    change on every turn.
  - `_get_safe_path` (`tools/core/memory.py:53`) deny-lists by **basename prefix only**:
    `os.path.basename(safe_path).startswith(_DENIED_PREFIX)`. "Deny-list `.git` like
    `threads.sqlite*`" therefore blocks only a file whose basename starts with `.git`. It does not
    block `.git/config`, `.git/hooks/…` or `.git/info/exclude`.
  - The jarvis-app memory browser (`gateway/apps/memory.py:99`) lists entries with `scandir`. It
    filters only `_DENIED_PREFIX`, so `.git/` would show up there and be readable through
    `_resolve`.
- *Failure scenarios:*
  - **Code execution:** a prompt-injected web page leads to `write_memory(".git/config", "...[core]
    fsmonitor = <cmd>")`. The next per-write `git add`/`commit` runs `<cmd>` as `jarvis_user`.
  - **Repo bloat:** a lazy `git add -A` (or `commit -a`) commits a 170 MB binary on every memory
    write.
  - **Memory file leaks into git internals:** the agent can also overwrite `.git/HEAD`, or write
    files under `.git/`, and corrupt the repository that `/memory undo` relies on.
- *Fix:*
  - Keep the repository **outside the sandbox**: `GIT_DIR=$DATA_DIR/memory_git`,
    `GIT_WORK_TREE=$MEMORY_DIR`. Nothing in `jarvis_memory/` changes, so the placement principle
    stays intact (`docs/architecture/MEMORY.md:30` names `threads.sqlite` as the *sole*
    exception), and no new deny-list entry is needed.
  - Commit with **explicit pathspecs** (the file just written), never `-A`.
  - Put `threads.sqlite*` and `*.tmp` in `$GIT_DIR/info/exclude`. The tmp files come from
    `NamedTemporaryFile` in `_exec_write_memory`.
  - If the plan keeps `.git` in-tree anyway, the guard must reject **any path component** equal to
    `.git` in `_get_safe_path`, `list_memory`, `gateway/apps/memory.py` *and* the plan's tests.

### MAJOR

**M1. Stateless heartbeat: the plan names the outcome, not the mechanism, and three code paths
depend on the checkpoint** (S4 lines 73–80; §6)

- *Evidence:*
  - The ack is read **from the checkpoint after the turn**: `heartbeat._read_ack` calls
    `agent.get_heartbeat_ack(HEARTBEAT_THREAD_ID)`, which calls `agent_executor.get_state`
    (`heartbeat.py:162-169`, `agent.py:1026-1037`). Delivery and stamping both key off that ack.
  - `_failed_outcome` writes the failure note with `update_state` (`agent.py:998`).
  - `ask_jarvis` calls `get_state` at turn start and end (`agent.py:709, 938`) for telemetry and
    for `no_action`.
  - The graph is compiled once, with the checkpointer (`agent.py:655`).
- *Failure scenario:* the obvious implementation is a fresh `thread_id` per tick (for example
  `heartbeat:<turn_id>`).
  - `PruningSqliteSaver` keeps one checkpoint **per thread** (`agent.py:203-218`), so every tick
    leaves a permanent row with the full tick transcript in it. That is about 24 rows a day.
    `threads.sqlite` grows faster than today, which contradicts §10's claim that S4 "removes the
    heartbeat checkpoint's growth".
  - The chat-slice filter `tid == HEARTBEAT_THREAD_ID` (`agent.py:361`) stops matching.
  - `scripts/context_report.py:61` per-thread weights fragment.
- *Fix:*
  - Compile a second graph without a checkpointer for heartbeat scope. Have `ask_jarvis` collect
    the final messages from the stream's last `values` event, and return the ack in
    `TurnOutcome`.
  - Replace `get_heartbeat_ack` and the `update_state` failure note with that in-memory state.
  - Rewrite `test_unsaved_tick_ignores_previous_ack` and the `test_turn_lifecycle` heartbeat tests,
    which assume the shared thread.
  - Filter heartbeat rows by `scope`/`provenance`, never by thread id.

**M2. "Per-task last output text" cannot be derived from the current ack** (S4 line 75; §6
line 231)

- *Evidence:* `heartbeat_respond` (`tools/core/heartbeat.py:96-125`) returns **one**
  `summary` and **one** `notification_text` per tick, plus a flat `acted_tasks` list.
  - Ticks routinely combine tasks. Prod `HEARTBEAT.md` has step-challenge-tracker feed "Steps
    Banked" into the morning-readiness briefing.
- *Failure scenario:* code stores the combined briefing as the "last output" of every acted task.
  The next run of each task sees text that belongs to the others, which is the same
  cross-contamination F2 is meant to remove.
- *Fix:* change the ack schema, for example `outcomes: [{task, outcome, note}]`, or store only a
  code-observable outcome (acted / not acted / notified) and leave narrative continuity in
  `heartbeat/<task>.md`, which already exists for this. A schema change touches
  `tests/golden/tools/core.json` and `prompts/heartbeat.md`, and needs a staging behaviour check
  (§7 says docstrings drive behaviour).

**M3. The episode write "from the final state" drops durability and records that
`chat_history.jsonl` provides today** (S1 lines 33–40; §3 "Write path"; decision 9)

- *Evidence:*
  - The owner's message goes to `chat_history.jsonl` **before** the turn runs (`main.py:60-66`;
    `DEVELOPMENT.md:249`; FORM_BLOCK_PLAN relies on this for form submissions).
  - Slash commands never reach `ask_jarvis`. Their input and reply are logged only in
    `process_inbound_message` (`main.py:54-58`).
  - `/clear` wipes the checkpoint with raw SQL (`handlers.py:60-74`).
- *Failure scenarios:*
  - A crash or restart mid-turn means `finally` never runs, so the owner's message never reaches
    L2. Retiring `chat_history.jsonl` makes this a strict regression.
  - A heartbeat tick that runs during a 30-second user turn no longer sees the in-flight message in
    its chat slice. Today it does, because of the pre-turn write. The tick can duplicate a
    briefing the owner is asking about.
  - `/tz`, `/triggers cancel`, `/clear` and the new `/memory undo` leave no episode rows.
- *Fix:*
  - Write the owner row (raw text, before `_turn_stamp`) **before** the graph runs, keyed by
    `turn_id`. Write the rest at the end.
  - Write `system_record` rows for slash commands, in `process_inbound_message` or the command
    router, so the gateway still stays model-free.
  - Retire `chat_history.jsonl` only after both are in place.

**M4. `/clear` content comes back through compaction** (S3 line 60; §4 "Source")

- *Evidence:*
  - The compaction source is "episode rows since the last consolidation", which is up to about 21
    hours of rows.
  - `/clear` deletes only the checkpoint (`handlers.py:64-71`), leaves no episode marker (M3), and
    **does not take the owner-turn lock**. It runs in `try_handle_command` before
    `run_owner_turn` (`main.py:54`).
- *Failure scenarios:*
  - The owner clears the conversation at 14:00. At 18:00 a compaction re-derives from 03:00
    onward, and the "cleared" morning comes back as an injected summary.
  - Separately, a `/clear` that lands between the compaction's checkpoint read and its
    `update_state` can be overwritten with summary plus tail. Compare-and-swap only works if every
    writer holds the lock.
- *Fix:*
  - `/clear` writes a boundary row to the episode store, and the compaction span starts at
    max(last consolidation, last clear).
  - Run `/clear`'s wipe under `_owner_turn_lock` (move the lock to a shared module, or add a
    command-side hook in `main.py`).

**M5. Compaction scheduling: "under the owner-turn lock, after the reply is delivered" isn't a
point that exists in the code, and holding the lock across an LLM call stalls the owner** (§4
"When")

- *Evidence:* `run_owner_turn` releases the lock (`main.py:96-103`) **before** the reply is sent.
  The channel router sends it after `on_message` returns (`telegram/router.py:230`,
  `jarvis_app/router.py:385`). Nothing domain-side runs "after delivery".
- *Failure scenario:*
  - Holding the lock: a compaction call over a day's rows (tool results truncated to 2k each, per
    the report) takes 10–30 seconds. The owner's next message, a quick follow-up, waits behind it.
  - Re-acquiring the lock afterwards: an inbound message already queued on the lock wins, so the
    compare-and-swap fails. During a burst of messages, compaction keeps getting skipped until the
    50-message backstop trims without a summary.
- *Fix:*
  - In `run_owner_turn`, after release, spawn `asyncio.create_task`. Read the checkpoint id and
    the span, then run the LLM **outside** the lock.
  - Take the lock only to re-check the checkpoint id and apply
    `[RemoveMessage(REMOVE_ALL_MESSAGES), summary, *tail]` via
    `update_state(..., as_node="llm")`.
  - Keep tail messages as the **checkpoint objects**, never rebuilt from episode rows. AIMessages
    carry Gemini thought signatures in `additional_kwargs`
    (`langchain_google_genai/chat_models.py:139`).

**M6. Compaction can re-fire every turn when the kept tail is already above the trigger** (S3
line 58; §4 "Trigger")

- *Evidence:* turns with many tool calls are normal. `test_fanout_turn_keeps_its_input` exists,
  and the reducer comment cites about 40 calls in one turn (`agent.py:143-144`).
- *Failure scenario:* the last 3 whole turns hold 45 messages. After every turn the count is above
  40, so compaction runs, removes little or nothing, passes or fails the "smaller than what it
  replaces" check, and runs again on the next turn.
  - The thrash guard as written fires only on *failure*, so a valid but useless compaction
    repeats, paying an LLM call and a cache break each time.
- *Fix:* trigger only when the **removable span**, meaning everything before the low-watermark
  cut, reaches a minimum (for example 15 messages or N tokens). Compute the cut with the
  reducer's own turn-start rule, a shared helper with `_add_and_trim:154-158`, so a mirror block
  stays fused with its turn.

**M7. Base64 media and the mirror block both sit in the turn's final messages** (§3 "Row",
"Write path")

- *Evidence (media):* `_add_and_trim` strips blobs only from **existing** messages at the *next*
  write (`agent.py:137-141`). At turn end, the new HumanMessage still carries `image_url`
  data-URLs and `media` `data` fields, up to 100 MB (`agent.py:55, 810-857`). Images get no path
  hint at all, only `[image attached]`.
- *Evidence (mirror):* the drained mirror block is inserted as a user-role message
  (`agent.py:869-870`), indistinguishable by type from owner input.
- *Failure scenarios:*
  - A 42 MB video turn writes about 56 MB of base64 into `episodes.sqlite` and its FTS index.
  - The mirror block is stored as `role=owner`. S5's "quote must match an owner row" check then
    accepts Jarvis's own briefing text as owner speech, which is the attribution laundering
    COMPACTION_AND_DREAMING.md §87 warns about.
  - The same send is also stored a second time as an Outbox `jarvis_sent` row, so FTS hits are
    duplicated.
- *Fix:*
  - Pass every stored message through `_strip_media_blobs` first, and take media paths from
    `media_attachments`.
  - Identify the mirror message by identity in `ask_jarvis`, since it knows `mirror_block`. Store
    it as `role=system_record, provenance=mirror` holding a reference to the Outbox rows, not a
    copy.

**M8. S1 breaks the prod `daily-log` task, and the deploy steps omit the edit** (S1 line 38;
"Deploy steps" line 113)

- *Evidence:* the prod `HEARTBEAT.md` `daily-log` task body (line 54) calls
  `get_notification_history(limit=30)`. That task stays until S5. `prompts/AGENTS.md:22` names
  both history tools, as does `docs/architecture/HEARTBEAT.md:82`.
- *Failure scenario:* after the S1 deploy, every `daily-log` run (every 3 hours) gets
  "Tool 'get_notification_history' does not exist" (`agent.py:591`), and the daily log loses
  proactive sends.
- *Fix:* add an S1 deploy step that edits the prod `HEARTBEAT.md` `daily-log` body to use
  `search_history`, and update AGENTS.md and the heartbeat doc in the same slice. Add
  `tests/golden/tools/core.json`, `surface.md` and `test_tool_surface` updates to S1's checklist;
  only S2 currently mentions golden updates.

**M9. Compaction and consolidator token costs go unrecorded or land on user turns** (S0; §9
readings; S3 line 66)

- *Evidence:* `record_llm_call` returns early when `TURN_ACC` is unset (`telemetry.py:123-125`).
  Inside `ask_jarvis`, the accumulator is the user turn's.
- *Failure scenario:*
  - The nightly consolidator and any compaction run outside `ask_jarvis` are invisible to
    `turns.jsonl`, `/usage` and §9.
  - Compaction run inside the turn context inflates "user input / turn".
  - Either way, the before/after readings the plan is gated on are wrong.
- *Fix:* each background LLM job opens its own accumulator with a distinct `scope`
  (`compaction`, `consolidation`) and emits a `turns.jsonl` row. Add those scopes to §9.

**M10. Daily logs written nightly leave the user scope's "today" view empty** (S5 line 94; §4
line 198; decision 10, which this finding does not challenge)

- *Evidence:*
  - `build_system_prompt` injects `daily/daily_<today>.md` into user scope (`agent.py:445-449`).
  - `heartbeat.py:87` tells every tick the filename of today's log.
  - `/logs` defaults to today (`handlers.py:307-322`).
  - `AGENTS.md:22` says "Today's synthesised context lives in daily/…".
- *Failure scenario:* after S5, today's file never exists during the day. The plan's substitute,
  "the compaction summary is also the today-so-far view", covers only the **owner thread**:
  - Heartbeat ticks that didn't notify (29 of 33, per §1) never reach the owner thread, so the
    user scope loses all awareness of them.
  - On a quiet day no compaction happens at all.
- *Fix:* replace the today's-log injection with a code-built slice from the episode store, for
  example today's heartbeat ack summaries (`provenance=heartbeat`, one line each). Fix
  `heartbeat.py:87`, the `/logs` default, AGENTS.md and `scripts/ci/check_command_replies.py:105`
  in S5. Also note that ticks between 00:00 and 03:00 have no "yesterday's log", because it is
  written at 03:00.

**M11. `/memory undo` as "git revert of the last consolidation commit" is under-specified** (S5
line 93)

- *Evidence:*
  - Per-write commits (S2) interleave hot-path and heartbeat-notes commits after the
    consolidation commit.
  - The owner hand-edits `SOUL.md` and `HEARTBEAT.md` (CLAUDE.md "Change Jarvis's personality",
    "hand-edit HEARTBEAT.md"), leaving uncommitted work-tree changes.
  - `/memory` already parses `args` as a filename (`handlers.py:186-197`).
- *Failure scenarios:*
  - `git revert` refuses on a dirty tree, or conflicts when a "remember this" write touched the
    same USER.md line afterwards.
  - The revert commits the owner's hand edit along with it.
  - `/memory undo` collides with reading a file named `undo`.
- *Fix:*
  - Undo means: reverse-apply the consolidation commit's diff **per file**, under the write lock,
    and refuse any file changed since, naming it.
  - Commit only explicit pathspecs.
  - Treat `undo` as a reserved word, and add a case to `check_command_replies.py`.

### MINOR

- **m1. S2's "file lock" already exists but is too narrow** (S2 line 46).
  - `_WRITE_LOCK` (`tools/core/memory.py:24`) covers only the tmp-write and rename.
  - The consolidator needs one lock across read-hash-check, apply and `git commit`.
  - Hot-path commits must take the same lock, or they collide on `index.lock`.
  - `manage_heartbeat_task` writes through `_exec_write_memory` (`tools/core/heartbeat.py:345`), so
    hooking the commit there covers it. The confirmed `_exec_delete_memory` path
    (`memory.py:312`) needs the hook too.
- **m2. "Skill-owned data" has no code marker** (§5 "Scope"). Files like `fitness/overview.md` are
  agent-written through `write_memory` per SKILL.md rules. No tool writes into `MEMORY_DIR`
  directly, so the consolidator cannot tell them apart. It needs an explicit allowlist, and
  `heartbeat/` and `daily/` should be excluded by path.
- **m3. Nightly scheduling.** Triggers support only one-shot `At` with `Send|Turn`
  (`triggers/model.py:23-40`), so "code action on the triggers scheduler" is a new cron job like
  `add_heartbeat`.
  - Don't copy its 60-second `misfire_grace_time` (`triggers/scheduler.py:53`). A restart around
    03:00 would skip the night. Use a long grace with `coalesce`.
  - Gate the job behind a config flag for staging.
- **m4. Stateless wakes lose their origin context.** A tick-created wake today runs on the same
  thread as the tick that set it. Stateless, it sees only `trigger.action.instruction`. The
  `manage_trigger` docstring (`tools/core/scheduling.py:68`) should require a self-contained
  instruction.
- **m5. Skill decay and parent skills.** Parent namespaces own zero tools (RUNTIME.md:68), so
  "unused in the tail" always decays them while children stay active, and the children then
  render flat.
  - Count a parent as used when any child is used.
  - Per-skill last-use time also needs a source: episode `tool_name` mapped through
    `registry.namespace_of`.
- **m6. `search_history` result size.** It "returns full rows" with "tool results stored in
  full". bm25 over JSON tool-result blobs will outrank owner and Jarvis rows, and one hit can
  return a 50k-token travel payload.
  - Default to owner and Jarvis roles, make tool rows opt-in, and cap each row in the result.
  - Re-estimate volume (§3 "Retention" uses `chat_history.jsonl`'s 1.4 MB, which holds no tool
    results). Use WAL and `busy_timeout`: the writers are the owner thread, the heartbeat thread,
    the Outbox sink and the consolidator.
- **m7. Backfill.** Bound the backfill by the timestamp of the first live row to avoid overlap at
  cutover.
  - Live owner rows carry the `_turn_stamp` prefix (`agent.py:696`); backfilled rows don't. Store
    raw text plus `ts`, so quote checks and FTS treat both alike.
  - Backfilled "assistant" rows for failed turns are `outcome.text`, not the checkpoint's
    `failure_note`. Harmless, but don't treat them as evidence.
- **m8. Unlisted `chat_history.jsonl` dependents:**
  - `TESTING_AND_EVALS_PLAN.md:295` (`evals/draft_case.py`)
  - `tests/fixtures/logs/chat_history.jsonl`, used by the heartbeat prompt golden
  - `DEVELOPMENT.md:249`
  - `CLAUDE.md:71,124,166`
  - `main.py:191` (`trim_log`)
- **m9. The Outbox must write through the injected `log_sink`** (`main.py:219`,
  `gateway/outbox.py:142-150`), not by importing the store. GATEWAY.md:94 says "the gateway
  imports nothing from the tools layer".
- **m10. MEMORY.md injection and caching.** Injected MEMORY.md (S2) changes the byte-stable
  system prompt every time the hot path updates the index, which AGENTS.md:19 tells it to do on
  each new file. Expect cache drops on those days. S0's cache measurement should include one.
- **m11. Caps.** Prod USER.md is 1,359 chars and MEMORY.md is 31 lines, so no deadlock today.
  Still, state the rule as "reject if the result exceeds the cap", so a shrinking write of an
  already-over-cap file passes. Otherwise a future overshoot through the consolidator locks the
  file.
- **m12. Golden snapshots.** The `read_memory` docstring change (S2 line 47) changes
  `tests/golden/tools/core.json`, not only the prompt golden.

---

## Things the plan gets right

- **Ordering by dependency.** S1 (durable record) comes before S3 (dropping context) and S5
  (moving writes off the hot path). The 50-message reducer stays as the backstop, so a failed or
  skipped compaction costs exactly today's behaviour.
- **Placement.** `episodes.sqlite` goes in `jarvis_data/`, which is correct under the placement
  principle, and `notifications.jsonl` stays as the mirror queue. That keeps
  `pending_mirrors.py` and its cursor semantics untouched.
- **The head-of-window user-role summary.** It reuses a shape the reducer already treats as one
  turn's input (consecutive HumanMessages, `agent.py:150-158`) and that Gemini already accepts
  through the mirror block.
- **Skill decay at compaction, keeping any skill with calls in the tail.** History never holds a
  call to an unbound tool, and the cache break lands where the prefix is already being rewritten.
- **Code-owned validation everywhere.** Owner-quote substring checks, the stale-file check before
  commit, and the cursor advancing only after the commit match the codebase's existing
  stamp-after-delivery discipline.
- **Measure first (S0).** Pricing errors have bitten before, so this is warranted. M9 is the gap
  that would undermine it.
