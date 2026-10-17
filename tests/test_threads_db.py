"""threads.sqlite stays small: freed pages go back to the disk and the WAL is capped."""

import os
import sqlite3

import agent


def test_a_bloated_file_is_compacted_once_and_then_stays_small(tmp_path):
    path = str(tmp_path / "threads.sqlite")
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (b BLOB)")
    conn.executemany("INSERT INTO t VALUES (?)", [(os.urandom(4000),) for _ in range(500)])
    conn.execute("DELETE FROM t")
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    bloated = os.path.getsize(path)

    agent._keep_db_small(conn)
    assert conn.execute("PRAGMA auto_vacuum").fetchone()[0] == 1
    assert os.path.getsize(path) < bloated / 10

    for _ in range(50):
        conn.execute("DELETE FROM t")
        conn.execute("INSERT INTO t VALUES (?)", (os.urandom(80000),))
        conn.commit()
    assert conn.execute("PRAGMA freelist_count").fetchone()[0] == 0
    assert conn.execute("PRAGMA journal_size_limit").fetchone()[0] == 4194304


def test_a_locked_file_is_left_as_it_is(tmp_path):
    path = str(tmp_path / "threads.sqlite")
    holder = sqlite3.connect(path)
    holder.execute("CREATE TABLE t (b BLOB)")
    holder.execute("BEGIN EXCLUSIVE")
    conn = sqlite3.connect(path, timeout=0)
    agent._keep_db_small(conn)   # logs and returns instead of raising
    holder.rollback()
    assert conn.execute("PRAGMA auto_vacuum").fetchone()[0] == 0
