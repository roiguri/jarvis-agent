# Observability

## Purpose

This layer answers one question: **how much did each agent turn cost, and what did it do?**

It is the durable record of agent activity: every turn (user or heartbeat) produces a structured row capturing tokens, durations, tool calls, and outcome; every tool invocation produces its own row. Both are append-only JSONL with bounded retention, and queryable from a slash command (headline numbers) or an operator script (per-turn timelines).

The observability layer is responsible for:

- **Recording** every agent turn: input/output/cache-read tokens, LLM and tool call counts, active skills, duration, errors.
- **Recording** every tool invocation: name, namespace, destructive flag, duration, status, full traceback on error.
- **Surfacing** that data in two shapes — a `/usage` slash command for headline numbers, and `scripts/trace.py` for per-turn timelines.
- **Bounding** disk growth via the same 90-day retention that the rest of `/app/jarvis_data/logs/` already uses.

It is **not** responsible for:

- Eval / pytest scaffolding — separate concern.
- Emitting metrics to external systems (LangSmith, OTel, Prometheus, Phoenix) — single-user single-host deployment doesn't warrant the dependency.
- Behavior change — telemetry observes the agent loop, it never alters it.

---

## Where It Lives

```
/app/jarvis_code/
├── observability/                # this layer (sibling to gateway/, tools/)
│   ├── __init__.py               # re-exports both write side and read side
│   ├── telemetry.py              # write: ContextVars + record_* recorders
│   └── usage.py                  # read: load_turns + summarize_usage + format
├── scripts/
│   └── trace.py                  # per-turn timeline (operator tool)
└── gateway/commands/handlers.py  # /usage slash command — thin wrapper

/app/jarvis_data/logs/
├── turns.jsonl                   # one record per agent turn
├── tool_calls.jsonl              # one record per tool invocation
├── chat_history.jsonl            # Jarvis-readable audit log (owned by tools/core/history.py)
└── notifications.jsonl           # Jarvis-readable audit log (owned by tools/core/history.py)
```

---

## The Four JSONL Streams

All four live in `/app/jarvis_data/logs/`. All four use the shared `_append_line` (with `_APPEND_LOCK`) and `trim_log` (90-day cutoff) in `tools/core/history.py`.

### `turns.jsonl` — one record per agent turn

```json
{
  "ts": "2026-05-21T08:01:22.103Z",
  "turn_id": "f8e2…",
  "thread_id": "telegram_12345",
  "scope": "user" | "heartbeat",
  "started_at": "...", "ended_at": "...", "duration_ms": 4123,
  "llm_calls": 2, "tool_calls": 3,
  "input_tokens": 4501,
  "cache_read_tokens": 0,
  "output_tokens": 312,
  "total_tokens": 4813,
  "model": "gemini-3-flash-preview",
  "active_skills_start": [],
  "active_skills_end": ["media/radarr"],
  "no_action": false,
  "error": null
}
```

Source: built up across a turn by `observability.telemetry.record_turn_start` (at entry) / `record_llm_call` (per LLM invocation) and flushed by `record_turn_end` (at exit).

`cache_read_tokens` is the count of input tokens served from the provider's prompt cache. It is the signal for evaluating prompt-cache effectiveness — `cache_read_tokens / input_tokens` is the cache hit rate. Reads 0 when caching is not enabled or no cached prefix matched. Sourced from `response.usage_metadata.input_token_details.cache_read` for the langchain-google-genai backend; the field is `None`-safe at every level for providers that don't expose it.

`no_action` is `true` iff `scope == "heartbeat"` and the final response begins with `[NO_ACTION]`. Detected from the response text in `ask_jarvis`'s `finally`.

### `tool_calls.jsonl` — one record per tool invocation

```json
{
  "ts": "...", "turn_id": "f8e2…",
  "tool": "search_sonarr", "namespace": "media/sonarr",
  "destructive": false,
  "duration_ms": 412,
  "status": "ok" | "error" | "not_active",
  "args_size": 87,
  "error": null,
  "traceback": null
}
```

Source: `observability.telemetry.record_tool_call` from inside `_tool_node` in `agent.py`. Written immediately (independent of when the turn ends), so even a crashing turn preserves its tool history.

**Args are deliberately not stored** — they routinely contain Roi's personal data. `args_size` (length of the JSON-encoded args) is enough to debug "this tool was hammered with huge args".

**Tracebacks are file-only.** The `ToolMessage` returned to the LLM remains the short `f"Error: {e}"` string the agent has always seen. Tracebacks are truncated to 3 KB before write as defence-in-depth alongside the lock.

### `chat_history.jsonl` and `notifications.jsonl`

Append-only audit logs owned by `tools/core/history.py`, not this layer. Both are *Jarvis-readable*: the agent reads them back via `get_chat_history` / `get_notification_history`, and they are injected into prompts as live-slice context (see [MEMORY.md](MEMORY.md) "Per-scope content"). They deliberately do not carry `turn_id` — adding one would change a schema the model already reads.

`scripts/trace.py` correlates these to a turn by `(thread_id, ts within [started_at - 2s, ended_at + 2s])` instead.

---

## The `turn_id` Contract

A `uuid4().hex` minted at the **top of every turn** and propagated through every record written during that turn.

- **Where it's minted.** `ask_jarvis` (the single entry point used by both user and heartbeat). If a caller passes one in via the `turn_id` kwarg, that wins; otherwise `ask_jarvis` generates its own.
- **How it's propagated.** Through a `contextvars.ContextVar` named `TURN_ID`. **Not** through `JarvisState` — `turn_id` is per-invocation by design and should not survive a checkpoint write.
- **Why `ContextVar`.** Each `asyncio.to_thread(ask_jarvis, ...)` call gets its own copy of the parent context, so a heartbeat tick and a user turn running concurrently never see each other's id. LangGraph's sync `.invoke()` / `.stream()` inherits whatever context `ask_jarvis` set.
- **Join semantics.** `turns.jsonl ↔ tool_calls.jsonl` join by `turn_id` exactly. `chat_history.jsonl` / `notifications.jsonl` join by time window because they don't carry the id (by design — see above).

---

## The Two Query Surfaces

### `/usage` — Telegram slash command

Lives in `gateway/commands/handlers.py`. Thin wrapper around `observability.summarize_usage` + `format_usage_table`.

```
/usage                  → today, per-scope rollup
/usage today            → same
/usage yesterday        → yesterday, per-scope rollup
/usage week             → last 7 calendar days, per-day-per-scope
/usage week user        → last 7 days, user scope only
/usage week heartbeat   → last 7 days, heartbeat scope only
/usage 21.5             → specific day (D.M, current year)
/usage 21.5.2026        → specific day, full ISO
```

Trailing `user` or `heartbeat` always narrows the rollup; combine freely with any date token. The handler reuses `_parse_log_date` from `/logs` for date parsing.

Rendering is a compact summary (totals line + per-bucket bullets when ≥ 2 buckets) — readable on mobile, no horizontal scroll, no fixed-width tables.

### `observability` Python module — REPL / scripts

Same functions the slash command calls. Pure (no implicit `now()` defaults):

```python
from datetime import datetime, timedelta, timezone
from observability import summarize_usage, format_usage_table, israel_last_n_days

since, until = israel_last_n_days(7)
rows = summarize_usage(since=since, until=until, group_by="day+scope")
print(format_usage_table(rows, title="Usage — last 7 days"))
```

Use cases: ad-hoc analysis from a REPL, one-off analysis scripts, Jupyter cells. `load_turns(since, until)` returns the raw records if you want to compute something the rollup doesn't expose.

### `scripts/trace.py` — per-turn timeline (operator)

```
venv/bin/python3 scripts/trace.py                # last 5 turns
venv/bin/python3 scripts/trace.py --last 10
venv/bin/python3 scripts/trace.py --turn 1e42c3  # prefix-match a turn_id
```

Joins all four JSONLs (turn_id where available, time-window elsewhere) into an ms-offset timeline. Used for diagnosing "what did Jarvis actually do at X?" — slow turns, errored tools, heartbeat behavior verification.

LLM call durations are *inferred* from gaps around tool calls. Exact when each LLM call produces 0–1 tool calls (the common case). Approximate when the model fires parallel tools in one response (multiple tool rows from one LLM call), since per-LLM timing is not recorded today.

---

## Pricing

`observability.MODEL_PRICES` maps model names → `{input_per_m, cache_read_per_m, output_per_m}` (USD per million tokens). `estimate_usd()` subtracts `cache_read_tokens` from the billable-input bucket before applying rates — providers that expose a cache-hit discount bill those tokens separately at the discounted rate.

The table must be kept in lockstep with the provider's published rates for whatever model the agent is configured with. A model name missing from `MODEL_PRICES` falls back to zero rates, which silently zeros out the USD column in rollups — so a sudden drop to `$0.0000` in `/usage` is a signal that the model was changed without updating the table, not that costs vanished.

---

## Retention

`turns.jsonl` and `tool_calls.jsonl` are in the startup `trim_log` loop in `main.py`. They share the 90-day `LOG_RETENTION_DAYS` cutoff (`tools/core/history.py`) with `chat_history.jsonl` and `notifications.jsonl`.

Records never go through in-place edits — they age out whole-line via `trim_log` (timestamp filter, temp-file + rename). Audit invariant maintained.

For trend visibility past 90 days, a daily rollup file (`usage_daily.jsonl`: one record per `(date, scope)` with totals, written by a small heartbeat task) is the natural extension. `summarize_usage` is shaped so that merging in older summarized data is purely additive when that file exists.

---

## Concurrency

Three sources of concurrent writes share the four log files in the same process:

1. The user's `ask_jarvis(...)` call (Telegram inbound, on an `asyncio.to_thread`).
2. The heartbeat's `ask_jarvis(...)` call (hourly APScheduler tick, also `to_thread`).
3. The gateway webhook notifier appending to `notifications.jsonl`.

`_append_line` (`tools/core/history.py`) takes a process-wide `threading.Lock` (`_APPEND_LOCK`) around every write. Append-mode writes are atomic only up to `PIPE_BUF` (~4 KB) on Linux; tool tracebacks can easily exceed that, so the lock is required, not aspirational.

This mirrors the pattern `tools/core/memory.py` uses for its `_WRITE_LOCK`. If heartbeat ever splits into a separate process, both locks must upgrade to `fcntl.flock`.

---

## Schema Evolution

Single-writer schemas. New fields:

- **Default `None` / `0`** on old records (every reader uses `r.get(...)`).
- **Add to the record builder** in `observability.telemetry.record_turn_start` / `record_turn_end` / `record_tool_call`. Old records gain the new field as `None` when re-read.
- **Document here.** Update this file's schema sections at the same commit.

There is no schema version field today. If/when one becomes necessary (a breaking field rename or semantic change), prefer a new column (`v2_<name>`) over editing an existing one — readers can fall back gracefully and the old data stays parseable.

---

## Relationship to Other Architecture Docs

- [MEMORY.md](MEMORY.md) owns the `chat_history.jsonl` and `notifications.jsonl` schemas (live-slice prompt injection) — this layer reads them for `scripts/trace.py` but does not modify them.
- [RUNTIME.md](RUNTIME.md) owns the agent loop and tool registry. This layer hooks into the chokepoints (`_llm_node`, `_tool_node`, `ask_jarvis`) but does not change the loop's behavior.
- [GATEWAY.md](GATEWAY.md) owns the Telegram channel. `/usage` is a gateway-layer slash command that delegates to this layer; no other gateway concerns apply.
