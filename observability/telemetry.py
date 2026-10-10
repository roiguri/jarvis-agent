"""Per-turn LLM telemetry, written to the telemetry store (observability/store.py).

One row per turn (or job) in ``turns``, one per model call in ``llm_calls``,
one per tool call in ``tool_calls``, the tool set bound in each turn in
``bound_tools``, and each distinct system prompt once in ``prompts``. All join
by ``turn_id``. The agent never reads the store.

The turn accumulator lives in a ContextVar so concurrent user and heartbeat
turns (each running in its own asyncio.to_thread context) do not collide. The
turn row is inserted at the start and updated at the end, so a turn the process
never finished stays visible as an open row.

Every write is guarded: a failed write is logged and dropped, never raised into
a turn. See docs/architecture/OBSERVABILITY.md for the schema.
"""
import contextvars
import hashlib
import logging
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from observability import store
from tools.core.history import _LOG_DIR

# The JSONL streams this store replaced: no longer written, kept only so the
# startup trim ages them out and the one-time import can read them.
# TODO(#150): remove with the import.
TURNS_LOG = os.path.join(_LOG_DIR, "turns.jsonl")
TOOL_CALLS_LOG = os.path.join(_LOG_DIR, "tool_calls.jsonl")

TURN_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "turn_id", default=None
)
TURN_ACC: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "turn_acc", default=None
)

logger = logging.getLogger(__name__)

_TRACEBACK_MAX = 3000  # chars; truncated before write.

_TURN_FIELDS = (
    "ts", "turn_id", "thread_id", "scope", "job", "channel", "trigger_id", "due_tasks",
    "started_at", "ended_at", "duration_ms", "llm_calls", "tool_calls", "input_tokens",
    "cache_read_tokens", "output_tokens", "reasoning_tokens", "total_tokens", "model",
    "active_skills_start", "active_skills_end", "no_action", "outcome", "error", "budget",
)
_JSON_FIELDS = ("due_tasks", "active_skills_start", "active_skills_end", "budget")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _guarded(what: str, fn, *args) -> None:
    """Run a store write; never raise. Telemetry observes the loop — a full
    disk or a locked file must not fail a tool step or a turn."""
    try:
        fn(*args)
    except Exception:
        logger.exception("telemetry write failed: %s", what)


def _turn_values(acc: dict) -> dict:
    values = {k: acc.get(k) for k in _TURN_FIELDS}
    for k in _JSON_FIELDS:
        values[k] = store.dumps(values[k])
    values["no_action"] = int(bool(values["no_action"]))
    return values


def _insert_turn(acc: dict) -> None:
    values = _turn_values(acc)
    with store.write() as con:
        con.execute(
            f"INSERT OR REPLACE INTO turns ({', '.join(values)}) "
            f"VALUES ({', '.join('?' for _ in values)})",
            tuple(values.values()),
        )


def _update_turn(acc: dict) -> None:
    values = _turn_values(acc)
    turn_id = values.pop("turn_id")
    with store.write() as con:
        cur = con.execute(
            f"UPDATE turns SET {', '.join(f'{k} = ?' for k in values)} WHERE turn_id = ?",
            (*values.values(), turn_id),
        )
        if cur.rowcount == 0:  # the start insert was lost; write the whole row now
            values["turn_id"] = turn_id
            con.execute(
                f"INSERT INTO turns ({', '.join(values)}) "
                f"VALUES ({', '.join('?' for _ in values)})",
                tuple(values.values()),
            )


def record_turn_start(
    thread_id: str | None,
    scope: str,
    active_skills_start: list[str] | None = None,
    model: str | None = None,
    channel: str | None = None,
    trigger_id: str | None = None,
    due_tasks: list[str] | None = None,
    job: str | None = None,
) -> dict:
    """Create the per-turn accumulator, bind it to TURN_ACC, and insert the
    open turn row.

    `model` may be omitted — record_llm_call() captures it lazily from the
    first response. `due_tasks` is the heartbeat's due-task set, `trigger_id`
    the trigger whose firing started a wake, `job` the name of a non-turn
    job (scope "job").
    """
    started_at = _now_utc()
    acc = {
        "ts": started_at.isoformat(),
        "turn_id": TURN_ID.get(),
        "thread_id": thread_id,
        "scope": scope,
        "job": job,
        "channel": channel,
        "trigger_id": trigger_id,
        "due_tasks": list(due_tasks) if due_tasks is not None else None,
        "started_at": started_at.isoformat(),
        "ended_at": None,
        "duration_ms": None,
        "llm_calls": 0,
        "tool_calls": 0,
        "input_tokens": 0,
        "cache_read_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
        "model": model,
        "active_skills_start": sorted(active_skills_start or []),
        "active_skills_end": [],
        "no_action": False,
        "error": None,
        # completed | wrapped_up | budget_exhausted | failed (turn_budget); the
        # caller sets it before record_turn_end.
        "outcome": None,
        # The turn's limits, which one ended it, and whether the wrap-up notice
        # fired — so each row explains itself across limit changes. Set by the
        # caller before record_turn_end.
        "budget": None,
    }
    TURN_ACC.set(acc)
    _guarded("turn start", _insert_turn, dict(acc))
    return acc


def _usage(response: Any) -> dict[str, int]:
    """Token counts from a response's usage_metadata, None-safe at every level.

    Gemini's shape (via langchain-google-genai):
        {"input_tokens", "output_tokens", "total_tokens",
         "input_token_details": {"cache_read"}, "output_token_details": {"reasoning"}}

    `cache_read` is the input served from the prompt cache (billed at a
    discount). `reasoning` is the thinking slice of output_tokens — already
    inside it and billed as output, so it is a diagnostic, not a cost term.
    """
    usage = getattr(response, "usage_metadata", None) or {}
    details = usage.get("input_token_details") or {}
    out_details = usage.get("output_token_details") or {}
    return {
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
        "cache_read_tokens": int(details.get("cache_read") or 0),
        "reasoning_tokens": int(out_details.get("reasoning") or 0),
    }


def _model_of(response: Any) -> str | None:
    md = getattr(response, "response_metadata", None) or {}
    return md.get("model_name") or md.get("model")


def _write_llm_call(call: dict, prompt: dict | None, bound: list[dict]) -> None:
    with store.write() as con:
        con.execute(
            f"INSERT INTO llm_calls ({', '.join(call)}) VALUES ({', '.join('?' for _ in call)})",
            tuple(call.values()),
        )
        if prompt:
            con.execute(
                "INSERT INTO prompts (prompt_hash, scope, chars, text, first_seen, last_seen) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(prompt_hash) DO UPDATE SET last_seen = excluded.last_seen",
                (prompt["hash"], prompt["scope"], len(prompt["text"]), prompt["text"],
                 call["ts"], call["ts"]),
            )
        con.executemany(
            "INSERT OR IGNORE INTO bound_tools "
            "(ts, turn_id, tool, namespace, schema_chars, first_call_index) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(call["ts"], call["turn_id"], b["tool"], b["namespace"], b["schema_chars"],
              call["call_index"]) for b in bound],
        )


def record_llm_call(
    response: Any = None,
    *,
    started_at: datetime | None = None,
    latency_ms: int | None = None,
    finish_reason: str | None = None,
    prompt: str | None = None,
    scope: str | None = None,
    bound: list[dict] | None = None,
    history_chars: int | None = None,
    turn_chars: int | None = None,
    history_messages: int | None = None,
    turn_messages: int | None = None,
    error: str | None = None,
    telemetry_error: str | None = None,
) -> None:
    """Record one model call: roll its tokens into the turn and write its row.

    ``response`` is None when the call raised (``error`` carries why). The
    composition arguments are chars, split at the current turn's start:
    ``prompt`` (the system prompt text, stored once by hash), ``bound`` (one
    ``{"tool", "namespace", "schema_chars"}`` per bound tool), history from
    earlier turns, and this turn's messages so far. ``telemetry_error`` says
    why the composition is missing when measuring it failed. A call outside
    any turn or job is not recorded.
    """
    acc = TURN_ACC.get()
    if acc is None:
        return
    acc["llm_calls"] += 1
    tokens = _usage(response) if response is not None else _usage(None)
    for k, v in tokens.items():
        acc[k] += v
    model = _model_of(response) if response is not None else None
    if acc.get("model") is None and model:
        acc["model"] = model
    prompt_rec = None
    if prompt is not None:
        prompt_rec = {
            "hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "scope": scope or acc.get("scope"),
            "text": prompt,
        }
    bound = bound or []
    call = {
        "ts": _now_utc().isoformat(),
        "turn_id": acc["turn_id"],
        "call_index": acc["llm_calls"],
        "started_at": started_at.isoformat() if started_at else None,
        "latency_ms": latency_ms,
        **tokens,
        "finish_reason": finish_reason,
        "model": model or acc.get("model"),
        "prompt_hash": prompt_rec["hash"] if prompt_rec else None,
        "prompt_chars": len(prompt) if prompt is not None else None,
        "schema_chars": sum(b["schema_chars"] for b in bound) if bound else None,
        "bound_tool_count": len(bound),
        "history_chars": history_chars,
        "turn_chars": turn_chars,
        "history_messages": history_messages,
        "turn_messages": turn_messages,
        "error": error,
        "telemetry_error": telemetry_error,
    }
    call.pop("total_tokens")
    _guarded("llm call", _write_llm_call, call, prompt_rec, bound)


def record_tool_call(
    tool_name: str,
    namespace: str,
    destructive: bool,
    duration_ms: int,
    status: str,
    args_size: int,
    error_str: str | None = None,
    traceback_str: str | None = None,
    result_size: int | None = None,
) -> None:
    """Write one tool-call row, linked to the model call that issued it, and
    bump the turn-level counter. Tracebacks are truncated to _TRACEBACK_MAX."""
    if traceback_str and len(traceback_str) > _TRACEBACK_MAX:
        traceback_str = traceback_str[:_TRACEBACK_MAX] + "\n...<truncated>"
    acc = TURN_ACC.get()
    record = {
        "ts": _now_utc().isoformat(),
        "turn_id": TURN_ID.get(),
        "llm_call_index": acc["llm_calls"] if acc is not None else None,
        "tool": tool_name,
        "namespace": namespace,
        "destructive": int(bool(destructive)),
        "duration_ms": int(duration_ms),
        "status": status,
        "args_size": int(args_size),
        "result_size": result_size,
        "error": error_str,
        "traceback": traceback_str,
    }

    def write(rec):
        with store.write() as con:
            con.execute(
                f"INSERT INTO tool_calls ({', '.join(rec)}) "
                f"VALUES ({', '.join('?' for _ in rec)})",
                tuple(rec.values()),
            )

    _guarded("tool call", write, record)
    if acc is not None:
        acc["tool_calls"] += 1


def record_turn_end(
    active_skills_end: list[str] | None = None,
    no_action: bool = False,
) -> None:
    """Finalize the current turn: stamp ended_at + duration, update its row,
    clear TURN_ACC.

    `error` and `outcome` come from the accumulator — `ask_jarvis` sets them
    before calling this. Idempotent: a second call with TURN_ACC already
    cleared is a no-op.
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
    _guarded("turn end", _update_turn, dict(acc))
    TURN_ACC.set(None)


@contextmanager
def job(name: str):
    """Record the model calls of work that runs outside a turn (a nightly job,
    a compaction) as one ``turns`` row with scope "job".

        with telemetry.job("compaction"):
            response = llm.invoke(...)
            telemetry.record_llm_call(response, latency_ms=...)

    An exception marks the row failed and propagates.
    """
    token = TURN_ID.set(uuid4().hex)
    outer = TURN_ACC.get()
    t0 = time.perf_counter()
    acc = record_turn_start(thread_id=None, scope="job", job=name)
    try:
        yield acc
        acc["outcome"] = "completed"
    except BaseException as e:
        acc["outcome"] = "failed"
        acc["error"] = f"{type(e).__name__}: {e}"
        raise
    finally:
        logger.debug("job %s took %.0fms", name, (time.perf_counter() - t0) * 1000)
        record_turn_end()
        TURN_ACC.set(outer)
        TURN_ID.reset(token)
