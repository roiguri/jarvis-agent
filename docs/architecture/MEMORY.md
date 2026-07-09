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
├── daily/daily_YYYY-MM-DD.md   # episodic daily logs (written by heartbeat)
├── *.txt / *.md           # long-term memory the agent writes freely
└── threads.sqlite(+-wal,-shm)  # deny-listed exception (LangGraph owns the path)

/app/jarvis_code/prompts/  # DEV-controlled, committed, NOT agent-writable
├── AGENTS.md              # always-on operating rules
└── heartbeat.md           # heartbeat-scope-only tick rules ([NO_ACTION] contract)

/app/jarvis_data/          # tool-opaque state — never in the memory tool surface
├── fitness/fitness.sqlite
├── scheduling/scheduled_events.json
├── heartbeat/state.json   # code-owned per-task last_run stamps (gate input — see HEARTBEAT.md doc)
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

Deletion is blocked for files the system needs to exist; `SOUL.md` additionally requires a Telegram confirmation to **write** (identity changes are user-gated):

| File | Delete | Write |
|---|---|---|
| `SOUL.md` | blocked | confirmation required |
| `USER.md` | blocked | allowed (no confirm) |
| `MEMORY.md` | blocked | allowed |
| `HEARTBEAT.md` | blocked | allowed |

---

## Access Model (per file)

| File | Location | Auto-loaded into prompt | Agent read (memory tools) | Agent write | Confirm |
|---|---|---|---|---|---|
| `SOUL.md` | jarvis_memory | every turn | yes | yes | **yes** |
| `USER.md` | jarvis_memory | every turn | yes | yes | no |
| `AGENTS.md` | **jarvis_code/prompts** | every turn | **no** (outside sandbox) | **no** (deploy only) | n/a |
| `prompts/heartbeat.md` | **jarvis_code/prompts** | heartbeat scope only | **no** | **no** (deploy only) | n/a |
| `MEMORY.md` | jarvis_memory | **no** — read on demand via tools | yes | yes | no (delete blocked) |
| `daily/<today>` | jarvis_memory | user scope (heartbeat: yesterday's) | yes | yes (heartbeat writes) | no |
| `HEARTBEAT.md` | jarvis_memory | heartbeat scope only — **due task blocks only** (non-due collapse to a note; see [HEARTBEAT.md doc](HEARTBEAT.md)) | yes | task edits via `manage_heartbeat_task` (validated + confirmation); raw `write_memory` possible but prompt-forbidden | create/update/delete confirm (delete of file blocked) |
| `chat_history.jsonl` | jarvis_data/logs | heartbeat scope — today's slice | via `get_chat_history` tool | append-only (gateway writes per turn) | n/a |
| `notifications.jsonl` | jarvis_data/logs | user scope — today's `event="heartbeat"` slice | via `get_notification_history` tool | append-only (heartbeat + gateway writers) | n/a |

The key non-obvious row: **`MEMORY.md` is not injected into the prompt.** It is the agent's master *index*; the agent consults it on demand with `read_memory` (AGENTS.md instructs it to). Injecting it every turn was considered and rejected — it would spend tokens on an index the agent only needs when navigating memory, and the tool path already covers that need.

---

## System Prompt Assembly

`build_system_prompt(scope, active_skills, due_tasks=None)` in `agent.py` assembles the prompt **fresh on every model call**. There is no prompt constant in code.

```
[Current time: <Israel local>] / [Active scope: user|heartbeat]   # envelope
SOUL.md            (jarvis_memory — identity/voice)
prompts/AGENTS.md  (code — operating rules; outside the sandbox)
USER.md            (jarvis_memory — durable user profile)
─ scope == "user" ─────────────────────────────────────────────
   _USER_FRAMING (conversational)  +  today's daily log
   + today's heartbeat-sent notifications (live slice of notifications.jsonl)
─ scope == "heartbeat" ────────────────────────────────────────
   _HEARTBEAT_FRAMING (terse tick) + prompts/heartbeat.md
   + HEARTBEAT.md — due task blocks only when due_tasks is a list
     (non-due tasks collapse to a one-line note; None = full file)
   + today's user chat (live slice of chat_history.jsonl)
   + yesterday's daily log
compact_skill_list(scope, active_skills)   # OWNED BY RUNTIME.md, slotted here
```

### Hot reload, crash-safety

Every file is read per turn via `load_or_blank(path)`: returns the stripped file, or `""` on any `OSError`. A missing or transiently-unreadable identity file **degrades the prompt; it never crashes a turn**. Because reads are per-turn, editing `SOUL.md`/`AGENTS.md`/`USER.md`/`heartbeat.md` takes effect on the next turn with no restart (a restart is still recommended to flush the upstream prompt cache).

### Per-scope content (not per-scope capability)

Scope changes *which prompt content* is assembled; tool reachability is owned by [RUNTIME.md](RUNTIME.md) (scope-neutral by default, with per-tool `scopes` opt-in). This layer owns only the file composition of each branch:

- **`user`** — conversational framing + **today's daily log** + **today's heartbeat-sent notifications** (live slice of `notifications.jsonl` filtered to `event="heartbeat"` and timestamps ≥ start-of-Israel-day). The notifications give the chat assistant a live view of what the background tick has already pushed; the daily log carries the richer narrative (still useful, but lagging because it is rewritten only at end-of-tick).
- **`heartbeat`** — terse framing + `prompts/heartbeat.md` (the `[NO_ACTION]` tick contract, present *only* here so it never adds noise to user turns) + `HEARTBEAT.md` **filtered to the due task blocks** (the gate's due-list arrives via `JarvisState["heartbeat_due_tasks"]`; non-due tasks collapse to a one-line note — see [HEARTBEAT.md doc](HEARTBEAT.md)) + **today's user-thread chat** (live slice of `chat_history.jsonl` filtered to `thread_id` starting with `telegram_`) + **yesterday's daily log** (older days are reachable via `read_memory` on demand). The chat slice is what lets the tick detect tasks Roi has already addressed and write a `User handled this on … — skipping today` note instead of duplicating a briefing.

Both live slices are read **directly** by `build_system_prompt` (no tool call), bounded by start-of-Israel-day plus a per-entry length cap, so they add finite tokens regardless of total log size. They sit alongside the daily log rather than replacing it: live for freshness, daily log for narrative.

### The skill block is RUNTIME's

The final `compact_skill_list(...)` section — every skill's `SKILL.md` description, plus active skills' rule bodies — is produced and owned by the tool registry. This layer slots its return value in and does not describe it; see [RUNTIME.md](RUNTIME.md) ("The skill block").

---

## Settled Deviations

Decisions that intentionally diverge from earlier plan sketches; recorded so they are not "fixed" back later.

- **No env-var overrides for relocated paths.** `fitness.sqlite`, `scheduled_events.json`, and the logs use hardcoded constants — no `FITNESS_DB_PATH` etc. A single Roi-operated LXC has no second deployment to parameterize for; an env knob would be dead configuration (YAGNI).
- **AGENTS.md lives in code, not `jarvis_memory/`.** Operating rules change by deploy and must not be agent-mutable; putting them in the version-controlled, sandbox-external `prompts/` dir enforces that structurally rather than via a runtime guard.
- **`MEMORY.md` is tool-read, not prompt-injected** (see Access Model) — a deliberate token trade, not an omission.
- **`threads.sqlite*` stays in `jarvis_memory/`** as the one deny-listed exception, because LangGraph owns the path. Its disk-footprint hygiene (WAL high-water mark, un-VACUUMed free pages) is self-bounded and tracked separately, not fixed here.

---

## Concurrency — `write_memory`

`write_memory` must survive a heartbeat tick and a user turn writing concurrently (distinct threads, but the daily log and a user-written memory can collide). `_exec_write_memory` (`tools/core/memory.py`):

1. takes a process-wide `threading.Lock` (`_WRITE_LOCK`);
2. writes to a `tempfile.NamedTemporaryFile` in the **same directory**;
3. `os.replace()` — atomic rename on one filesystem — to publish.

A reader therefore sees either the old or the new file, never a truncated one; concurrent writers serialize. This mirrors the atomic temp+replace pattern `scheduling.py` already uses for `scheduled_events.json`, plus the lock.

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
| `threads.sqlite` disk-footprint maintenance (WAL/VACUUM) | tracked as a separate enhancement, not built here |

---

## See Also

- [RUNTIME.md](RUNTIME.md) — the agent runtime & tool registry; owns the skill block slotted into the prompt this layer assembles.
- [GATEWAY.md](GATEWAY.md) — the channel boundary; owns the media cache this layer's placement principle points to.
