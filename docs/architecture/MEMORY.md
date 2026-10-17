# Memory & Identity Architecture

## Purpose

This layer answers three coupled questions:

1. **Where does each piece of state live**, and who is allowed to change it?
2. **What is genuine agent memory** versus tool-opaque state versus dev-controlled prompt content versus channel artifacts?
3. **How is the system prompt assembled** from files, per turn, per scope?

It owns the `/app/jarvis_memory/` surface, the `/app/jarvis_data/` and `/app/jarvis_code/prompts/` boundaries, the memory-tool sandbox, and `build_system_prompt`. It does **not** own the skill block of the prompt (that is the registry's — see [RUNTIME.md](RUNTIME.md)) nor the channel-owned media cache (see [GATEWAY.md](GATEWAY.md)); it references both.

---

## The Placement Principle

> `/app/jarvis_memory/` contains **only genuine memory**: markdown the agent
> both *reads* and *mutates* through the memory tools. Everything else lives
> by **who changes it**, not by what it is conceptually "about".

This is the single rule that governs every file's home. Apply it by asking *who writes this file*:

| Who changes it | Home | In the memory tool surface? |
|---|---|---|
| The **agent**, via `write_memory`/`delete_memory` (markdown it also reads back) | `/app/jarvis_memory/` | **Yes** — this *is* the surface |
| **Code / deploy** only (prompt, operating rules) | `/app/jarvis_code/prompts/` | No — version-controlled, outside the sandbox |
| **Tools** only, never `read_memory`'d (opaque DBs, append-only logs, code-managed JSON) | `/app/jarvis_data/<tool>/` | No — opaque to the agent |
| A **channel** (inbound media blobs) | the channel's own gateway dir | No — channel-owned ([GATEWAY.md](GATEWAY.md)) |

**Sole exception:** `threads.sqlite(+-wal,-shm)` physically lives in `/app/jarvis_memory/` because LangGraph's `SqliteSaver` owns that path and it cannot be relocated. It is therefore **deny-listed** from the memory tools (`_get_safe_path` rejects it) so it never appears in `list_memory` or is readable/writable as "memory". The checkpointer mechanism itself is owned by [RUNTIME.md](RUNTIME.md) (State); this layer only carves out the exception.

### Why this rule (not "group by topic")

Grouping by subject ("all scheduling stuff together") put a tool-opaque SQLite DB and a code-managed JSON file inside the agent's markdown memory dir, where `list_memory` surfaced them and a stray `read_memory` could corrupt them. Grouping by *mutator* makes the invariant checkable: anything in `jarvis_memory/` must be agent-mutable markdown; anything that isn't, leaves. The directory's contents become self-describing.

---

## Directory Layout

```
/app/jarvis_memory/        # ONLY genuine memory — agent reads AND writes via memory tools
├── SOUL.md                # user-curated identity/voice (agent-writable w/ confirmation)
├── USER.md                # durable user profile/prefs (agent-writable, no confirm)
├── MEMORY.md              # agent-maintained master index of memory files
├── HEARTBEAT.md           # recurring task list (edited via manage_heartbeat_task)
├── heartbeat/*.md         # per-task NOTES files (narrative state; machine last_run lives in jarvis_data)
├── daily/daily_YYYY-MM-DD.md   # daily logs, written the morning after (see "Daily log")
├── *.txt / *.md           # long-term memory the agent writes freely
└── threads.sqlite(+-wal,-shm)  # deny-listed exception (LangGraph owns the path)

/app/jarvis_code/prompts/  # DEV-controlled, committed, NOT agent-writable
├── AGENTS.md              # always-on operating rules
└── heartbeat.md           # heartbeat-scope-only tick rules

/app/jarvis_data/          # tool-opaque state — never in the memory tool surface
├── fitness/fitness.sqlite
├── triggers/triggers.json # code-owned: pending triggers, gate state, task last_run stamps (gate input — see HEARTBEAT.md doc)
├── episodes/episodes.sqlite  # the episode store — everything that happened (see "Episode store")
└── logs/{chat_history,notifications}.jsonl

/app/jarvis_code/gateway/*/media_cache/   # channel-owned (see GATEWAY.md)
```

---

## The Memory Tool Sandbox

`_get_safe_path(filename)` in `tools/core/memory.py` resolves every memory-tool path against `MEMORY_DIR = /app/jarvis_memory` and rejects:

- `..` traversal and absolute escapes;
- sibling-prefix attacks — the check is `startswith(MEMORY_DIR + os.sep)`, so `/app/jarvis_memory_evil/` is **not** accepted as inside the sandbox.

Consequences that the rest of this architecture relies on:

- **`prompts/AGENTS.md` needs no protection logic.** It lives in `/app/jarvis_code/`, outside the sandbox, so the memory tools *physically cannot* read or write it. Dev-controlled prompt content is unreachable by the agent by construction, not by a deny-list.
- **`/app/jarvis_data/`** is likewise unreachable — tool-opaque state cannot be `read_memory`'d or corrupted by the agent.
- **`threads.sqlite*`** is inside the sandbox dir, so it needs an *explicit* deny-list entry (the one exception above).

### Protected files

Deletion is blocked for files the system needs to exist; `SOUL.md` additionally requires a Telegram confirmation to **write** (identity changes are user-gated), and `HEARTBEAT.md` rejects direct writes entirely (`manage_heartbeat_task` is the only write path). Guards compare the canonical sandbox-relative name (resolved by the same path logic the operation uses), so alias spellings like `./SOUL.md` cannot bypass them:

| File | Delete | Write |
|---|---|---|
| `SOUL.md` | blocked | confirmation required |
| `USER.md` | blocked | allowed (no confirm) |
| `MEMORY.md` | blocked | allowed |
| `HEARTBEAT.md` | blocked | blocked — `manage_heartbeat_task` only |

---

## Access Model (per file)

| File | Location | Auto-loaded into prompt | Agent read (memory tools) | Agent write | Confirm |
|---|---|---|---|---|---|
| `SOUL.md` | jarvis_memory | every turn | yes | yes | **yes** |
| `USER.md` | jarvis_memory | every turn | yes | yes | no |
| `AGENTS.md` | **jarvis_code/prompts** | every turn | **no** (outside sandbox) | **no** (deploy only) | n/a |
| `prompts/heartbeat.md` | **jarvis_code/prompts** | heartbeat scope only | **no** | **no** (deploy only) | n/a |
| `MEMORY.md` | jarvis_memory | every turn (both scopes, after USER.md) | yes | yes | no (delete blocked) |
| `HEARTBEAT.md` | jarvis_memory | heartbeat scope only — **due task blocks only** (non-due collapse to a note; see [HEARTBEAT.md doc](HEARTBEAT.md)) | yes | task edits via `manage_heartbeat_task` (validated before write); raw `write_memory` rejected in code | no (delete of file blocked) |
| `chat_history.jsonl` | jarvis_data/logs | heartbeat scope — today's slice | no (the episode store holds the same messages) | append-only (gateway writes per turn) | n/a |
| `notifications.jsonl` | jarvis_data/logs | user scope — undrained rows mirrored into the owner thread as one user-role block (cursor-tracked; not a prompt slice) | no (the episode store holds the same sends) | append-only (heartbeat + gateway writers) | n/a |
| `episodes.sqlite` | jarvis_data/episodes | user scope — the today view (heartbeat activity) | via `search_history` tool | code only, as things happen | n/a |

**`MEMORY.md` is injected** (since the context redesign's S3). It is the agent's master *index*: with it in context the agent knows what files exist without a `read_memory` call, which earlier turns spent on most memory questions. It was kept out at first to save tokens; at ~2k chars (~450 tokens) the trade favours injecting it.

**Size targets, not caps.** `USER.md` (4,000 chars) and `MEMORY.md` (8,000 chars) have targets (`CORE_FILE_TARGETS` in `agent.py`). Writes are never refused for size — refusing mid-conversation would make the agent restructure files while the owner talks to it. Keeping the files under target is the nightly consolidation's job (moving detail into topic files). As a safety net only, the prompt includes at most 3× a target, with a `[truncated — consolidation pending]` marker and a journal warning.

---

## System Prompt Assembly

`build_system_prompt(scope, active_skills, due_tasks=None)` in `agent.py` assembles the prompt **fresh on every model call**. There is no prompt constant in code.

```
[Current date: <Israel date>] / [Active scope: user|heartbeat]   # envelope
SOUL.md            (jarvis_memory — identity/voice)
prompts/AGENTS.md  (code — operating rules; outside the sandbox)
USER.md            (jarvis_memory — durable user profile)
MEMORY.md          (jarvis_memory — index of memory files)
─ scope == "user" ─────────────────────────────────────────────
   _USER_FRAMING (conversational)
   + today view (what the heartbeat did today, from the episode store)
   (proactive sends reach the thread as mirrored history, not the prompt)
─ scope == "heartbeat" ────────────────────────────────────────
   _HEARTBEAT_FRAMING (terse tick) + prompts/heartbeat.md
   + HEARTBEAT.md — due task blocks only when due_tasks is a list
     (non-due tasks collapse to a one-line note; None = every task but gated ones)
   + today's user chat (live slice of chat_history.jsonl)
   + today view (episode store) + yesterday's daily log (daily/)
compact_skill_list(scope, active_skills)   # OWNED BY RUNTIME.md, slotted here
```

**The clock is not in the prompt.** `ask_jarvis` reads the time once at turn start and prefixes it
to the turn's input (`_turn_stamp`: `[Saturday, 2026-10-03 14:05 Israel time]`, plus the owner's
clock in `/tz` away mode). Every call in the turn therefore sees the same "now", and the prompt —
rebuilt per call — changes only when the date does. The stamp lives in the checkpoint, so history
messages keep the time they arrived; `chat_history.jsonl` stays unstamped (it has its own `ts`).

### Hot reload, crash-safety

Every file is read per turn via `load_or_blank(path)`: returns the stripped file, or `""` on any `OSError`. A missing or transiently-unreadable identity file **degrades the prompt; it never crashes a turn**. Because reads are per-turn, editing `SOUL.md`/`AGENTS.md`/`USER.md`/`heartbeat.md` takes effect on the next turn with no restart (a restart is still recommended to flush the upstream prompt cache).

### Per-scope content (not per-scope capability)

Scope changes *which prompt content* is assembled; tool reachability is owned by [RUNTIME.md](RUNTIME.md) (scope-neutral by default, with per-tool `scopes` opt-in). This layer owns only the file composition of each branch:

- **`user`** — conversational framing + the **today view**. Proactive sends (briefings, reminders) are not a prompt slice: the pending-mirror drain delivers them into the owner thread as conversation history at the next user turn, so replies to them have their antecedent.
- **`heartbeat`** — terse framing + `prompts/heartbeat.md` (the tick rules, present *only* here so they never add noise to user turns) + `HEARTBEAT.md` **filtered to the due task blocks** (the gate's due-list arrives via `JarvisState["heartbeat_due_tasks"]`; non-due tasks collapse to a one-line note — see [HEARTBEAT.md doc](HEARTBEAT.md)) + **today's user-thread chat** (live slice of `chat_history.jsonl`, every thread except `heartbeat` and `*_test`) + the today view + **yesterday's daily log** (`daily/daily_YYYY-MM-DD.md`, written the morning after; older days via `read_memory` or `search_history`). The chat slice is what lets the tick detect tasks the owner has already addressed and write a `User handled this on … — skipping today` note instead of duplicating a briefing.

Both live slices are read **directly** by `build_system_prompt` (no tool call), bounded by start-of-Israel-day plus a per-entry length cap, so they add finite tokens regardless of total log size. The today view comes from the episode store the same way.

### The skill block is RUNTIME's

The final `compact_skill_list(...)` section — every skill's `SKILL.md` description, plus active skills' rule bodies — is produced and owned by the tool registry. This layer slots its return value in and does not describe it; see [RUNTIME.md](RUNTIME.md) ("The skill block").

---

## Episode store

`jarvis_data/episodes/episodes.sqlite` (package `episodes/`) records everything that happened, in
order, written by code as it happens. It is a separate file from the telemetry store: this one is
conversation content.

**Rows.** One per thing: `ts`, `turn_id` (the telemetry turn ID, so the stores join), `thread_id`,
`scope`, `channel`, `role` (`owner`, `jarvis`, `tool_call` — name and arguments, `tool_result`,
`jarvis_sent` — a delivered notification, `system` — a tick or wake instruction or a confirmation
result), `source` (`chat`, `heartbeat`, `wake`, `slash_command`, `outbox`, `import`), `tool_name`,
`meta` (JSON extras: notification event, trigger, call ID) and `content`. An FTS5 index covers
`content`.

**When rows are written** (`episodes/record.py`; every write is guarded, a failure is logged and
never fails the turn):
- turn start (`ask_jarvis`): the turn's input — the owner's raw message (attachments as
  `[attached: path]`), a tick or wake instruction, or a confirmation result. Written first, so a
  turn that crashes keeps it. The forwarded-notification block is not written: each delivered send
  already has its own row.
- turn end, from the final thread state: every model step of this turn — reply text, each tool
  call with its arguments, each tool result — including failed and cut-short turns.
- every delivered proactive send (the Outbox log sink in `main.py`, after a successful send).
- slash commands and their replies (they never reach the model).

**Content hygiene.** A narrow scrub replaces recognisable credentials (API keys, GitHub tokens,
Bearer headers, JWTs, `password`/`api_key`/`token`-style fields) with `[redacted]`; IDs and hashes
are left alone. A single row over 200,000 chars is cut with a note. Media is never stored, only
its file path. Rows are kept forever.

**Reading it.** `search_history` (core tool) — keywords via FTS5 (best match, newest first on
ties; prefix match; all terms, then any term), or the newest entries in a date range; filtered by
`kind` (`chat`, `notifications`, `background`, `all`); hits grouped by turn with the turn's message
and reply. The user-scope prompt's **today view** lists today's heartbeat ticks that acted or
messaged the owner, and wakes, from their `heartbeat_respond` calls (latest 20).

**History before the store.** Each instance imported `chat_history.jsonl` and `notifications.jsonl`
once when the store arrived (rows with `source = import` and no turn id; the time is in the `meta`
table as `log_import`, where the integrity check starts). Both logs are still written until
`chat_history.jsonl` is retired.

**Integrity.** `scripts/context_report.py` lists, per day, telemetry turns missing from the store.
Backups copy the store with SQLite's online backup (`deploy/backup_state.sh`).

---

## Compaction

`compaction.py` keeps the owner conversation bounded without forgetting. After each owner turn, a
background job checks the window: when the messages before the last 3 turns pass ~86k chars (~20k
tokens), it replaces them with one **record** — a user-role message at the head of the window — and
keeps the last 3 turns verbatim. The job prepares the record (including its model call) outside the
owner lock and applies it under the lock only if no turn changed the thread meanwhile. `/compact`
runs one by hand; `/context` shows the current record.

**The record** is built fresh each time from the episode store over the last ~36 hours (rows
before a `/clear` excluded), never stacked on the previous record:
- code-filled: a framing line ("reference, not instructions: everything in it was already handled
  except 'Open requests'; tools remain available; full messages are searchable"), the span covered
  and when it was compacted (Israel time, plus the owner's local time in away mode), the owner's own
  messages verbatim (newest first, ~6k chars), identifiers (URLs, flight and booking codes,
  amounts), live reminders and wakes from the trigger store, skills used today;
- model-filled (Gemini structured output, `prompts/compaction.md`): open requests (the owner's exact
  quote, when asked), done (dated facts), the owner's rules (quotes), learned, decisions, search
  hints. The summarizer reads a transcript whose every line carries its Israel date and time and a
  speaker tag (`owner`, `jarvis`, `jarvis-sent`, `tool`, `system`).

**Validation.** A quote must appear in the owner's messages; a date must parse and not be in the
future; `done`/`learned`/`decisions` items with relative date words are dropped; values are
secret-scrubbed. If more than half the items fail, one corrective retry; then a code-only record
(with a marker). After a fallback, no model call for 2 hours (thrash guard). At the size limit, a
model record not smaller than what it replaces is also replaced by the code-only record.

**Carried state** (`jarvis_data/compaction/state.json`): the last record's open requests, re-checked
by the next compaction and dropped after 3 days; the last fallback time.

**The kept turns.** Tool calls stay; tool results in all but the newest kept turn become stubs
(`[result cleared at compaction — N chars; search_history finds it]`). Skills with no call in the
kept turns are switched off. The window changes only at a compaction, so the cached prefix holds
between them. The 50-message trim remains as a backstop and never evicts the record.

Each compaction is a telemetry job row (`job = "compaction"`) with its model call.

## The morning job

`dream.py` runs each morning at 08:30 Israel time, when the heartbeat is enabled (a process started
before noon catches up a missed run, tracked in `jarvis_data/dream/state.json`). For yesterday it
writes the daily log and sends the memory suggestions: two separate model calls, neither sees the
other's output. `/dream [yesterday|today]` runs both by hand.

## Daily log

`daily_log.py` writes a short summary of a finished day to `daily/daily_YYYY-MM-DD.md`, one model
call reading the day as the episode store holds it: what the heartbeat did (the background lines of
the today view, for that day) and the owner conversation (the owner's messages, Jarvis's replies
and its own sends; tool traffic left out). The prompt (`prompts/daily_log.md`) asks for
Conversations (each topic with its outcome), Heartbeat Activity and Notes (what is still open), and
only what the input shows. A day where nothing happened writes nothing; an answer cut off at the
output cap is not written; writing again replaces the file. The heartbeat reads yesterday's log as
its "yesterday"; `/logs <date>` shows a finished day's log and `/logs today` the code-built day view
(background activity and the conversation so far). Daily logs written before this job, by a
heartbeat task, use the same files. Each run is a telemetry job row (`job = "daily_log"`).

## Memory suggestions

`memory_suggestions.py` helps keep long-term memory current without writing it. One model call
reads:

- yesterday's owner conversation, through compaction's transcript builder: the owner's messages,
  Jarvis's replies and Jarvis's own sends (`[owner]` / `[jarvis]` / `[jarvis-sent]`); tool traffic
  and the heartbeat's inner work are left out;
- every memory file the agent maintains, each under its path with its size and last-changed date
  (SOUL.md marked read-only); HEARTBEAT.md, `heartbeat/` notes and `daily/` are not shown.

The prompt (`prompts/memory_suggestions.md`) asks for at most seven suggestions in sections
(corrections and preferences, facts, out of date, organization, guesses), each with the file, the
exact text and a quoted reason. What counts as organized, duplicated or too large is the model's
judgement; code imposes no rules on memory. The note goes out as a `notification` event with
`job: "memory_suggestions"` in its metadata, which also lifts the pending-mirror's per-entry cap so
the whole note reaches the conversation: the owner can answer "do 2 and 5" and Jarvis applies them
with its memory tools. A day with no owner message makes no model call; "Nothing to suggest." sends
nothing; a note cut off at the output cap is not sent. Each run is a telemetry job row
(`job = "memory_suggestions"`).

---

## Settled Deviations

Decisions that intentionally diverge from earlier plan sketches; recorded so they are not "fixed" back later.

- **No env-var overrides for relocated paths.** `fitness.sqlite`, `triggers.json`, and the logs use hardcoded constants — no `FITNESS_DB_PATH` etc. A single Roi-operated LXC has no second deployment to parameterize for; an env knob would be dead configuration (YAGNI).
- **AGENTS.md lives in code, not `jarvis_memory/`.** Operating rules change by deploy and must not be agent-mutable; putting them in the version-controlled, sandbox-external `prompts/` dir enforces that structurally rather than via a runtime guard.
- **`MEMORY.md` is tool-read, not prompt-injected** (see Access Model) — a deliberate token trade, not an omission.
- **`threads.sqlite*` stays in `jarvis_memory/`** as the one deny-listed exception, because LangGraph owns the path. It keeps itself small: `agent.keep_db_small` (at service start) turns on `auto_vacuum=FULL` (freed pages go back on each commit; one VACUUM the first time) and caps the WAL at 4 MB.

---

## Concurrency — `write_memory`

`write_memory` must survive a heartbeat tick and a user turn writing concurrently (distinct threads, but a tick's notes file and a user-written memory can collide). `_exec_write_memory` (`tools/core/memory.py`):

1. takes a process-wide `threading.Lock` (`_WRITE_LOCK`);
2. writes to a `tempfile.NamedTemporaryFile` in the **same directory**;
3. `os.replace()` — atomic rename on one filesystem — to publish.

A reader therefore sees either the old or the new file, never a truncated one; concurrent writers serialize. This mirrors the atomic temp+replace pattern `triggers/store.py` uses for `triggers.json`, plus the lock.

---

## Adding or Relocating State — Checklist

When introducing new persistent state, ask *who mutates it* and place by the principle:

1. **Agent-mutable markdown the agent reads back?** → `/app/jarvis_memory/`. It will appear in `list_memory`; add a protection entry only if the system requires it to exist.
2. **A tool's opaque store the agent never `read_memory`'s?** → `/app/jarvis_data/<tool>/`, hardcoded path, tool ensures `os.makedirs(..., exist_ok=True)`. It is unreachable by memory tools automatically.
3. **Prompt/rule content changed only by deploy?** → `/app/jarvis_code/prompts/`. Unreachable by the agent by construction; wire it into `build_system_prompt` via `load_or_blank`.
4. **A channel's inbound artifact?** → not this layer — see [GATEWAY.md](GATEWAY.md).

Relocations out of `jarvis_memory/` also need a one-time `mv` of the live data at deploy and removal of any now-defunct protection entry.

---

## Boundaries (deliberately not part of this layer)

| Item | Owner |
|---|---|
| The skill block (`compact_skill_list`, SKILL.md format, active-only rules) | [RUNTIME.md](RUNTIME.md) |
| Scope as a runtime parameter (informational, not a permission boundary) | [RUNTIME.md](RUNTIME.md) |
| The LangGraph checkpointer / `threads.sqlite` mechanism | [RUNTIME.md](RUNTIME.md) |
| Channel-owned media cache (absolute paths, no core resolver) | [GATEWAY.md](GATEWAY.md) |

---

## See Also

- [RUNTIME.md](RUNTIME.md) — the agent runtime & tool registry; owns the skill block slotted into the prompt this layer assembles.
- [GATEWAY.md](GATEWAY.md) — the channel boundary; owns the media cache this layer's placement principle points to.
