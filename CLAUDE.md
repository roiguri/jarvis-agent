# Jarvis — Developer Guide for Claude Code

This file is for Claude Code sessions only. It is **not visible to Jarvis** — `_get_safe_path()` in `tools/core/memory.py` sandboxes memory access to `/app/jarvis_memory/`, and this file lives in `/app/jarvis_code/`.

---

## What Jarvis Is

Jarvis is a stateful, proactive AI assistant running as a systemd service on a home server. It operates through a Telegram bot and can take actions: manage media, set reminders, search the web, read/write its own memory, and run scheduled background checks.

- **Single-user**: designed for one owner (no multi-tenant routing)
- **Runtime**: Python, hand-rolled LangGraph `StateGraph` agent (scoped tool registry + same-turn skill activation), Google Gemini model
- **Persistence**: SQLite (LangGraph thread state), JSONL logs, Markdown memory files

---

## Repository Layout

```
/app/jarvis_code/          # Application code (this repo)
├── agent.py               # LangGraph agent + system prompt construction
├── heartbeat.py           # APScheduler heartbeat runner (pre-LLM due-gate + tick ack handling)
├── heartbeat_state.py     # code-owned HEARTBEAT.md parser, due-gate (any_due), state.json stamps
├── turn_context.py        # ambient per-turn ContextVars (CURRENT_SCOPE) — set by ask_jarvis, read by tools
├── timeutils.py           # shared Israel-time home: ISRAEL_TZ + Sunday-anchored week bounds
├── pending_mirrors.py     # pending-mirror drain: proactive sends → owner-thread history (cursor-stamped)
├── main.py                # Entry point
├── gateway/               # Channel-decoupled messaging boundary (see docs/architecture/GATEWAY.md)
│   ├── base.py            # Channel ABC + InboundMessage (neutral contracts)
│   ├── outbox.py          # Outbox — single owner-send seam (log-on-success, SendOutcome, thread→loop bridge)
│   ├── factory.py         # per-channel build_*_stack(); default_outbox(); get_confirmation(); default_owner_thread_id()
│   ├── confirmation/      # Confirmation/ConfirmationUI ABCs + InMemoryConfirmationStore
│   ├── blocks/            # Neutral interactive-block contracts (Form + BlockAction); channel
│   │                      #   adapters map them to their wire (see GATEWAY.md § Interactive Blocks)
│   ├── commands/          # Channel-agnostic slash-command dispatch (pre-LLM short-circuit)
│   ├── apps/              # Channel-agnostic structured surfaces — request/response, no model
│   ├── channels/          # Concrete channels, one dir each (telegram/, jarvis_app/) — ONLY channel-specific code
│   └── webhook/           # Channel-agnostic: FastAPI server + media notifier
├── prompts/               # DEV-controlled prompt content, committed and NOT agent-writable: AGENTS.md
│                          #   (always-on operating rules) + heartbeat.md (tick rules)
├── tools/                 # registry.py is the mechanism (@tool_register, scoped get_tools, SKILL.md parsing);
│                          #   core/ is always-on; every other dir is an activatable skill, and a parent skill
│                          #   may own zero tools and nest sub-skills (see "Add a new tool" below)
├── observability/         # Per-turn LLM telemetry (telemetry.py, usage.py) — app-layer infra, NOT an agent tool
├── scripts/               # Dev/ops entry points: trace.py (per-turn timeline), ci/ (guards), jrestart*.sh, check_env.sh
└── DEVELOPMENT.md         # Operational/dev runbook (env, constants, systemd, firewall, local testing)

/app/jarvis_memory/        # ONLY genuine memory: markdown the agent both reads AND writes via memory tools
├── SOUL.md                # User-curated identity (protected — write requires button)
├── USER.md                # Durable user profile/prefs (protected — agent-writable, no confirm)
├── MEMORY.md              # Agent-maintained index of all memory files (protected)
├── HEARTBEAT.md           # Active heartbeat task list (protected; edited via manage_heartbeat_task)
├── heartbeat/             # Per-task NOTES files (agent narrative state; machine last_run → jarvis_data)
│   └── *.md
├── daily/                 # Daily context logs (written by heartbeat)
│   └── daily_YYYY-MM-DD.md
├── threads.sqlite(+-wal,-shm)  # LangGraph checkpointer — deny-listed exception (path owned by LangGraph)
└── *.txt / *.md           # Persistent memory files (Jarvis writes freely)

/app/jarvis_data/          # Tool-opaque state — NEVER in the memory tool surface, never read_memory'd
├── fitness/fitness.sqlite          # fitness-skill DB (hardcoded path, no env override)
├── scheduling/scheduled_events.json# pending reminders (scheduler-owned)
├── heartbeat/state.json            # code-owned per-task last_run stamps (heartbeat_state.py; gate input)
├── agent/mirror_cursor.json        # pending-mirror drain cursor (agent.py; last mirrored notification ts)
└── logs/
    ├── chat_history.jsonl, notifications.jsonl  # 90-day JSONL, Jarvis-readable via history tools
    └── turns.jsonl, tool_calls.jsonl            # 90-day JSONL, app-only (observability/), agent never reads

/app/jarvis_code/gateway/channels/telegram/media_cache/   # Channel-owned media blobs (gitignored;
                                                 #   absolute paths from gateway/channels/telegram/media_cache.py)
/app/jarvis_code/gateway/channels/jarvis_app/media_cache/ # jarvis-app channel's own media blobs (gitignored;
                                                 #   same channel-owned pattern, filenames embed the att_ id)

/app/secrets/.env          # API keys and tokens — DO NOT READ THIS FILE
```

> **Granularity is deliberate.** The `/app/jarvis_code/` tree stops at directories, naming a file
> only where that one file *is* the concept. Adding a module inside an existing directory needs no
> edit here — only a new top-level concept does. Don't re-expand it to file level: an exhaustive
> listing is what `ls` is for, and the two spots that had drifted before this rule (`scripts/`, and
> `channels/` missing `jarvis_app/`) both drifted at file granularity. The memory and data trees
> above stay file-granular because there their contents *are* the contract.

### Placement principle

`/app/jarvis_memory/` holds **only genuine memory** — markdown the agent both reads *and* mutates through memory tools. Everything else lives by who changes it:

- Changed only by **code/deploy** (prompt, rules) → `/app/jarvis_code/prompts/` (version-controlled, outside the memory sandbox).
- Changed only by **tools**, never `read_memory`'d (opaque DBs, append-only logs, code-managed JSON) → `/app/jarvis_data/<tool>/`.
- **Gateway artifacts** (Telegram blobs) → a channel-owned cache inside that channel's gateway dir; the channel hands core/agent an absolute path and they never name a channel.
- Sole exception: `threads.sqlite*` — LangGraph owns the path and can't relocate, so it stays in `jarvis_memory/`, deny-listed from the memory tools.

---

## Critical Constraints

**NEVER read `/app/secrets/.env`.** The user explicitly forbids this. If you need to know what secrets exist, read the code that uses them — the variable names are sufficient.

---

## System Prompt Architecture

Assembled per LLM call by `build_system_prompt(scope, active_skills)` in `agent.py`. All identity/rules are **files, read fresh per turn** (hot-reload via `load_or_blank`) — no system-prompt constant exists in code. Full architecture (placement principle, access-model table, assembly rationale): **[docs/architecture/MEMORY.md](docs/architecture/MEMORY.md)**.

Assembly order:

```
[Current time] / [Active scope] envelope
SOUL.md            (memory dir — user-curated identity; agent-writable w/ confirmation)
prompts/AGENTS.md  (code — operating rules; outside the memory sandbox, deploy-only)
USER.md            (memory dir — durable user profile; agent-writable, no confirm)
─ user scope ──────────  _USER_FRAMING + today's daily log (proactive sends reach the
                         thread as mirrored history via the pending-mirror drain, not the prompt)
─ heartbeat scope ─────  _HEARTBEAT_FRAMING + prompts/heartbeat.md + HEARTBEAT.md
                         + today's user chat + yesterday's daily log
compact_skill_list (registry — every skill's SKILL.md description; active skills' rule bodies too)
```

**Live cross-scope awareness.** Each scope's prompt now includes a live, log-derived view of what the other side did *today*: the user scope receives pending proactive sends as mirrored conversation history (the pending-mirror drain in `ask_jarvis`: undrained `notifications.jsonl` rows become one user-role block in the turn input, cursor-tracked in `jarvis_data/agent/mirror_cursor.json`); the heartbeat scope receives today's user-thread chat (every non-heartbeat thread in `chat_history.jsonl`). Both are read directly by `build_system_prompt` (no tool call), bounded by start-of-Israel-day and a per-entry length cap. This lets the heartbeat skip a task the user already addressed in chat, and lets the chat assistant reference a briefing the tick just sent as real history — a reply to it has its antecedent in the thread. The daily log is still injected (richer per-day narrative) but is no longer the **sole** awareness bridge.

`MEMORY.md` is **not** injected — the agent reads it on demand via the memory tools (AGENTS.md instructs it to consult the index). Per-tool schemas are **not** in the prompt — they come from `llm.bind_tools()` with the scoped tool set. A skill's rules (`SKILL.md` body) appear **only when that skill is active**. `AGENTS.md`/`heartbeat.md` change by code deploy only; `SOUL.md` writes trigger a Telegram confirmation (enforced in `write_memory`). `AGENTS.md` is physically outside the `_get_safe_path` sandbox, so memory tools cannot read or write it.

---

## Memory Tool Sandbox

`_get_safe_path(filename)` in `tools/core/memory.py` resolves all paths relative to `/app/jarvis_memory/` and rejects any `..` traversal. Jarvis cannot access files outside that directory via memory tools.

Protected files (cannot be deleted; `SOUL.md` additionally requires confirmation to write; `HEARTBEAT.md` rejects direct writes — `manage_heartbeat_task` is the only write path). Guards compare the canonical sandbox-relative name, so alias spellings (`./SOUL.md`) cannot bypass them:
- `SOUL.md`
- `HEARTBEAT.md`
- `MEMORY.md`
- `USER.md`

`prompts/AGENTS.md` needs no protection entry — it lives in `/app/jarvis_code/`, outside the sandbox, so memory tools physically cannot reach it.

---

## Heartbeat System

Full reference: **[docs/architecture/HEARTBEAT.md](docs/architecture/HEARTBEAT.md)** (tick pipeline, task grammar, gate semantics, authoring tool). The short version — `heartbeat.py` runs via APScheduler at the top of every hour (UTC; the phase is fixed, so a restart never re-phases the schedule), and **code decides when the model runs**:

1. **Pre-LLM gate** (`heartbeat_state.any_due`): a task is due iff its cadence has elapsed per code-owned `/app/jarvis_data/heartbeat/state.json` — measured raw or with both ends floored to the tick lattice, whichever comes due first — AND its optional `due:` time/day window (Israel time) is open. Nothing due → the tick returns without any model call. Gate errors fail open (model runs with the full task list).
2. **Due-only prompt**: the turn runs with `scope="heartbeat"` on the `heartbeat` thread; `build_system_prompt` injects only the due HEARTBEAT.md task blocks (non-due collapse to a one-line note) plus tick rules, today's user-thread chat (already-handled detection), and yesterday's daily log. The thread keeps a mixed history of recent ticks under the same 50-message cap — the noise turns dilute the in-context pattern deliberately
3. The agent works the due tasks (notes in `heartbeat/*.md`), ends the tick with a `heartbeat_respond(acted_tasks, notify, summary, notification_text, ...)` ack (the reply text is just a terse tick log). Delivery keys off the ack and goes through the gateway Outbox (`default_outbox().notify_owner(..., event="heartbeat")` — send + log-on-success); stamping runs **after** delivery settles — only acted tasks advance `state.json`, and a failed send skips stamping so the tasks re-run next tick
4. Writes a unified daily log: `daily/daily_YYYY-MM-DD.md` covering both heartbeat activity and today's user conversations (via `get_chat_history(since=...)`)

Task authoring goes through `manage_heartbeat_task` (validated before write, no confirmation; heartbeat turns may not `create`). The agent's notes files still carry a transitional `last_run:` line in parallel with `state.json` until the two have agreed in production.

The heartbeat and user agents share SOUL.md/AGENTS.md/USER.md and the same tool registry, but the prompt **differs by scope**: heartbeat gets the terse framing + `heartbeat.md` + today's chat; user gets conversational framing + today's daily log + today's heartbeat notifications. Awareness now flows both ways via live log injection (chat history into heartbeat, notifications into user); the daily log remains as a richer per-day narrative.

---

## Two LangGraph Threads

| Thread ID | Purpose |
|-----------|---------|
| `owner` | The one owner conversation — all channels stamp it (50-message window) |
| `heartbeat` | Scheduled background checks (separate window) |

Both threads write to the same `chat_history.jsonl` (tagged by `thread_id`). Cross-thread awareness flows through two paths: today's chat/notification slices are injected directly into each scope's system prompt by `build_system_prompt` (live, per-turn), and the daily log adds a richer per-day narrative (heartbeat-written, lagging).

---

## Confirmation Pattern

Used for irreversible actions (delete memory, write SOUL.md, delete media with files). A destructive tool calls `get_confirmation().request_confirmation_sync(...)` (from `gateway/factory.py` — channel-agnostic, never imports a concrete channel). Called from a sync worker thread, it returns a status string immediately; `action_fn` fires later only if the owner taps Confirm. `InMemoryConfirmationStore` (`gateway/confirmation/store.py`) owns bookkeeping + 5-minute TTL eviction (swept every 60s); the channel implements only `ConfirmationUI` (`gateway/channels/telegram/confirmation.py` — inline keyboard). The conversational acknowledgement is delivered via an `on_outcome` callback injected by `main.py`, so the gateway never imports the agent. See docs/architecture/GATEWAY.md (Plane 3).

---

## Deployment

Two instances on one host, each rooted by its systemd unit's `JARVIS_ROOT`. Full
runbook: **[docs/DEPLOY.md](docs/DEPLOY.md)**; rationale in
**[docs/plans/archive/STAGING_AND_DEPLOY.md](docs/plans/archive/STAGING_AND_DEPLOY.md)**.

- **Prod** — `/app/jarvis_code`, `jarvis.service`, `JARVIS_ROOT=/app`. **Deploy-only:**
  updated exclusively by `deploy/deploy.sh` (pull `origin/main` → snapshot → tag → smoke
  check → hand off), never edited in place.
- **Staging** — `/app/jarvis_staging/code`, `jarvis-staging.service` (on demand, not
  enabled), `JARVIS_ROOT=/app/jarvis_staging`. Where development and behavior testing
  happen; inert by default. **New sessions start here** (see DEVELOPMENT.md § Development
  Workflow).

**This Claude Code session runs on the same host.** Development edits go in the staging
tree; prod changes only through `deploy/deploy.sh` + a restart.

```bash
journalctl -u jarvis -f            # prod logs
journalctl -u jarvis-staging -f    # staging logs
```

Claude **cannot restart** either service (no permission) and does not run `deploy.sh`'s
restart. After a change, ask the user to restart, then verify: read the `Running code:`
provenance block (`scripts/jrestart.sh` prints it), check logs, and send a Telegram
message. Never infer or fake service state.

---

## Key Files to Know

| Task | File |
|------|------|
| Add a new tool | Add it under `tools/core/` (always-on) or `tools/<skill>/` (activatable skill); decorate `@tool_register(namespace=..., destructive=...)` above `@tool`. Nothing else — the registry auto-discovers it. New skill = new `tools/<name>/` dir + a `tools/<name>/SKILL.md` (YAML frontmatter `name`/`description` + optional rules body). **Sub-skill** = a `tools/<parent>/<child>/` subpackage (`__init__.py` importing its module + its own `SKILL.md`) with `namespace="<parent>/<child>"`; the parent's `__init__.py` imports the subpackage, and a parent may own zero tools (pure discovery index — children stay hidden until the parent is activated). |
| Add a new channel (email, etc.) | New dir `gateway/channels/<channel>/` implementing `Channel`; register in `gateway/factory.py`. No tool/agent edits. See docs/architecture/GATEWAY.md |
| Add a slash command | Add an `async def` handler in `gateway/commands/handlers.py` decorated with `@command(name, description)`. The router auto-discovers it; `/help` and each channel's command-menu (e.g. Telegram autocomplete via `register_command_menu()`) pick it up next start. Handlers receive `(InboundMessage, args: list[str])` and return reply text — they may import `agent`/`tools` but **not** any concrete channel. Build the reply with `gateway/commands/format.py` (`section`/`kv_section`/`document`/`join`), never hand-rolled markdown, and add a case to `scripts/ci/check_command_replies.py` (the guard fails on an uncovered command). See docs/architecture/GATEWAY.md (Plane 1 — Slash-Command Dispatch + Reply formatting). |
| Add an app surface | New module under `gateway/apps/` holding its `AppSpec` beside its handlers, plus one import line in `gateway/apps/specs.py`. `register_app(AppSpec(ns, name, entries=(AppEntry(id, method, params, handler), ...)))` — validated at import, so a typo fails at startup rather than as a silently empty Apps screen. A handler is `async (params: dict[str, str]) -> Any`; it may import `agent`/`tools` but **not** any concrete channel, and every blocking call goes through `asyncio.to_thread` (one event loop serves the poll, the turn, and the query). Failures a caller could have caused raise `AppNotFound`/`AppInvalidRequest` — that closed code vocabulary is what a channel maps to a status; anything else propagates and is reported as an internal fault. A channel with somewhere to show apps publishes `list_apps()` in its own wire shape. See docs/architecture/GATEWAY.md (App Surfaces). |
| Change Jarvis's personality | Edit `/app/jarvis_memory/SOUL.md` directly |
| Change behavioral rules | `/app/jarvis_code/prompts/AGENTS.md` (always-on) or `prompts/heartbeat.md` (heartbeat-scope only); a skill's own rules go in `tools/<ns>/SKILL.md`. Tool usage is driven by tool docstrings, not prompt prose. |
| Add a heartbeat task | Ask Jarvis (it uses `manage_heartbeat_task`, validated before write), or hand-edit `/app/jarvis_memory/HEARTBEAT.md` following the grammar in docs/architecture/HEARTBEAT.md (a malformed hand edit degrades to always-due, never a silent drop) |
| Understand the memory layout | `/app/jarvis_memory/MEMORY.md` |
| Full architecture reference | `docs/architecture/{GATEWAY,MEMORY,RUNTIME,HEARTBEAT,OBSERVABILITY}.md` |
| Add per-turn telemetry / read usage | `observability/{telemetry,usage}.py` + `scripts/trace.py` (see [docs/architecture/OBSERVABILITY.md](docs/architecture/OBSERVABILITY.md)) |
| Deploy / ops / local testing | `DEVELOPMENT.md` |
