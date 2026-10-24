"""The file index: one SQLite file describing every stored file.

Tables:
  files        one row per arrival (the same bytes arriving twice are two rows
               sharing a hash). Kept forever; a removed file keeps a minimal row.
  derived      what was made from a file: description, transcript, text, and
               later thumbnails or embeddings. One row per (file, kind); the
               producer and version say what made it, so it can be redone.
  chunks       searchable text split into pieces with their position, indexed
               by chunks_fts. Derived from the tables above and rebuildable.
               Trigram-tokenized: a word matches inside a longer one, so a
               Hebrew word is found with a prefix attached.
  jobs         background work still to do for a file.
  legacy_paths old channel-cache paths → the file ids they became.

Short-lived connections, WAL and a busy timeout, as in the episode store: the turn,
tools and the background worker run on different threads.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from typing import Iterator

import config

INDEX_DIR = os.path.join(config.DATA_DIR, "files")
INDEX_PATH = os.path.join(INDEX_DIR, "files.sqlite")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id TEXT PRIMARY KEY,
    hash TEXT NOT NULL,
    kind TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    size INTEGER NOT NULL,
    filename TEXT,
    caption TEXT,
    source TEXT NOT NULL,
    origin TEXT,
    turn_id TEXT,
    created TEXT NOT NULL,
    retention TEXT NOT NULL DEFAULT 'kept',
    state TEXT NOT NULL DEFAULT 'available',
    state_changed TEXT,
    removed_reason TEXT
);
CREATE INDEX IF NOT EXISTS files_hash ON files(hash);
CREATE INDEX IF NOT EXISTS files_created ON files(created);
CREATE INDEX IF NOT EXISTS files_turn ON files(turn_id);

CREATE TABLE IF NOT EXISTS derived (
    id INTEGER PRIMARY KEY,
    file_id TEXT NOT NULL REFERENCES files(id),
    kind TEXT NOT NULL,
    producer TEXT NOT NULL,
    version TEXT NOT NULL,
    created TEXT NOT NULL,
    content TEXT NOT NULL,
    UNIQUE (file_id, kind)
);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    file_id TEXT NOT NULL REFERENCES files(id),
    origin TEXT NOT NULL,
    position INTEGER NOT NULL,
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_file ON chunks(file_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text, content='chunks', content_rowid='id', tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    file_id TEXT NOT NULL REFERENCES files(id),
    kind TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_at TEXT NOT NULL,
    last_error TEXT,
    created TEXT NOT NULL,
    UNIQUE (file_id, kind)
);

CREATE TABLE IF NOT EXISTS legacy_paths (
    path TEXT NOT NULL,
    file_id TEXT NOT NULL REFERENCES files(id),
    PRIMARY KEY (path, file_id)
);
"""

_INIT_LOCK = threading.Lock()
_initialized: set[str] = set()


def _open(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=5)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 5000")
    con.execute("PRAGMA foreign_keys = ON")
    # Safe with WAL: a power cut can lose the last commits, never corrupt the file.
    con.execute("PRAGMA synchronous = NORMAL")
    return con


def connect() -> sqlite3.Connection:
    """A connection with the schema in place. Callers close it."""
    with _INIT_LOCK:
        # A deleted file (tests, a manual reset) is re-created on next use.
        if INDEX_PATH not in _initialized or not os.path.exists(INDEX_PATH):
            os.makedirs(INDEX_DIR, exist_ok=True)
            with closing(_open(INDEX_PATH)) as con:
                con.execute("PRAGMA journal_mode = WAL")
                con.executescript(_SCHEMA)
                _upgrade(con)
            _initialized.add(INDEX_PATH)
    return _open(INDEX_PATH)


# PRAGMA user_version: 1 = chunks_fts is trigram-tokenized and built.
_VERSION = 1


def _upgrade(con: sqlite3.Connection) -> None:
    """Bring an older index up to _VERSION. Version 1 rebuilds the search table
    with the trigram tokenizer from `chunks`. The version is set last, so an
    interrupted upgrade runs again on the next open."""
    if con.execute("PRAGMA user_version").fetchone()[0] >= _VERSION:
        return
    con.execute("DROP TABLE IF EXISTS chunks_fts")
    con.executescript(_SCHEMA)
    con.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('rebuild')")
    con.execute(f"PRAGMA user_version = {_VERSION}")
    con.commit()


@contextmanager
def write() -> Iterator[sqlite3.Connection]:
    """One short transaction, committed on exit, rolled back on error."""
    with closing(connect()) as con:
        with con:
            yield con


def rows(sql: str, params: tuple = ()) -> list[dict]:
    """Read query → list of dicts. An absent index reads as empty."""
    if not os.path.exists(INDEX_PATH):
        return []
    with closing(connect()) as con:
        return [dict(r) for r in con.execute(sql, params)]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
