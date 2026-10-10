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
-- The natural key that makes the JSONL import re-runnable (COALESCE: NULLs
-- are distinct in a unique index, so rows without a turn_id would repeat).
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

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
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
# One-time import of the JSONL streams this store replaced
# TODO(#150): remove this section once both instances have imported.
# ---------------------------------------------------------------------------

_TURN_COLUMNS = (
    "ts", "turn_id", "thread_id", "scope", "started_at", "ended_at", "duration_ms",
    "llm_calls", "tool_calls", "input_tokens", "cache_read_tokens", "output_tokens",
    "reasoning_tokens", "total_tokens", "model", "outcome", "error",
)
_TOOL_COLUMNS = (
    "ts", "turn_id", "tool", "namespace", "duration_ms", "status", "args_size",
    "error", "traceback",
)


def _read_jsonl(path: str, cutoff: datetime) -> tuple[list[dict], int]:
    out, skipped = [], 0
    if not os.path.exists(path):
        return out, skipped
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                ts = datetime.fromisoformat(rec["ts"])
            except (KeyError, ValueError, TypeError):
                skipped += 1
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts >= cutoff:
                # Range queries compare ts as text, so every row is UTC ISO.
                rec["ts"] = ts.astimezone(timezone.utc).isoformat()
                out.append(rec)
    return out, skipped


def import_jsonl(turns_path: str, tool_calls_path: str,
                 days: int = RETENTION_DAYS) -> dict[str, int]:
    """Copy the last ``days`` of turns.jsonl / tool_calls.jsonl into the store.

    Idempotent: turns are keyed by turn_id and tool calls by (turn_id, ts,
    tool), so a re-run inserts nothing new. Rows without a turn_id (very old
    records) get a synthetic one so they still count in rollups.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    turns, bad_turns = _read_jsonl(turns_path, cutoff)
    tools, bad_tools = _read_jsonl(tool_calls_path, cutoff)
    added_turns = added_tools = 0
    with write() as con:
        for rec in turns:
            values = {c: rec.get(c) for c in _TURN_COLUMNS}
            values["turn_id"] = values["turn_id"] or f"legacy-{rec['ts']}"
            values["no_action"] = int(bool(rec.get("no_action")))
            values["active_skills_start"] = dumps(rec.get("active_skills_start"))
            values["active_skills_end"] = dumps(rec.get("active_skills_end"))
            values["budget"] = dumps(rec.get("budget"))
            cols = ", ".join(values)
            marks = ", ".join("?" for _ in values)
            added_turns += con.execute(
                f"INSERT OR IGNORE INTO turns ({cols}) VALUES ({marks})", tuple(values.values())
            ).rowcount
        for rec in tools:
            values = {c: rec.get(c) for c in _TOOL_COLUMNS}
            values["destructive"] = int(bool(rec.get("destructive")))
            cols = ", ".join(values)
            marks = ", ".join("?" for _ in values)
            added_tools += con.execute(
                f"INSERT OR IGNORE INTO tool_calls ({cols}) VALUES ({marks})", tuple(values.values())
            ).rowcount
        con.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('jsonl_import', ?)",
            (datetime.now(timezone.utc).isoformat(),),
        )
    return {"turns": added_turns, "tool_calls": added_tools,
            "skipped_lines": bad_turns + bad_tools}


def imported() -> bool:
    """Whether the JSONL import has run against this store."""
    return bool(rows("SELECT 1 FROM meta WHERE key = 'jsonl_import'"))


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
