"""Per-turn LLM usage telemetry.

Writes two append-only JSONL streams in /app/jarvis_data/logs/:
- TURNS_LOG (turns.jsonl): one record per agent turn (user OR heartbeat).
- TOOL_CALLS_LOG (tool_calls.jsonl): one record per tool invocation.

Both join by `turn_id`. The accumulator lives in a ContextVar so concurrent
user and heartbeat turns (each running in its own asyncio.to_thread context)
do not collide. The agent never reads these streams — they are queried by
tools/core/usage.py and scripts/trace.py only.

Prerequisite for measuring cost-reduction levers (#33): without per-turn
input/output/cache_read token counts we cannot evaluate prompt caching,
heartbeat gating, or compaction. See docs/architecture/OBSERVABILITY.md
(Stage C) for the full schema.
"""
import contextvars
import os
from datetime import datetime, timezone
from typing import Any

from tools.core.history import _LOG_DIR, _append_line

# Paths live with the writer that owns the schema. history.py owns the shared
# log dir + the _append_line / trim_log primitives; the names are ours.
TURNS_LOG = os.path.join(_LOG_DIR, "turns.jsonl")
TOOL_CALLS_LOG = os.path.join(_LOG_DIR, "tool_calls.jsonl")

TURN_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "turn_id", default=None
)
TURN_ACC: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "turn_acc", default=None
)

_TRACEBACK_MAX = 3000  # chars; truncated before write as defence-in-depth.


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def record_turn_start(
    thread_id: str,
    scope: str,
    active_skills_start: list[str] | None = None,
    model: str | None = None,
) -> dict:
    """Create the per-turn accumulator and bind it to TURN_ACC.

    Returns the accumulator dict; callers normally do not need to hold it
    (the recorders below mutate via TURN_ACC). `model` may be omitted —
    record_llm_call() captures it lazily from the first response.
    """
    started_at = _now_utc()
    acc = {
        "ts": started_at.isoformat(),
        "turn_id": TURN_ID.get(),
        "thread_id": thread_id,
        "scope": scope,
        "started_at": started_at.isoformat(),
        "ended_at": None,
        "duration_ms": None,
        "llm_calls": 0,
        "tool_calls": 0,
        "input_tokens": 0,
        "cache_read_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "model": model,
        "active_skills_start": sorted(active_skills_start or []),
        "active_skills_end": [],
        "no_action": False,
        "error": None,
    }
    TURN_ACC.set(acc)
    return acc


def record_llm_call(response: Any) -> None:
    """Increment llm_calls and accumulate tokens from response.usage_metadata.

    Gemini's usage_metadata shape (via langchain-google-genai):
        {"input_tokens": ..., "output_tokens": ..., "total_tokens": ...,
         "input_token_details": {"cache_read": ...}}

    `cache_read` is the count of input tokens served from the prompt cache —
    the dollar-saving metric for Lever 4 of #33. Treat usage_metadata and
    its sub-dicts as None-safe; providers vary.
    """
    acc = TURN_ACC.get()
    if acc is None:
        return
    acc["llm_calls"] += 1
    usage = getattr(response, "usage_metadata", None) or {}
    if usage:
        acc["input_tokens"] += int(usage.get("input_tokens") or 0)
        acc["output_tokens"] += int(usage.get("output_tokens") or 0)
        acc["total_tokens"] += int(usage.get("total_tokens") or 0)
        details = usage.get("input_token_details") or {}
        acc["cache_read_tokens"] += int(details.get("cache_read") or 0)
    if acc.get("model") is None:
        md = getattr(response, "response_metadata", None) or {}
        model = md.get("model_name") or md.get("model")
        if model:
            acc["model"] = model


def record_tool_call(
    tool_name: str,
    namespace: str,
    destructive: bool,
    duration_ms: int,
    status: str,
    args_size: int,
    error_str: str | None = None,
    traceback_str: str | None = None,
) -> None:
    """Append one tool-call record to tool_calls.jsonl and bump the turn-level
    counter. Tracebacks are truncated to _TRACEBACK_MAX chars so the file-only
    payload stays bounded."""
    if traceback_str and len(traceback_str) > _TRACEBACK_MAX:
        traceback_str = traceback_str[:_TRACEBACK_MAX] + "\n...<truncated>"
    record = {
        "ts": _now_utc().isoformat(),
        "turn_id": TURN_ID.get(),
        "tool": tool_name,
        "namespace": namespace,
        "destructive": bool(destructive),
        "duration_ms": int(duration_ms),
        "status": status,
        "args_size": int(args_size),
        "error": error_str,
        "traceback": traceback_str,
    }
    _append_line(TOOL_CALLS_LOG, record)
    acc = TURN_ACC.get()
    if acc is not None:
        acc["tool_calls"] += 1


def record_turn_end(
    active_skills_end: list[str] | None = None,
    no_action: bool = False,
) -> None:
    """Finalize the current turn: stamp ended_at + duration, append one line
    to turns.jsonl, clear TURN_ACC.

    `error` is sourced from acc["error"] — callers set it when an exception
    is caught (`_llm_node`, `ask_jarvis` try/finally) before re-raising.
    Idempotent: a second call with TURN_ACC already cleared is a no-op.
    """
    acc = TURN_ACC.get()
    if acc is None:
        return
    ended_at = _now_utc()
    started_at_dt = datetime.fromisoformat(acc["started_at"])
    acc["ended_at"] = ended_at.isoformat()
    acc["duration_ms"] = int((ended_at - started_at_dt).total_seconds() * 1000)
    if active_skills_end is not None:
        acc["active_skills_end"] = sorted(active_skills_end)
    acc["no_action"] = bool(no_action)
    _append_line(TURNS_LOG, acc)
    TURN_ACC.set(None)
