"""The telemetry store: one SQLite file of what each turn did and cost.

Separate from conversation content by design — messages, tool arguments and
tool results never land here. The one exception is the system prompt, kept
once per distinct text (``prompts``), because "what exactly did the model see"
is the first question when debugging a turn.

Writers open a short-lived connection per event (``write``): the user and
heartbeat turns run on different threads, and a connection per event keeps
them from sharing one. WAL lets readers (``/usage``, scripts) run alongside.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator

import config

STORE_DIR = os.path.join(config.DATA_DIR, "observability")
STORE_PATH = os.path.join(STORE_DIR, "telemetry.sqlite")
RETENTION_DAYS = 180

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    turn_id TEXT NOT NULL UNIQUE,
    thread_id TEXT,
    scope TEXT,
    job TEXT,
    channel TEXT,
    trigger_id TEXT,
    due_tasks TEXT,
    started_at TEXT,
    ended_at TEXT,
    duration_ms INTEGER,
    llm_calls INTEGER DEFAULT 0,
    tool_calls INTEGER DEFAULT 0,
    input_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    reasoning_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    model TEXT,
    active_skills_start TEXT,
    active_skills_end TEXT,
    no_action INTEGER DEFAULT 0,
    outcome TEXT,
    error TEXT,
    budget TEXT
);
CREATE INDEX IF NOT EXISTS turns_ts ON turns(ts);

CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    turn_id TEXT,
    call_index INTEGER,
    started_at TEXT,
    latency_ms INTEGER,
    input_tokens INTEGER,
    cache_read_tokens INTEGER,
    output_tokens INTEGER,
    reasoning_tokens INTEGER,
    finish_reason TEXT,
    model TEXT,
    prompt_hash TEXT,
    prompt_chars INTEGER,
    schema_chars INTEGER,
    bound_tool_count INTEGER,
    history_chars INTEGER,
    turn_chars INTEGER,
    history_messages INTEGER,
    turn_messages INTEGER,
    error TEXT,
    telemetry_error TEXT
);
CREATE INDEX IF NOT EXISTS llm_calls_turn ON llm_calls(turn_id);
CREATE INDEX IF NOT EXISTS llm_calls_ts ON llm_calls(ts);

CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    turn_id TEXT,
    llm_call_index INTEGER,
    tool TEXT,
    namespace TEXT,
    destructive INTEGER,
    duration_ms INTEGER,
    status TEXT,
    args_size INTEGER,
    result_size INTEGER,
    error TEXT,
    traceback TEXT
);
CREATE INDEX IF NOT EXISTS tool_calls_turn ON tool_calls(turn_id);
CREATE INDEX IF NOT EXISTS tool_calls_ts ON tool_calls(ts);
-- One row per tool call: a duplicate write fails, and the recorder logs it.
CREATE UNIQUE INDEX IF NOT EXISTS tool_calls_key ON tool_calls(COALESCE(turn_id, ''), ts, tool);

CREATE TABLE IF NOT EXISTS bound_tools (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    namespace TEXT,
    schema_chars INTEGER,
    first_call_index INTEGER,
    UNIQUE (turn_id, tool)
);
CREATE INDEX IF NOT EXISTS bound_tools_ts ON bound_tools(ts);

CREATE TABLE IF NOT EXISTS prompts (
    prompt_hash TEXT PRIMARY KEY,
    scope TEXT,
    chars INTEGER,
    text TEXT,
    first_seen TEXT,
    last_seen TEXT
);
"""

_INIT_LOCK = threading.Lock()
_initialized: set[str] = set()


def _open(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=5)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 5000")
    # Safe with WAL: a power cut can lose the last commits, never corrupt the file.
    con.execute("PRAGMA synchronous = NORMAL")
    return con


def connect(path: str | None = None) -> sqlite3.Connection:
    """A connection with the schema in place. Callers close it."""
    path = path or STORE_PATH
    with _INIT_LOCK:
        # A deleted file (tests, a manual reset) is re-created on next use.
        if path not in _initialized or not os.path.exists(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with closing(_open(path)) as con:
                con.execute("PRAGMA journal_mode = WAL")
                con.executescript(_SCHEMA)
            _initialized.add(path)
    return _open(path)


@contextmanager
def write() -> Iterator[sqlite3.Connection]:
    """One short transaction. Raises on failure — the telemetry recorders catch
    it, so a write problem never reaches a turn."""
    with closing(connect()) as con:
        with con:
            yield con


def rows(sql: str, params: tuple = ()) -> list[dict]:
    """Read query → list of dicts. An absent store reads as empty."""
    if not os.path.exists(STORE_PATH):
        return []
    with closing(connect()) as con:
        return [dict(r) for r in con.execute(sql, params)]


def dumps(value) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False)


def loads(value):
    if value is None:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------

def trim(days: int = RETENTION_DAYS) -> dict[str, int]:
    """Delete rows older than ``days`` from every table; returns counts."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    deleted = {}
    with write() as con:
        for table in ("turns", "llm_calls", "tool_calls", "bound_tools"):
            deleted[table] = con.execute(f"DELETE FROM {table} WHERE ts < ?", (cutoff,)).rowcount
        deleted["prompts"] = con.execute(
            "DELETE FROM prompts WHERE last_seen < ?", (cutoff,)
        ).rowcount
    return deleted


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------

def backup(dest: str) -> None:
    """A consistent copy of the live store via SQLite's online backup API."""
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    with closing(connect()) as src, closing(sqlite3.connect(dest)) as dst:
        src.backup(dst)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Telemetry store maintenance.")
    ap.add_argument("--backup", metavar="DEST", help="online-backup the store to DEST")
    args = ap.parse_args()
    if args.backup:
        backup(args.backup)
        print(args.backup)
