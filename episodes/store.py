"""The episode store: one SQLite file holding everything that happened, in order.

Every turn's input, each model step (reply text, tool calls with their
arguments, tool results), every delivered notification and every slash command
is a row. Content is kept in full apart from a narrow secret scrub and a size
cap on a single row, and is never deleted. Rows are written by code as things
happen; the agent reads them only through ``search_history`` and the prompt's
today view.

A separate file from the telemetry store: this one is conversation content.
Writers open a short-lived connection per event (user and heartbeat turns run
on different threads); WAL lets readers run alongside.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from typing import Iterator

import config

STORE_DIR = os.path.join(config.DATA_DIR, "episodes")
STORE_PATH = os.path.join(STORE_DIR, "episodes.sqlite")

# A single row's content is cut past this; nothing real comes near it (the
# largest tool result recorded is ~51 KB), it guards against a freak page.
MAX_CONTENT_CHARS = 200_000

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    turn_id TEXT,
    thread_id TEXT,
    scope TEXT,
    channel TEXT,
    role TEXT NOT NULL,
    source TEXT NOT NULL,
    tool_name TEXT,
    meta TEXT,
    content TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS episodes_ts ON episodes(ts);
CREATE INDEX IF NOT EXISTS episodes_turn ON episodes(turn_id);

CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(
    content, content='episodes', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS episodes_ai AFTER INSERT ON episodes BEGIN
    INSERT INTO episodes_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS episodes_ad AFTER DELETE ON episodes BEGIN
    INSERT INTO episodes_fts(episodes_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;

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


def connect() -> sqlite3.Connection:
    """A connection with the schema in place. Callers close it."""
    with _INIT_LOCK:
        # A deleted file (tests, a manual reset) is re-created on next use.
        if STORE_PATH not in _initialized or not os.path.exists(STORE_PATH):
            os.makedirs(STORE_DIR, exist_ok=True)
            with closing(_open(STORE_PATH)) as con:
                con.execute("PRAGMA journal_mode = WAL")
                con.executescript(_SCHEMA)
            _initialized.add(STORE_PATH)
    return _open(STORE_PATH)


@contextmanager
def write() -> Iterator[sqlite3.Connection]:
    """One short transaction. Raises on failure — the recorders catch it."""
    with closing(connect()) as con:
        with con:
            yield con


def rows(sql: str, params: tuple = ()) -> list[dict]:
    """Read query → list of dicts. An absent store reads as empty."""
    if not os.path.exists(STORE_PATH):
        return []
    with closing(connect()) as con:
        return [dict(r) for r in con.execute(sql, params)]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Content hygiene
# ---------------------------------------------------------------------------

# Recognisable credential shapes only. Generic long numbers and hex strings are
# left alone: IDs and hashes look the same and would be destroyed by mistake.
_SECRET_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),                      # Google API key
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),                 # GitHub token
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),               # GitHub fine-grained token
    re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"),                     # OpenAI-style key
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),  # JWT
    re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9\-._~+/]{16,}=*"),   # Authorization header
]
# key=value / "key": "value" fields whose value is a secret; the key is kept.
_SECRET_FIELD = re.compile(
    r"""(?ix)
    (["']?\b(?:password|passwd|api[_-]?key|secret|token|access[_-]?token|
             refresh[_-]?token|auth[_-]?token|client[_-]?secret)\b["']?\s*[:=]\s*["']?)
    ([^"'\s,&}]{6,})
    """
)


def scrub(text: str) -> str:
    """Replace recognisable secrets with [redacted]."""
    for pattern in _SECRET_PATTERNS:
        if pattern.groups:
            text = pattern.sub(lambda m: m.group(1) + "[redacted]", text)
        else:
            text = pattern.sub("[redacted]", text)
    return _SECRET_FIELD.sub(lambda m: m.group(1) + "[redacted]", text)


def clean(text: str) -> str:
    """Scrubbed and size-capped content for one row."""
    text = scrub(text or "")
    if len(text) > MAX_CONTENT_CHARS:
        cut = len(text) - MAX_CONTENT_CHARS
        text = text[:MAX_CONTENT_CHARS] + f"\n[… {cut:,} more chars cut]"
    return text


def insert(con: sqlite3.Connection, **row) -> None:
    row["content"] = clean(row.get("content", ""))
    if isinstance(row.get("meta"), dict):
        row["meta"] = json.dumps(row["meta"], ensure_ascii=False) if row["meta"] else None
    row.setdefault("ts", now_iso())
    con.execute(
        f"INSERT INTO episodes ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
        tuple(row.values()),
    )
