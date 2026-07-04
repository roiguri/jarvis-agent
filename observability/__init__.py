"""Per-turn LLM observability — app-layer infrastructure, not agent tools.

Sits alongside ``gateway/`` (channel layer) and ``tools/`` (agent-callable
tools). The agent loop calls into the *write* side from `agent.py`; the
``/usage`` slash command and ad-hoc REPL queries call the *read* side.

Two modules, one concern:
- ``telemetry`` writes ``turns.jsonl`` / ``tool_calls.jsonl`` from inside the
  agent loop (ContextVar-scoped, called by ask_jarvis / _llm_node / _tool_node).
- ``usage`` reads the same streams back: parameterized rollups for the
  ``/usage`` slash command and ad-hoc analysis from a Python REPL.

Both should grow together; new helpers (e.g. the deferred ``usage_daily.jsonl``
rollup writer per the observability plan) belong here.
"""

from observability.telemetry import (
    TURN_ID,
    TURN_ACC,
    TURNS_LOG,
    TOOL_CALLS_LOG,
    record_turn_start,
    record_llm_call,
    record_tool_call,
    record_turn_end,
)
from observability.usage import (
    MODEL_PRICES,
    estimate_usd,
    load_turns,
    summarize_usage,
    format_usage_table,
    israel_day_range,
    israel_last_n_days,
)

__all__ = [
    # Write side (telemetry).
    "TURN_ID",
    "TURN_ACC",
    "TURNS_LOG",
    "TOOL_CALLS_LOG",
    "record_turn_start",
    "record_llm_call",
    "record_tool_call",
    "record_turn_end",
    # Read side (usage).
    "MODEL_PRICES",
    "estimate_usd",
    "load_turns",
    "summarize_usage",
    "format_usage_table",
    "israel_day_range",
    "israel_last_n_days",
]
