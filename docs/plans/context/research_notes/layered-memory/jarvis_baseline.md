# Jarvis baseline — the current context/memory system, mapped onto a layered model

Scope: internal codebase research on the staging tree `/app/jarvis_staging/code` (HEAD `8c6cd3f`, same code as prod tag `deploy-2026-10-05-2` plus doc-only merges) and read-only measurements of prod state (`/app/jarvis_memory/`, `/app/jarvis_data/logs/`) taken 2026-10-06. Sources are given as repo paths with line numbers rather than URLs. No private memory content is reproduced, only sizes and structure. Token figures marked "≈" use the rough bytes/4 rule and are estimates. Figures without that mark are read directly from `turns.jsonl`.

Target layers used throughout: **L1 working context**, **L2 episodic store**, **L3 core memory**, **L4 consolidation**, **L5 background work**.

---

## Q1. How the prompt is assembled per call (L1), and how big each part is

### Takeaway
The system prompt is rebuilt from files on **every LLM call** (`_llm_node` → `build_system_prompt`). Since PR #132 its only time-varying line is the date, and the per-minute clock is prefixed once to the turn's HumanMessage (`_turn_stamp`). The system prompt itself is small: about 2.5k tokens in user scope and about 3–7k in heartbeat scope. Per-call input is about 34k tokens (user) and about 20k (heartbeat), so the bulk of each call is thread history, tool results and bound tool schemas, not the system prompt.

### Cited Findings
- **Assembly order** (`build_system_prompt(scope, active_skills, due_tasks)`): envelope `[Current date]`, optional `[Owner timezone]` (away mode), `[Active scope]`, and `[Channel]` (user scope only), then SOUL.md, prompts/AGENTS.md and USER.md. Scope-specific parts come next. The registry's `compact_skill_list` goes last. Parts are joined with blank lines and empty parts are dropped. — [agent.py:386-453](/app/jarvis_staging/code/agent.py)
- **User scope** appends `_USER_FRAMING` (a single sentence) and today's daily log (`--- Today's log (daily_<date>.md) ---`). — [agent.py:445-449](/app/jarvis_staging/code/agent.py), [agent.py:272-275](/app/jarvis_staging/code/agent.py), [agent.py:319-323](/app/jarvis_staging/code/agent.py)
- **Heartbeat scope** appends the following:
  - `_HEARTBEAT_FRAMING`
  - `prompts/heartbeat.md`
  - HEARTBEAT.md filtered to the due task blocks (`heartbeat_state.filter_heartbeat_md`)
  - today's user chat (`_load_recent_user_chat`: last 60 rows since Israel midnight, each capped at 240 chars, heartbeat thread and `*_test` threads excluded)
  - yesterday's daily log

  — [agent.py:431-444](/app/jarvis_staging/code/agent.py), [agent.py:334-383](/app/jarvis_staging/code/agent.py), [agent.py:308-316](/app/jarvis_staging/code/agent.py)
- **Rebuilt per call, not per turn.** `_llm_node` calls `build_system_prompt` on every model invocation. It also re-binds the tool set every time (`llm.bind_tools(registry.get_tools(scope, active))`), which is what lets a skill activated mid-turn take effect on the next call. — [agent.py:509-545](/app/jarvis_staging/code/agent.py)
- **`_turn_stamp`.** `ask_jarvis` computes `[Weekday, YYYY-MM-DD HH:MM Israel time]` once per turn and prefixes it to the user input text. In `/tz` away mode it also adds `| owner local: …`. The envelope keeps only the date, so the system prompt is byte-stable through an Israel day. — [agent.py:296-305](/app/jarvis_staging/code/agent.py), [agent.py:696](/app/jarvis_staging/code/agent.py), [agent.py:407-412](/app/jarvis_staging/code/agent.py), [docs/plans/archive/TIME_GROUNDING_PLAN.md §2](/app/jarvis_staging/code/docs/plans/archive/TIME_GROUNDING_PLAN.md)
- **Hot reload.** Every file goes through `load_or_blank`, which returns `""` on `OSError`. A missing file degrades the prompt and never crashes a turn. — [agent.py:282-289](/app/jarvis_staging/code/agent.py), [docs/architecture/MEMORY.md:136-138](/app/jarvis_staging/code/docs/architecture/MEMORY.md)
- **Budget notices are request-only.** When the turn budget is wrapping up or exhausted, a trailing HumanMessage notice is appended to the request but not to the checkpoint ("'stop now' text left in history would be imitated by later turns"). — [agent.py:525-538](/app/jarvis_staging/code/agent.py)
- **Turn budgets:** user turns get 300s, 30 LLM calls and 1.5M input tokens. Heartbeat turns get 120s, 15 calls and 400k input tokens. — [turn_budget.py:65](/app/jarvis_staging/code/turn_budget.py), [turn_budget.py:79](/app/jarvis_staging/code/turn_budget.py)
- **Model:** `gemini-3-flash-preview`, temperature 0.2, 60s per-call timeout. — [agent.py:250-255](/app/jarvis_staging/code/agent.py)
- **Live part sizes**, measured as prod bytes on 2026-10-06:

  | Part | Scope | Bytes |
  |---|---|---|
  | SOUL.md | both | 1,682 |
  | prompts/AGENTS.md | both | 3,881 |
  | USER.md | both | 1,359 |
  | prompts/heartbeat.md | heartbeat only | 3,571 |
  | HEARTBEAT.md, full (8 tasks; filtered to due blocks) | heartbeat only | 5,373 |
  | Daily logs, last 30 days, mean | user (today) / heartbeat (yesterday) | 1,690 |
  | Daily logs, max | user (today) / heartbeat (yesterday) | 3,872 |

  — measured via `wc -c` on `/app/jarvis_memory/*` and `/app/jarvis_staging/code/prompts/*`
- **Estimated system-prompt totals:**
  - User scope: ≈ 1.7k + 3.9k + 1.4k + 1.7k (daily log) + skill block ≈ **9–10 KB, about 2.5k tokens**.
  - Heartbeat scope: adds heartbeat.md (3.6 KB), the filtered HEARTBEAT.md (≤5.4 KB) and the chat slice (up to 60×~250 B ≈ 15 KB on a heavy chat day) ≈ **12–30 KB, about 3–7.5k tokens**.
  - Earlier reading for comparison: the 08-06 inventory put the system prompt at "~4.5k of a ~42.8k call". — [PROBLEMS.md E9 (L267-270)](/app/jarvis_staging/code/docs/plans/context/PROBLEMS.md)
- **Tool schemas sent every call** (golden snapshots, bytes of JSON):

  | Schema set | Bytes | When bound |
  |---|---|---|
  | core | 16,064 | always |
  | fitness | 19,674 | when the skill is active |
  | travel | 19,821 | when the skill is active |
  | github | 5,373 | when the skill is active |
  | google_health | 2,925 | when the skill is active |
  | web | 1,735 | when the skill is active |
  | media/* | ~17.4 KB | when the skill is active |

  The heartbeat thread in prod currently carries `active_skills = [fitness, google_health]`, so every heartbeat call binds about 38.6 KB of schemas (≈ 9.6k tokens) before any history. — [tests/golden/tools/](/app/jarvis_staging/code/tests/golden/tools); latest prod `turns.jsonl` row (`active_skills_start: ["fitness","google_health"]`)
- **Per-call input, measured from prod `turns.jsonl`:**

  | Window | Scope | Turns/day | Input/turn | Input/call | LLM calls/turn | Tool calls/turn | Cache-read share | Max turn |
  |---|---|---|---|---|---|---|---|---|
  | 2026-10-03..05 (3 Israel days) | user | 10.7 | 91.8k | 33.8k | 2.72 | 2.12 | 59% | 439,072 |
  | 2026-10-03..05 | heartbeat | 11.0 | 78.5k | 21.1k | 3.73 | 5.97 | 59% | 159,703 |
  | 2026-09-29..10-05 (7 days) | user | 6.3 | 97.0k | 35.3k | — | — | 56% | — |
  | 2026-09-29..10-05 | heartbeat | 11.1 | 69.1k | 19.9k | — | — | 53% | — |

  In the 3-day window, 29 of 33 heartbeat turns had `no_action=true`. In the 7-day window, 72 of 78 did. — computed from `/app/jarvis_data/logs/turns.jsonl` (2,355 parseable rows, 1 malformed row skipped)
- **PROBLEMS.md's own 10-03 reading, for comparison:** user 110.3k input/turn; heartbeat 62.1k input/turn and 3.29 LLM calls/turn; 49% cache-read share. — [PROBLEMS.md §0 (L41-52)](/app/jarvis_staging/code/docs/plans/context/PROBLEMS.md)
- **Problems touched:**
  - A4/A5: the full history and in-turn tool results are re-sent on every call. A4 measured the heartbeat checkpoint at ~63% of each call's input; A4+A5 together put ~81% of a call's input as already-seen material. — [PROBLEMS.md L82-88](/app/jarvis_staging/code/docs/plans/context/PROBLEMS.md)
  - A10: the heartbeat carries SOUL/USER/yesterday's log (`ASSERTED`). — [PROBLEMS.md L112-115](/app/jarvis_staging/code/docs/plans/context/PROBLEMS.md)
  - B1 is `RESOLVED`, and B2 is `ASSERTED`, with its after-deploy cache reading not yet taken. — [PROBLEMS.md L121-137](/app/jarvis_staging/code/docs/plans/context/PROBLEMS.md)
  - C1: injected files are uncapped. — [PROBLEMS.md L164-166](/app/jarvis_staging/code/docs/plans/context/PROBLEMS.md)

### Inferences
- The prompt is assembled per call but stays stable within a day, so the remaining cache-relevant churn comes from history growth inside a turn (each tool round appends) and from the turn-start trim, not from the system prompt.
- Tool schemas are probably a larger fixed slice per call than the whole system prompt: core alone is about 4k tokens versus about 2.5k for the user system prompt. Skills stay active indefinitely (no decay; [RUNTIME.md:130](/app/jarvis_staging/code/docs/architecture/RUNTIME.md)), so the heartbeat pays fitness+google_health schemas on every call, including no-action ticks.
- The cache-read share has risen from 49% (09-30..10-02, PROBLEMS §0) to 59% (10-03..05). PR #132 is in the prod tag `deploy-2026-10-05-2`, but its exact deploy time relative to this window was not established, so the rise cannot be attributed to it. The TIME_GROUNDING §4 before/after reading is still formally untaken.

### Gaps
- No exact live token count of the assembled prompt was taken. Importing `agent.py` loads the secrets `.env` via `load_dotenv`, so it was not run. A golden-test-style render against a copy of prod memory would give exact figures.
- The live `compact_skill_list` size was not measured. The golden snapshot uses fixtures: its skill block is about 669 B in `tests/golden/prompt_user.md:48-57`.
- Per-call cache attribution is not possible from telemetry. — PROBLEMS.md E7

---

## Q2. How the thread window works: MAX_MESSAGES, the reducer and trimmed content (L1)

### Takeaway
Each thread keeps one LangGraph checkpoint holding a message list. A custom reducer trims it to whole turns that fit in 50 messages, and the trim runs only when a write carries a HumanMessage (turn start). The previous turn is always kept. Whatever is trimmed is simply discarded. The user/assistant text survives in `chat_history.jsonl`, but tool calls and tool results, all heartbeat-turn content, and mirrored blocks have no durable copy beyond source logs. There is no compaction or summarisation.

### Cited Findings
- **`MAX_MESSAGES = 50`.** `_add_and_trim` does the following:
  - strips media blobs from existing messages
  - returns `combined` untouched unless the new write contains a HumanMessage
  - otherwise cuts at the earliest turn start (a HumanMessage not preceded by another HumanMessage, so a mirror block and its message count as one turn) for which `len - i <= 50`
  - never cuts past the previous turn's start

  — [agent.py:45](/app/jarvis_staging/code/agent.py), [agent.py:137-162](/app/jarvis_staging/code/agent.py)
- **Storage bound.** Stored state is "the cap plus up to two turns' traffic", because a turn's own traffic is never trimmed mid-turn. — [agent.py:42-44](/app/jarvis_staging/code/agent.py), [RUNTIME.md:125](/app/jarvis_staging/code/docs/architecture/RUNTIME.md)
- **Why turn-boundary trimming.** It fixed B3. The old per-write slice evicted a turn's own HumanMessage at about 40 tool calls, causing `400 INVALID_ARGUMENT` (7 prod incidents 2026-09-09..09-20). Staging later completed a 101-tool-call turn. — [PROBLEMS.md L139-149](/app/jarvis_staging/code/docs/plans/context/PROBLEMS.md), [TURN_LIFECYCLE_PLAN.md slice 1 (L253)](/app/jarvis_staging/code/docs/plans/archive/TURN_LIFECYCLE_PLAN.md)
- **One checkpoint row per thread.** `PruningSqliteSaver.put` deletes all older checkpoints and writes for the thread after each put, so there is no checkpoint history to recover trimmed messages from. — [agent.py:203-218](/app/jarvis_staging/code/agent.py)
- **Live checkpoint sizes** (prod `threads.sqlite`, `length(checkpoint)`):

  | Thread | Checkpoint bytes |
  |---|---|
  | `owner` | 100,946 |
  | `heartbeat` | 69,910 |
  | `jarvis-app_roi` (stale pre-spine thread) | 87,594 |
  | `telegram_508317402` (stale pre-spine thread) | 48,106 |
  | `local_dev_test_01` | 23,218 |

  On disk the DB is 101.7 MB with a 68.0 MB WAL, which is the disk-footprint issue #12 and is deliberately out of scope for A4. — sqlite read of `/app/jarvis_memory/threads.sqlite`; [PROBLEMS.md L351-353](/app/jarvis_staging/code/docs/plans/context/PROBLEMS.md)
- **What the model is told.** AGENTS.md says "Your short-term memory is a sliding window of the last 50 messages (~25 exchanges). Anything older is no longer in your context." It points to the daily log, the chat-history tool ("filter by start time") and the notification-history tool. — [prompts/AGENTS.md:17-22](/app/jarvis_staging/code/prompts/AGENTS.md)
- **What survives trimming:**
  - `chat_history.jsonl` receives only the raw user text (before the stamp) and the final assistant reply text, both from `main.py`. Tool calls and results are never logged there. — [main.py:56-66](/app/jarvis_staging/code/main.py), [main.py:103-105](/app/jarvis_staging/code/main.py)
  - The heartbeat thread is **absent** from prod `chat_history.jsonl`. Thread counts there are owner 1,727, telegram_508317402 358 and jarvis-app_roi 328, with no `heartbeat` rows. — measured
  - Heartbeat turns survive only as delivered notifications in `notifications.jsonl`, plus whatever notes and daily-log prose the model wrote.
- **Compaction is deferred.** "Compact-don't-trim + flush-before-forget (B3)" is listed as Deferred and as "the risky one". The plan's stated reason, "our trim is continuous", is now outdated because the trim is turn-boundary since TURN_LIFECYCLE slice 1. — [CONTEXT_PLAN.md L252-258](/app/jarvis_staging/code/docs/plans/archive/CONTEXT_PLAN.md)
- **Media stripping.** Base64 images become `[image attached]` and audio/video/PDF blobs are dropped (the text hint stays) once a message is in existing history. — [agent.py:101-134](/app/jarvis_staging/code/agent.py)
- **Failed turns.** A failed turn writes a failure-note AIMessage, and the HumanMessage too if it was unsaved, into the thread so the next turn knows what happened. — [agent.py:978-1001](/app/jarvis_staging/code/agent.py)
- **Problems touched:**
  - A4: re-sent history. — [PROBLEMS.md L82-84](/app/jarvis_staging/code/docs/plans/context/PROBLEMS.md)
  - B3: `RESOLVED`.
  - C3: history beyond the window is reachable only by guessing a time range. — [PROBLEMS.md L172-174](/app/jarvis_staging/code/docs/plans/context/PROBLEMS.md)
  - F2: a polluted window re-seeds itself. — [PROBLEMS.md L286-289](/app/jarvis_staging/code/docs/plans/context/PROBLEMS.md)
  - #81: unrecoverable truncation, mapped as a complement of C1/C3. — [PROBLEMS.md L342-343](/app/jarvis_staging/code/docs/plans/context/PROBLEMS.md)

### Inferences
- The window is count-based (messages), not token-based. One tool-heavy turn can hold most of the 50 slots and carry large tool results, which is consistent with a 439k-input user turn in the 3-day window.
- Trimmed tool outputs are irrecoverable. Any "flush before forget" layer would be the first durable home for them.
- Several docs still describe per-channel threads (`telegram_<id>`), and the stale checkpoints remain in the DB:
  - [RUNTIME.md:100](/app/jarvis_staging/code/docs/architecture/RUNTIME.md)
  - [RUNTIME.md:308](/app/jarvis_staging/code/docs/architecture/RUNTIME.md)
  - [MEMORY.md:145](/app/jarvis_staging/code/docs/architecture/MEMORY.md) ("filtered to `thread_id` starting with `telegram_`"; the code actually excludes heartbeat and `*_test` threads, agent.py:361)

  A redesign should treat the code as ground truth.

### Gaps
- No message-count or byte breakdown of the current `owner`/`heartbeat` checkpoint (human vs AI vs tool messages) was taken. The blob is msgpack, and decoding it was skipped to keep the work read-only and simple.

---

## Q3. Core memory: files, sizes, how they are written, guards, and MEMORY.md usage (L3)

### Takeaway
Core memory is markdown in `/app/jarvis_memory/` (about 10 KB of protected files plus topic files). SOUL.md, AGENTS.md (code) and USER.md are injected whole on every call in both scopes. MEMORY.md is a tool-read index that is **not** injected. The agent writes freely through `write_memory`, using an atomic temp+replace under a lock. SOUL.md writes need owner confirmation, HEARTBEAT.md writes go only through `manage_heartbeat_task`, and four files are delete-protected. There is no size cap anywhere.

### Cited Findings
- **Protected files and sizes** (prod, 2026-10-06):

  | File | Bytes | Injected? |
  |---|---|---|
  | SOUL.md | 1,682 | yes, both scopes |
  | USER.md | 1,359 | yes, both scopes |
  | MEMORY.md | 1,886 | no |
  | HEARTBEAT.md | 5,373 | heartbeat scope, due blocks only |

  Total 10,300 B. — `wc -c /app/jarvis_memory/*.md`
- **Other memory:**
  - top-level topic files: `active_projects.md` (959 B), `people_and_connections.md` (575 B)
  - subdirectories: `fitness/` (5 files, 15.1 KB), `trips/` (2 files, 15.2 KB), `projects/` (1 file, 5.8 KB), `life/` (1 file, 4.3 KB), `system/` (empty)
  - `heartbeat/` notes: 9 files, 79–668 B each, about 3.2 KB total
  - `daily/`: 150 files, 612 KB on disk, median 1,795 B, max 3,872 B, earliest `daily_2026-05-08.md`

  — `ls`/`du` on `/app/jarvis_memory`
- **The sandbox** (`_get_safe_path`):
  - resolves against `MEMORY_DIR` and rejects `..`, absolute escapes and sibling-prefix paths
  - resolves symlinks before the containment check (#73 fix)
  - deny-lists `threads.sqlite*`

  `_PROTECTED_FILES = {"SOUL.md","HEARTBEAT.md","MEMORY.md","USER.md"}`. — [tools/core/memory.py:30](/app/jarvis_staging/code/tools/core/memory.py), [tools/core/memory.py:40-66](/app/jarvis_staging/code/tools/core/memory.py), [MEMORY.md:65-87](/app/jarvis_staging/code/docs/architecture/MEMORY.md)
- **Core memory tools:** `write_memory`, `read_memory`, `list_memory` and `delete_memory` (destructive, confirmed, blocked for protected files). — [tools/core/memory.py:154-317](/app/jarvis_staging/code/tools/core/memory.py)
- **SOUL.md write path.** `write_memory("SOUL.md")` routes through `get_confirmation().request_confirmation_sync(...)` with a diff preview and returns a status immediately; the write happens only on Confirm. Raw writes to HEARTBEAT.md are rejected. — [tools/core/memory.py:156-198](/app/jarvis_staging/code/tools/core/memory.py)
- **Concurrency.** `_exec_write_memory` takes a process-wide `_WRITE_LOCK`, writes a same-directory temp file and publishes it with `os.replace`. — [tools/core/memory.py:200](/app/jarvis_staging/code/tools/core/memory.py), [MEMORY.md:166-174](/app/jarvis_staging/code/docs/architecture/MEMORY.md)
- **MEMORY.md is not injected**, as a deliberate token trade: "Injecting it every turn was considered and rejected". AGENTS.md tells the agent to consult it and to update it whenever a memory file is created, changed significantly or deleted. — [MEMORY.md:99](/app/jarvis_staging/code/docs/architecture/MEMORY.md), [MEMORY.md:105](/app/jarvis_staging/code/docs/architecture/MEMORY.md), [MEMORY.md:161](/app/jarvis_staging/code/docs/architecture/MEMORY.md), [prompts/AGENTS.md:19](/app/jarvis_staging/code/prompts/AGENTS.md)
- **USER.md** is agent-writable without confirmation, and AGENTS.md says to keep it accurate. — [prompts/AGENTS.md:21](/app/jarvis_staging/code/prompts/AGENTS.md)
- **Placement principle.** `jarvis_memory/` holds only markdown the agent both reads and writes. Code-owned or tool-opaque state lives in `jarvis_data/`, and deploy-only prompts live in `prompts/`. — [MEMORY.md:15-34](/app/jarvis_staging/code/docs/architecture/MEMORY.md)
- **Problems touched:**
  - C1: uncapped injection. — [PROBLEMS.md L164-166](/app/jarvis_staging/code/docs/plans/context/PROBLEMS.md)
  - C2: recall requires knowing the filename. — [PROBLEMS.md L168-170](/app/jarvis_staging/code/docs/plans/context/PROBLEMS.md)
  - C4: nothing pushes back on growth. — [PROBLEMS.md L176-181](/app/jarvis_staging/code/docs/plans/context/PROBLEMS.md)
  - D1: notes files drift. — [PROBLEMS.md L215-219](/app/jarvis_staging/code/docs/plans/context/PROBLEMS.md)

### Inferences
- C4 as written in PROBLEMS.md says "USER.md and MEMORY.md are injected into every prompt in both scopes". That is **wrong for MEMORY.md** per the code (agent.py:424-429) and MEMORY.md:105. Only USER.md and SOUL.md are always-injected memory files, which weakens C4's premise for MEMORY.md. The redesign doc should correct this.
- The always-injected core memory is small today (SOUL+USER ≈ 3 KB ≈ 750 tokens). C1/C4 are growth risks rather than current cost drivers. The daily log is the largest always-injected memory artifact, about 1.7 KB/day on average.
- Retrieval is filename-only (`read_memory(filename)`). No search tool exists over memory files.

### Gaps
- Growth history of USER.md and SOUL.md over time was not measured. Git does not track `/app/jarvis_memory`, and no size telemetry exists.

---

## Q4. Episodic store: logs, transcripts, and how they are searchable (L2)

### Takeaway
Three append-only JSONL logs with 90-day retention, plus 150 daily-log markdown files. The agent can read only two of the logs: `chat_history.jsonl` via `get_chat_history(limit, since)` and `notifications.jsonl` via `get_notification_history(limit)`. Neither tool supports keyword search, and their outputs truncate each entry to 200 or 120 chars. Telemetry logs are app-only.

### Cited Findings
- **Logs** (prod, 2026-10-06):

  | Log | Rows | Size | Notes |
  |---|---|---|---|
  | `chat_history.jsonl` | 2,413 | 1.39 MB | fields `ts, thread_id, role, content`; first row 2026-07-09 |
  | `notifications.jsonl` | 402 | 116 KB | |
  | `turns.jsonl` | 2,356 | 1.48 MB | |
  | `tool_calls.jsonl` | 12,604 | 3.11 MB | |

  `LOG_RETENTION_DAYS = 90` (`trim_log`). — `wc` on `/app/jarvis_data/logs/`; [tools/core/history.py:20-31](/app/jarvis_staging/code/tools/core/history.py)
- **`get_chat_history(limit=20, since=None)`:**
  - With no `since`, it tails the last N rows.
  - With `since` (a date, meaning the Israel day start, or ISO with an offset), it scans the whole file and keeps rows at or after that time, then the last `limit`.
  - Content is truncated to 200 chars.
  - There is no thread filter or keyword filter.
  - Docstring: "Use when you need history from earlier days — today's chat is already in context."

  — [tools/core/history.py:162-225](/app/jarvis_staging/code/tools/core/history.py)
- **`get_notification_history(limit=20)`:** tail only, messages truncated to 120 chars, no time or keyword filter. — [tools/core/history.py:143-160](/app/jarvis_staging/code/tools/core/history.py)
- **Writers:**
  - `chat_history.jsonl` is written by `main.py` for user turns only (user text, and the reply if non-empty), and for slash commands. — [main.py:56-66](/app/jarvis_staging/code/main.py), [main.py:103-105](/app/jarvis_staging/code/main.py)
  - `notifications.jsonl` is written by the Outbox on delivery success. — [pending_mirrors.py:3-4](/app/jarvis_staging/code/pending_mirrors.py)
- **App-only telemetry.** `turns.jsonl`/`tool_calls.jsonl` are never agent-read, and tool args are deliberately not stored. — [OBSERVABILITY.md:118](/app/jarvis_staging/code/docs/architecture/OBSERVABILITY.md), [CLAUDE.md layout](/app/jarvis_staging/code/CLAUDE.md)
- **Daily logs** are a narrative episodic layer reachable by filename (`read_memory("daily/daily_YYYY-MM-DD.md")`). AGENTS.md directs the agent there for "earlier today or yesterday". — [prompts/AGENTS.md:22](/app/jarvis_staging/code/prompts/AGENTS.md)
- **Problems touched:**
  - C2 and C3 (`ASSERTED`) — [PROBLEMS.md L168-174](/app/jarvis_staging/code/docs/plans/context/PROBLEMS.md)
  - #81, unrecoverable truncation — [PROBLEMS.md L342-343](/app/jarvis_staging/code/docs/plans/context/PROBLEMS.md)
  - Phase 3 "history recall" candidates on record: transcript FTS (hermes) and hybrid search plus trigger phrases (OpenClaw). — [CONTEXT_PLAN.md L245-246](/app/jarvis_staging/code/docs/plans/archive/CONTEXT_PLAN.md)

### Inferences
- The episodic store is small (about 1.4 MB of chat over 3 months), so full-text indexing such as SQLite FTS5 is cheap in storage terms. The lack of a search path is a tooling gap, not a data-volume problem.
- Heartbeat transcripts, tool results and silent heartbeat actions (#106) are missing from every agent-readable episodic source except whatever prose the model chose to write into notes or daily logs.
- The `since` path reads the whole file on every call, which is O(file) but trivial at the current size.

### Gaps
- No measurement exists of how often the agent actually calls `get_chat_history` or `get_notification_history` in user scope. `tool_calls.jsonl` holds this but was not tallied, beyond the earlier "heartbeat get_chat_history 0.03/tick" recorded in project memory, which is not in the repo docs.

---

## Q5. Consolidation: daily logs, audit tasks, and anything that rewrites memory (L4)

### Takeaway
Consolidation is done entirely by LLM-driven heartbeat tasks, with no code-side summariser:

- `daily-log` (every 3h, 05:00–23:30) rewrites `daily/daily_<today>.md` from today's chat, notifications and heartbeat activity.
- `memory-index-audit` (every 7 days, 10:00–18:00) curates MEMORY.md.
- The model updates USER.md and topic files ad hoc during user turns, as AGENTS.md instructs.

### Cited Findings
- **Current prod HEARTBEAT.md tasks** (8 tasks; names and headers only):

  | Task | Cadence | `due:` window | Gate |
  |---|---|---|---|
  | morning-readiness-check | every 1d | 08:00-10:00 | — |
  | crossfit-sync-and-remind | every 1h | 05:00-22:00 | `arbox_registrations` |
  | weekly-fitness-scouting | every 1d | thu 19:00-21:00 | — |
  | weekly-attendance-sync | every 1d | sun 08:00-11:00 | — |
  | memory-index-audit | every 7 days | 10:00-18:00 | — |
  | reading-list-suggestion | every 7d | Fri 10:00±1h | — |
  | daily-log | every 3h | 05:00-23:30 | — |
  | step-challenge-tracker | every 4h | 08:00-23:00 | — |

  — grep of `/app/jarvis_memory/HEARTBEAT.md`
- **Daily log.** It is "written by an ordinary task, `daily-log` … every 3h … whose prose folds in today's chat, today's proactive sends (`get_notification_history`) and the day's heartbeat activity". It used to be a rule on every tick and was moved to a task once crossfit went behind a gate. "Chat after the last run of the day (23:00) is not captured." — [HEARTBEAT.md doc L237-245](/app/jarvis_staging/code/docs/architecture/HEARTBEAT.md)
- **Daily-log consumers.** The user scope injects today's log. The heartbeat scope injects yesterday's. — [agent.py:442-449](/app/jarvis_staging/code/agent.py)
- **The tick prompt names today's daily-log file** in its imperative. — [heartbeat.py:83-88](/app/jarvis_staging/code/heartbeat.py)
- **A7** (daily log rewritten on the hourly path) was measured when the rule ran every tick. The 09-30..10-02 acks still read "Updated daily log with today's conversations". — [PROBLEMS.md L95-96](/app/jarvis_staging/code/docs/plans/context/PROBLEMS.md), [PROBLEMS.md L107-110](/app/jarvis_staging/code/docs/plans/context/PROBLEMS.md)
- **Notes files.** Each heartbeat task has a notes file (`heartbeat/<task>.md`) that the agent reads first and updates after acting (heartbeat.md steps 1–3). Code never parses them. — [prompts/heartbeat.md:36-38](/app/jarvis_staging/code/prompts/heartbeat.md), [HEARTBEAT.md doc "Three files, three owners"](/app/jarvis_staging/code/docs/architecture/HEARTBEAT.md)
- **Proactive memory writes in user turns.** "Write to memory proactively whenever something important is established — do not wait to be asked"; USER.md is kept current; MEMORY.md is updated on file changes; "Files absent from the index are cleanup candidates." — [prompts/AGENTS.md:18-21](/app/jarvis_staging/code/prompts/AGENTS.md)
- **Phase 2 (memory write pressure)** was filed with candidates and no decision: "write-time caps (hermes) vs gated batch consolidation (OpenClaw) vs a mix". — [CONTEXT_PLAN.md L242-244](/app/jarvis_staging/code/docs/plans/archive/CONTEXT_PLAN.md)
- **Problems touched:**
  - A6: retrieving about 325 tokens of notes cost about 3 `read_memory` round-trips. — [PROBLEMS.md L90-93](/app/jarvis_staging/code/docs/plans/context/PROBLEMS.md)
  - A7, C1, C4, D1.
  - #106: a silent heartbeat action is invisible to the user scope. — [PROBLEMS.md L208-209](/app/jarvis_staging/code/docs/plans/context/PROBLEMS.md)

### Inferences
- Because daily logs are LLM-rewritten prose, they lose detail between 3-hour runs and after 23:00, and they cost a full heartbeat turn (about 20k tokens/call) each run.
- The only "memory pressure" mechanism is a weekly LLM audit of the index. Nothing audits or bounds USER.md or SOUL.md size.

### Gaps
- Per-run cost of the `daily-log` and `memory-index-audit` tasks individually is not measurable from telemetry. `turns.jsonl` does not record which due tasks ran; the due list appears only in journal logs.

---

## Q6. Background work: heartbeat, triggers/wakes and what context they carry (L5)

### Takeaway
An hourly APScheduler cron runs a code-side gate (cadence plus `due:` window, with code-owned `last_run` stamps in `triggers.json`), then optional code gates. Only then does it run an LLM turn on the persistent `heartbeat` thread, whose prompt shows only the due task blocks. The turn ends with a structured `heartbeat_respond` ack that drives delivery (Outbox) and stamping (after delivery). Scheduled wakes run on the same thread and lock with `due_tasks=[]` or `[task]`. Delivered sends reach the owner thread through the pending-mirror drain.

### Cited Findings
- **Gate.**
  - `run_heartbeat` calls `heartbeat_state.any_due(now)`. Nothing due means it returns without any model call or agent import.
  - A gate error fails open and runs every task except gated ones.
  - Due tasks with `gate:` run in code via `triggers.gates.evaluate` and are stamped on success.
  - If no non-gated task is due, no model call is made.

  — [heartbeat.py:19-74](/app/jarvis_staging/code/heartbeat.py), [HEARTBEAT.md doc L14-48](/app/jarvis_staging/code/docs/architecture/HEARTBEAT.md)
- **Gate semantics.** A task is due when it is not paused, its cadence has elapsed, and its window is open. Cadence is measured raw or with both ends floored to the tick lattice, whichever comes due first. — [heartbeat_state.py:306-383](/app/jarvis_staging/code/heartbeat_state.py), [CLAUDE.md Heartbeat §1](/app/jarvis_staging/code/CLAUDE.md)
- **Tick turn.**
  - Prompt: "Run the scheduled heartbeat check now. Work the due tasks … Today's daily log file: daily/daily_<today>.md."
  - Call: `ask_jarvis(..., HEARTBEAT_THREAD_ID, scope="heartbeat", heartbeat_due_tasks=due_names)`, run under `TURN_LOCK`.

  — [heartbeat.py:77-97](/app/jarvis_staging/code/heartbeat.py)
- **Ack, delivery and stamping.**
  - `_ack_from_messages` takes the last `heartbeat_respond` call after the final HumanMessage, so a stale ack from an earlier tick is never used. — [agent.py:1004-1037](/app/jarvis_staging/code/agent.py)
  - Acted tasks are filtered to the due list ("rogue" names are dropped).
  - Delivery goes through `default_outbox().notify_owner(..., event="heartbeat")`.
  - Stamping happens only after delivery succeeds; a failed send leaves tasks unstamped.
  - A tick that broke without an ack sends a code-built `tick_failed` notice.

  — [heartbeat.py:101-187](/app/jarvis_staging/code/heartbeat.py), [heartbeat.py:249-267](/app/jarvis_staging/code/heartbeat.py)
- **Wakes.** `run_wake(trigger)` runs on the heartbeat thread under `TURN_LOCK` with prompt "Scheduled wake [id], <source>. Work only this instruction…". It removes the trigger from the store before the turn (at-most-once), passes `heartbeat_due_tasks=[task] if task else []`, never stamps, and retries only a failed delivery, as a plain send. — [heartbeat.py:190-246](/app/jarvis_staging/code/heartbeat.py)
- **`manage_trigger`** (core) creates reminders and wakes, with at most 10 pending Jarvis-created wakes. — [tools/core/scheduling.py:27](/app/jarvis_staging/code/tools/core/scheduling.py), [tools/core/scheduling.py:54-114](/app/jarvis_staging/code/tools/core/scheduling.py)
- **`heartbeat_respond`** is registered with `scopes=("heartbeat",)`. `manage_heartbeat_task` is core, and heartbeat turns may not `create`. — [tools/core/heartbeat.py:94-130](/app/jarvis_staging/code/tools/core/heartbeat.py), [HEARTBEAT.md doc L212](/app/jarvis_staging/code/docs/architecture/HEARTBEAT.md)
- **Context a tick carries:**
  - the system prompt as in Q1 (SOUL, AGENTS, USER, heartbeat.md, due HEARTBEAT blocks, today's user chat up to 60×240 chars, yesterday's daily log)
  - the persistent heartbeat thread window (50 messages of prior ticks and wakes; checkpoint 69,910 B)
  - bound schemas for core plus any persisted active skills
  - notes files, read on demand via `read_memory`

  — [agent.py:431-444](/app/jarvis_staging/code/agent.py), [prompts/heartbeat.md:36](/app/jarvis_staging/code/prompts/heartbeat.md)
- **Pending-mirror drain (into the owner thread).**
  - On a user turn on `owner`, `drain_pending()` reads `notifications.jsonl` rows newer than the cursor and newer than 24h, skipping `heartbeat_outcome` rows.
  - Each row is prefixed (`[Heartbeat]`/`[Reminder]`/`[Notification]`) and capped at 2,000 chars, with at most 20 entries (oldest dropped).
  - The rows become one user-role block with the header `[Messages Jarvis sent you since the last turn:]`, inserted before the user message.
  - The cursor (`jarvis_data/agent/mirror_cursor.json`) advances only once the input has been checkpointed.

  — [pending_mirrors.py:1-97](/app/jarvis_staging/code/pending_mirrors.py), [agent.py:727-730](/app/jarvis_staging/code/agent.py), [agent.py:869-870](/app/jarvis_staging/code/agent.py), [agent.py:927-929](/app/jarvis_staging/code/agent.py)
- **Measured cost.**
  - Heartbeat: 11.0 turns/day, 78.5k input/turn, 3.73 LLM calls/turn and 5.97 tool calls/turn (10-03..05). 29 of 33 turns had `no_action`.
  - Latest prod tick (2026-10-06 17:00Z): 69,605 input, 40,307 cache-read, 3 calls, 8 tools, `no_action=true`.
  - Source: computed from `/app/jarvis_data/logs/turns.jsonl`
- **Problems touched:**
  - A1–A3: heartbeat spend, per-tick cost and round-trip ceremony. — [PROBLEMS.md L67-80](/app/jarvis_staging/code/docs/plans/context/PROBLEMS.md)
  - A8: the gate rarely closes because of task definitions. — [PROBLEMS.md L98-102](/app/jarvis_staging/code/docs/plans/context/PROBLEMS.md)
  - A9: the empty-list check, now moved behind `gate: arbox_registrations`. — [PROBLEMS.md L104-110](/app/jarvis_staging/code/docs/plans/context/PROBLEMS.md)
  - A10.
  - E6: a gated tick is indistinguishable from a dead service. — [PROBLEMS.md L249-251](/app/jarvis_staging/code/docs/plans/context/PROBLEMS.md)
  - E10: paused tasks distort readings. — [PROBLEMS.md L261-265](/app/jarvis_staging/code/docs/plans/context/PROBLEMS.md)
  - C5: `RESOLVED` by the drain; #106 remains (silent action is invisible). — [PROBLEMS.md L183-209](/app/jarvis_staging/code/docs/plans/context/PROBLEMS.md)

### Inferences
- About 88% of model-reaching ticks (29/33) still end with no notification. Each costs about 70–80k input across 3–4 calls. Most of that is the replayed heartbeat thread plus tool schemas, not the task content.
- Ticks and wakes share one thread, so a wake's instruction-specific traffic and the ticks' routine traffic co-mingle in one 50-message window.

### Gaps
- `turns.jsonl` does not log the due-task list per tick, so cost cannot be attributed per task.
- The number of gated-only or nothing-due skips per day is not in `turns.jsonl` (E6). It is visible only in journal logs, which were not read.

---

## Q7. Hard constraints and deliberate decisions a redesign must respect or explicitly reverse

### Takeaway
A redesign must respect a set of explicit, documented decisions: the single owner thread spine with mirrored delivered text, prompt files outside or inside the sandbox by mutator, MEMORY.md not injected, separate isolated heartbeat thread, code-owned scheduling state, turn-boundary trimming, request-only budget notices, docstring-as-contract, manual deploy, and golden-snapshot tests. Several carry recorded reasons that would need a stated counter-reason to reverse.

### Cited Findings

**Gemini implicit caching, as used here**

- No explicit cache API is used anywhere. The strategy is byte-stable prefix ordering. Since PR #132 the envelope changes only at Israel midnight. — [agent.py:407-412](/app/jarvis_staging/code/agent.py), [TIME_GROUNDING_PLAN.md §2](/app/jarvis_staging/code/docs/plans/archive/TIME_GROUNDING_PLAN.md)
- REFERENCE_ARCHITECTURES records two points:
  - hermes "emits no explicit cache markers for Gemini at all: their entire Gemini strategy is prefix ordering for implicit caching"
  - cross-tick caching is "still effectively true [unreachable] for Gemini implicit caching at hourly cadence"

  — [REFERENCE_ARCHITECTURES.md §5 (L139-163)](/app/jarvis_staging/code/docs/plans/context/REFERENCE_ARCHITECTURES.md)
- B2's mechanism (cache prefix spans system instruction, then tools, then history) is `ASSERTED`, not re-verified against Gemini docs. — [PROBLEMS.md L129-137](/app/jarvis_staging/code/docs/plans/context/PROBLEMS.md)
- Measured cache-read share: 49% (09-30..10-02), 59% (10-03..05). — PROBLEMS §0; turns.jsonl
- Hot-reloading prompt files is a feature, and "a restart is still recommended to flush the upstream prompt cache". — [MEMORY.md:138](/app/jarvis_staging/code/docs/architecture/MEMORY.md)

**LangGraph checkpointer and reducer**

- `SqliteSaver` subclass keeping 1 checkpoint per thread. — agent.py:203-218
- `threads.sqlite` must stay in `jarvis_memory/` because LangGraph owns the path; it is deny-listed. — [MEMORY.md:30](/app/jarvis_staging/code/docs/architecture/MEMORY.md), [MEMORY.md:162](/app/jarvis_staging/code/docs/architecture/MEMORY.md)
- `JarvisState` fields: `messages` (`_add_and_trim`), `scope`, `active_skills` (`_merge_skills`), `heartbeat_due_tasks`. New fields must be `NotRequired` because existing checkpoints predate them. — [agent.py:180-191](/app/jarvis_staging/code/agent.py)
- Gemini wire constraints shape the reducer:
  - a history must not start on an orphaned tool response (B3)
  - user/model alternation, which is why the mirror is ONE user-role block ("the reducer drops leading assistant-role messages, and one message keeps the Gemini wire alternating")

  — [pending_mirrors.py:5-7](/app/jarvis_staging/code/pending_mirrors.py), [agent.py:148-153](/app/jarvis_staging/code/agent.py)
- Hand-rolled `StateGraph` exists to re-bind tools per call for same-turn skill activation. — [RUNTIME.md:26](/app/jarvis_staging/code/docs/architecture/RUNTIME.md)

**Owner-thread spine and pending-mirror drain**

- "One owner conversation (the spine)… Owner-approved as a product change". — [CONTEXT_PLAN.md L34-39](/app/jarvis_staging/code/docs/plans/archive/CONTEXT_PLAN.md)
- "Mirror, don't inject… write the *delivered text* — only the delivered text, never the background run's transcript… Provenance goes in the message text (`[Heartbeat] …`)". — [CONTEXT_PLAN.md L40-45](/app/jarvis_staging/code/docs/plans/archive/CONTEXT_PLAN.md)
- "Fresh checkpoint, no key migration… thread resets are a known-safe operation… durable state lives outside the checkpoint". — [CONTEXT_PLAN.md L48-50](/app/jarvis_staging/code/docs/plans/archive/CONTEXT_PLAN.md)
- Turn origin is carried by `CURRENT_CHANNEL`, not the thread prefix. — [CONTEXT_PLAN.md L51-54](/app/jarvis_staging/code/docs/plans/archive/CONTEXT_PLAN.md), [agent.py:418-422](/app/jarvis_staging/code/agent.py)
- Drain invariants: 24h rolling window (not a calendar day, because of travel), the cursor advances only after the input is checkpointed (re-deliver, never lose), and the turn-start stamp is not applied to the mirror block. — [pending_mirrors.py:1-12](/app/jarvis_staging/code/pending_mirrors.py), [TIME_GROUNDING_PLAN.md §2 "Not stamped"](/app/jarvis_staging/code/docs/plans/archive/TIME_GROUNDING_PLAN.md)

**Separate heartbeat thread and its mixed history**

- "The thread keeps a mixed history of recent ticks under the same 50-message cap — the noise turns dilute the in-context pattern deliberately". — [CLAUDE.md Heartbeat §2](/app/jarvis_staging/code/CLAUDE.md)
- Separate threads exist so "heartbeat's terse, machine-flavored turns never pollute the conversational sliding window". — [RUNTIME.md:100](/app/jarvis_staging/code/docs/architecture/RUNTIME.md)
- F2: a polluted checkpoint re-seeds itself, and resetting the thread was the fix. — [RESEARCH.md §3.3](/app/jarvis_staging/code/docs/plans/context/RESEARCH.md)

**Code owns scheduling; the model owns judgement**

- "Code decides *when* the model runs; the model decides *what* to do." `last_run` is code-owned and the agent never writes it. — [HEARTBEAT.md doc L7-8](/app/jarvis_staging/code/docs/architecture/HEARTBEAT.md), [prompts/heartbeat.md:38](/app/jarvis_staging/code/prompts/heartbeat.md)
- Asymmetric fail directions: read side fails open, gated tasks retry then tell, write side fails loud. — [HEARTBEAT.md doc L114-120](/app/jarvis_staging/code/docs/architecture/HEARTBEAT.md)
- Four constraints on event-driven probes:
  - `fetch_upcoming_arbox_classes` is not a pure read
  - probe state must be code-owned
  - stamp after success
  - time-shaped versus diff-shaped wakes are different mechanisms

  — [RESEARCH.md §3.1 (L98-127)](/app/jarvis_staging/code/docs/plans/context/RESEARCH.md)

**Confirmation pattern for SOUL.md**

- Writes go through `get_confirmation().request_confirmation_sync`, which is channel-agnostic, has a 5-minute TTL, and returns its status immediately. AGENTS.md: "Never rewrite SOUL.md autonomously". — [tools/core/memory.py:156-198](/app/jarvis_staging/code/tools/core/memory.py), [prompts/AGENTS.md:20](/app/jarvis_staging/code/prompts/AGENTS.md), [CLAUDE.md "Confirmation Pattern"](/app/jarvis_staging/code/CLAUDE.md)
- Any automated consolidation that rewrites SOUL.md would therefore need the confirmation flow.

**Placement principle and memory tools**

- The sandbox enforces the placement principle structurally. AGENTS.md stays deploy-only in `prompts/`.
- MEMORY.md tool-read rather than injected is a "deliberate token trade, not an omission".
- No env-var path overrides (YAGNI).

— [MEMORY.md "Settled Deviations" L155-162](/app/jarvis_staging/code/docs/architecture/MEMORY.md)

**Behaviour drivers**

- The tool docstring is the behavioural contract, not prompt prose (F1). Negative instructions were deliberately not added. — [RESEARCH.md §3.3](/app/jarvis_staging/code/docs/plans/context/RESEARCH.md)
- Request-only notices exist so that "stop now" text is never imitated from history. — [agent.py:528-529](/app/jarvis_staging/code/agent.py)
- No `get_current_time` tool, because docstring-driven tool use is unreliable (F1). — [TIME_GROUNDING_PLAN.md §2](/app/jarvis_staging/code/docs/plans/archive/TIME_GROUNDING_PLAN.md)

**Single owner and deploy model**

- Jarvis is single-user with no multi-tenant routing. — [CLAUDE.md "What Jarvis Is"](/app/jarvis_staging/code/CLAUDE.md)
- Prod is deploy-only via `deploy/deploy.sh`, with the owner restarting manually; staging is where development happens. Continuous deploy was explicitly declined. — [CLAUDE.md "Deployment"](/app/jarvis_staging/code/CLAUDE.md), [RESEARCH.md §3.4 (L160-178)](/app/jarvis_staging/code/docs/plans/context/RESEARCH.md)
- Every phase is bracketed by before/after readings, and cost claims need numbers (E-cluster). — [CONTEXT_PLAN.md L55-57](/app/jarvis_staging/code/docs/plans/archive/CONTEXT_PLAN.md)

**Test suite and golden snapshots**

- `tests/` is a required CI gate. `tests/golden/` snapshots both scope prompts (`prompt_user.md` 5,047 B, `prompt_heartbeat.md` 9,177 B), the skill block and every tool schema. Any change to prompt files or docstrings needs `pytest --update-golden` and a reviewed diff. — [CLAUDE.md "Key Files"](/app/jarvis_staging/code/CLAUDE.md), [tests/conftest.py:29-43](/app/jarvis_staging/code/tests/conftest.py)
- Relevant suites: `test_prompt_snapshot.py`, `test_context_mirror.py`, `test_turn_lifecycle.py`, `test_heartbeat_state.py`, `test_time_boundaries.py`.
- An LLM-judge eval framework was declined, with one narrow judge recognised as needed: whether a lighter heartbeat prompt yields blander or wronger briefings. — [RESEARCH.md §3.4](/app/jarvis_staging/code/docs/plans/context/RESEARCH.md)

**Runtime limits**

- Per-scope turn budgets (Q1).
- Skills persist until deactivated, and activation decay is explicitly out of scope. — [RUNTIME.md:130](/app/jarvis_staging/code/docs/architecture/RUNTIME.md), [RUNTIME.md:333](/app/jarvis_staging/code/docs/architecture/RUNTIME.md)

### Inferences
- The decisions most likely to collide with a layered redesign are listed below. Each would need an explicit reason to reverse.
  1. "Mirror only delivered text, never the background transcript" conflicts with any proposal to give the user scope heartbeat working detail.
  2. "Mixed heartbeat history dilutes the pattern" and the persistent heartbeat thread conflict with a proposal to make ticks stateless or fresh-context. A stateless tick would remove F2-style self-seeding and most of A4 at once, but would also drop whatever in-context continuity ticks rely on; the docs do not record any such reliance.
  3. "MEMORY.md not injected" conflicts with an injected core-memory index.
  4. "Hot-reloaded files read per call" conflicts with a session-frozen prompt like hermes'. Today it costs nothing within a day, because the files rarely change mid-turn.
- The deferral of compaction rested partly on the "trim is continuous" premise ([CONTEXT_PLAN.md L254](/app/jarvis_staging/code/docs/plans/archive/CONTEXT_PLAN.md)). That premise became false with TURN_LIFECYCLE slice 1 (turn-boundary trim), so the main stated blocker to a flush-before-forget step at turn start no longer holds.

### Gaps
- No current Gemini documentation was consulted in this pass (internal-only scope). The precise implicit-cache rules (minimum prefix, TTL, whether tool declarations sit before system instructions in the cache key) remain unverified here, as B2 itself says.
- The doc inconsistencies found (MEMORY.md:145 `telegram_` filter, RUNTIME.md:100/308 per-channel threads, PROBLEMS.md C4 "MEMORY.md is injected", CLAUDE.md "both threads write to the same chat_history.jsonl" while prod has no heartbeat rows) are recorded, not resolved.
