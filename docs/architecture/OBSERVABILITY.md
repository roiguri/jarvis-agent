# Observability

## Purpose

This layer answers one question: **what did each agent turn do, what did it cost, and what did the model see?**

It is the durable record of agent activity, kept in one SQLite file: every turn (user, heartbeat, or a job outside a turn) gets a row with its tokens, duration, outcome and context; every model call gets a row with its tokens, latency and the make-up of its input; every tool call gets a row linked to the model call that issued it; the tool set bound in each turn and every distinct system prompt are kept too. It is queried from a slash command (headline numbers) and operator scripts (timelines, context readings).

The observability layer is responsible for:

- **Recording** every turn and job, every model call (including failed ones), every tool invocation, the bound tool set, and each distinct system prompt.
- **Surfacing** that data — `/usage` for headline numbers, `scripts/trace.py` for per-turn timelines, `scripts/context_report.py` for context readings.
- **Bounding** disk growth with a 180-day retention applied at startup.

It is **not** responsible for:

- Conversation content. Messages, tool arguments and tool results never land here; the system prompt is the one exception (see `prompts`).
- Eval / pytest scaffolding — separate concern.
- Emitting to external systems (LangSmith, OTel, Prometheus) — single-user single-host deployment; telemetry stays on the box.
- Behavior change — telemetry observes the agent loop, it never alters it.

---

## Where It Lives

```
/app/jarvis_code/
├── observability/                # this layer (sibling to gateway/, tools/)
│   ├── __init__.py               # re-exports the write and read sides
│   ├── store.py                  # the SQLite file: schema, connections, retention, backup
│   ├── telemetry.py              # write: ContextVars + record_* recorders + job()
│   └── usage.py                  # read: load_turns + summarize_usage + format
├── scripts/
│   ├── trace.py                  # per-turn timeline (operator tool)
│   ├── context_report.py         # context readings: per-scope cost, input composition, cost per due-task set
│   └── measure_context.py        # real token cost of core/skills + prompt-cache test (makes model calls)
└── gateway/commands/handlers.py  # /usage slash command — thin wrapper

/app/jarvis_data/observability/
└── telemetry.sqlite              # WAL mode; separate from any conversation-content store
```

---

## The Store

`jarvis_data/observability/telemetry.sqlite`. All tables join by `turn_id`; every row has `id` and `ts` (UTC ISO text, which range queries compare as strings).

| Table | One row per | Columns (beyond `id`, `ts`) |
|---|---|---|
| `turns` | Turn or job | `turn_id`, `thread_id`, `scope` (`user` \| `heartbeat` \| `job`), `job`, `channel`, `trigger_id`, `due_tasks` (JSON list), `started_at`, `ended_at`, `duration_ms`, `llm_calls`, `tool_calls`, token totals (`input_tokens`, `cache_read_tokens`, `output_tokens`, `reasoning_tokens`, `total_tokens`), `model`, `active_skills_start` / `_end` (JSON), `no_action`, `outcome`, `error`, `budget` (JSON) |
| `llm_calls` | Model call | `turn_id`, `call_index` (1-based), `started_at`, `latency_ms`, input / cache-read / output / reasoning tokens, `finish_reason`, `model`, `prompt_hash`, `prompt_chars`, `schema_chars`, `bound_tool_count`, `history_chars`, `turn_chars`, `history_messages`, `turn_messages`, `error` |
| `tool_calls` | Tool call | `turn_id`, `llm_call_index` (the call that issued it), `tool`, `namespace`, `destructive`, `duration_ms`, `status` (`ok` \| `error` \| `not_active`), `args_size`, `result_size`, `error`, `traceback` |
| `bound_tools` | Turn × tool | `turn_id`, `tool`, `namespace`, `schema_chars`, `first_call_index` |
| `prompts` | Distinct system prompt | `prompt_hash` (SHA-256), `scope`, `chars`, `text`, `first_seen`, `last_seen` |

### `turns`

Inserted when the turn starts and updated when it ends, so a turn the process never finished stays visible as an **open row** (`ended_at` null). `load_turns` leaves open rows out of rollups (they have no totals); `trace.py` shows them as OPEN.

- `channel` is the origin channel, `trigger_id` the trigger whose firing started a wake, `due_tasks` the heartbeat's due-task set — so heartbeat cost can be read per task set.
- `cache_read_tokens` is the input served from the provider's prompt cache; `cache_read_tokens / input_tokens` is the hit rate. From `usage_metadata.input_token_details.cache_read`, `None`-safe.
- `reasoning_tokens` is the thinking slice **of** `output_tokens` — a subset, billed as output, so it is a diagnostic and never enters `estimate_usd`. It is the only observable `thinking_level` moves.
- `llm_calls` counts every call, including a final one that raised.
- `no_action` is `true` iff `scope == "heartbeat"` and the tick sent the owner no message (`not ack.notify`; no ack counts as a no-op).
- `outcome` is how the turn ended, from `turn_budget.py`: `completed`, `wrapped_up`, `budget_exhausted` or `failed` (`error` carries the detail). A job is `completed` or `failed`.
- `budget` is the turn budget as it stood: the scope's `limits`, which limit ended it (`exhausted_by`: `time` / `steps` / `tokens`) and whether the wrap-up notice fired. Usage against each limit is the row's own `duration_ms`, `llm_calls`, `input_tokens`, so every row explains itself across limit changes. A `budget_exhausted` row is censored: it shows the limit, not what the turn wanted.

### `llm_calls` — composition

Each call records what its input was made of, in chars, split at the current turn's start (the same boundary the window trim uses, `agent._turn_starts`):

- `prompt_chars` — the system prompt (its text is in `prompts` under `prompt_hash`);
- `schema_chars` — the bound tool declarations (`bound_tool_count` of them);
- `history_chars` / `history_messages` — messages from earlier turns, replayed on every call;
- `turn_chars` / `turn_messages` — this turn's messages so far, which grow with each tool round.

Chars are shares; tokens are cost. Together they split a call's input into prompt, schemas, replayed history and the growing tool-result tail. Message chars count text and tool-call JSON; media blobs are left out. A call that raised is recorded with its `error`, latency and composition before the exception propagates.

### `tool_calls`

Written as each call returns (`ts` is the return time; start is `ts - duration_ms`), so a crashing turn keeps its tool history. **Arguments and results are not stored** — they routinely contain the owner's personal data; `args_size` / `result_size` are enough to debug "this tool was hammered" or "this result was huge". Tracebacks are truncated to 3 KB; the `ToolMessage` the model sees stays the short `Error: …` string.

### `bound_tools` and `prompts`

`bound_tools` has one row per tool bound at any point in a turn; `first_call_index` says when it joined (a skill activated mid-turn joins at the next call). `prompts` stores each distinct system prompt once; repeats only move `last_seen`.

### Writes

One short transaction per event, on a short-lived connection (user and heartbeat turns run on different threads). A failed write is logged and dropped, never raised into a turn — a locked file or a full disk must not fail a turn after its work has landed.

---

## The `turn_id` Contract

A `uuid4().hex` minted at the **top of every turn** and stamped on every row written during it.

- **Where it's minted.** `ask_jarvis` (the single entry point used by both user and heartbeat), or `telemetry.job()` for work outside a turn.
- **How it's propagated.** Through a `contextvars.ContextVar` named `TURN_ID`. **Not** through `JarvisState` — it is per-invocation and must not survive a checkpoint write.
- **Why `ContextVar`.** Each `asyncio.to_thread(ask_jarvis, ...)` gets its own copy of the parent context, so a heartbeat tick and a user turn running concurrently never see each other's id.
- **Join semantics.** Store tables join by `turn_id` exactly. `chat_history.jsonl` / `notifications.jsonl` carry no id (the model reads that schema), so `trace.py` matches them by thread and time window.

---

## Jobs

Model calls made outside a turn (a nightly job, a compaction) are recorded under `telemetry.job(name)`:

```python
with telemetry.job("compaction"):
    response = llm.invoke(...)
    telemetry.record_llm_call(response, latency_ms=...)
```

The job gets a `turns` row with `scope = "job"` and `job = name`; an exception marks it `failed` and propagates. A call recorded outside any turn or job is dropped.

---

## Query Surfaces

### `/usage` — slash command

Lives in `gateway/commands/handlers.py`. Thin wrapper around `observability.summarize_usage` + `format_usage_table`.

```
/usage                  → today, per-scope rollup
/usage yesterday        → yesterday, per-scope rollup
/usage week             → last 7 calendar days, per-day-per-scope
/usage week user        → last 7 days, user scope only
/usage 21.5             → specific day (D.M, current year; D.M.Y also works)
```

Jobs appear as their own `job` scope. Turn outcomes show alongside the counts: `N errors`, `N stopped early (2 steps, 1 time)`, `N wrapped up` — a budget stop is not counted as an error. Rendering follows the slash-command reply contract ([GATEWAY.md](GATEWAY.md) § Reply formatting).

### `observability` Python module — REPL / scripts

```python
from observability import summarize_usage, format_usage_table, israel_last_n_days, load_turns
from observability import store

since, until = israel_last_n_days(7)
print(format_usage_table(summarize_usage(since=since, until=until, group_by="day+scope")))
store.rows("SELECT tool, COUNT(*) n FROM tool_calls GROUP BY tool ORDER BY n DESC")
```

### `scripts/trace.py` — per-turn timeline

```
venv/bin/python3 scripts/trace.py                # last 5 turns
venv/bin/python3 scripts/trace.py --turn 1e42c3  # prefix-match a turn_id
```

Model calls (with measured latency, tokens and input composition) and tool calls (with the call that issued them) in one ms-offset timeline, plus chat and notification rows matched by time window. Turns imported from the old JSONL streams have no model-call rows and show tool calls only.

### `scripts/context_report.py` — context readings

Per-scope turn cost and cache ratio, average input composition per model call, heartbeat cost per due-task set, checkpoint weight per thread, and the assembled prompt's section sizes. Run before and after any context change.

### `scripts/measure_context.py` — real token costs

Makes real model calls (needs the API key; run by hand on staging, never in CI): the token cost of the system prompt, of the core tools and of each skill, and a prompt-cache test (same request ×3, then one skill added ×3, run twice) showing whether a tool-set change costs the cache. Saves JSON next to the store.

---

## Pricing

`observability.MODEL_PRICES` maps model names → `{input_per_m, cache_read_per_m, output_per_m}` (USD per million tokens). `estimate_usd()` subtracts `cache_read_tokens` from the billable-input bucket before applying rates.

A model missing from `MODEL_PRICES` falls back to zero rates; rollups flag it with `⚠ unpriced`, so a sudden `$0.00` reads as a stale table, not free usage.

---

## Retention and Backups

Every table keeps 180 days (`store.RETENTION_DAYS`); `store.trim()` runs at startup (`main.py`). `prompts` age out by `last_seen`. Rows never change after a turn ends.

`deploy/backup_state.sh` copies the store with SQLite's online backup (consistent while the service runs) instead of tarring the live file. `python -m observability.store --backup DEST` does the same by hand.

### Transition from JSONL

Telemetry was previously written to `logs/turns.jsonl` and `logs/tool_calls.jsonl`. Those files are no longer written. At startup, `store.import_jsonl` copies their last 180 days into the store once (idempotent; recorded in the `meta` table), and the startup trim keeps aging the files out. That code is temporary and marked `TODO(#150)`.

**`TODO(#N)` convention.** Code that exists only until a follow-up lands carries `TODO(#N)`, naming the issue that tracks its removal. The `todo-issues` workflow (`.github/workflows/todo-issues.yml`) reopens issue N if it is closed while any marker remains on `main`, so the issue can only stay closed once the code is gone.

---

## Schema Evolution

Single writer (`observability/telemetry.py`). New columns:

- Add to `_SCHEMA` in `store.py` **and** an `ALTER TABLE … ADD COLUMN` for existing files (`CREATE TABLE IF NOT EXISTS` does not add columns to a table that already exists).
- Readers treat a missing value as `None` / `0`.
- Document here in the same commit.

Prefer a new column over changing an existing one's meaning, so old rows stay readable.

---

## Relationship to Other Architecture Docs

- [MEMORY.md](MEMORY.md) owns the `chat_history.jsonl` and `notifications.jsonl` schemas — this layer reads them for `trace.py` but does not modify them.
- [RUNTIME.md](RUNTIME.md) owns the agent loop and tool registry. This layer hooks into the chokepoints (`_llm_node`, `_tool_node`, `ask_jarvis`) but does not change the loop's behavior.
- [GATEWAY.md](GATEWAY.md) owns the channels. `/usage` is a gateway-layer slash command that delegates to this layer.
