"""
One-time checkpoint pruning script.

Extracts only the latest checkpoint per thread from the bloated threads.sqlite
into a clean new file. Avoids VACUUM (which needs free space equal to DB size).

Run with the jarvis service STOPPED:
    python3 /app/jarvis_code/scripts/prune_checkpoints.py

Steps:
    1. inspect   — show current state, no writes
    2. extract   — write threads_clean.sqlite (few MB)
    3. swap      — rename old to .bak, promote clean file
    4. cleanup   — delete .bak and stale WAL/SHM files (only after service confirmed healthy)
"""

import os
import sqlite3
import sys

OLD = "/app/jarvis_memory/threads.sqlite"
NEW = "/app/jarvis_memory/threads_clean.sqlite"
BAK = "/app/jarvis_memory/threads.sqlite.bak"

STEPS = ("inspect", "extract", "swap", "cleanup")


def step_inspect():
    print("=== Current state ===")
    conn = sqlite3.connect(f"file:{OLD}?mode=ro", uri=True)
    rows = conn.execute("""
        SELECT thread_id, COUNT(*) as checkpoints,
               ROUND(SUM(length(checkpoint) + length(metadata)) / 1e6, 1) as mb
        FROM checkpoints GROUP BY thread_id ORDER BY mb DESC
    """).fetchall()
    for thread_id, count, mb in rows:
        print(f"  {thread_id}: {count} checkpoints, {mb} MB")
    total = conn.execute(
        "SELECT ROUND(SUM(length(checkpoint)+length(metadata))/1e9,2) FROM checkpoints"
    ).fetchone()[0]
    print(f"  Total checkpoint data: {total} GB")
    print(f"  File on disk: {os.path.getsize(OLD)/1e9:.2f} GB")
    conn.close()


def step_extract():
    if os.path.exists(NEW):
        print(f"  {NEW} already exists — delete it first if you want to re-run.")
        sys.exit(1)

    src = sqlite3.connect(f"file:{OLD}?mode=ro", uri=True)
    dst = sqlite3.connect(NEW)

    dst.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE checkpoints (
            thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL DEFAULT '',
            checkpoint_id TEXT NOT NULL, parent_checkpoint_id TEXT,
            type TEXT, checkpoint BLOB, metadata BLOB,
            PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
        );
        CREATE TABLE writes (
            thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL DEFAULT '',
            checkpoint_id TEXT NOT NULL, task_id TEXT NOT NULL,
            idx INTEGER NOT NULL, channel TEXT NOT NULL,
            type TEXT, value BLOB,
            PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
        );
    """)

    keeper_rows = src.execute("""
        SELECT thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id,
               type, checkpoint, metadata
        FROM checkpoints
        WHERE checkpoint_id IN (
            SELECT MAX(checkpoint_id) FROM checkpoints GROUP BY thread_id, checkpoint_ns
        )
    """).fetchall()
    dst.executemany("INSERT INTO checkpoints VALUES (?,?,?,?,?,?,?)", keeper_rows)

    keeper_ids = [r[2] for r in keeper_rows]
    placeholders = ",".join("?" * len(keeper_ids))
    write_rows = src.execute(f"""
        SELECT thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, value
        FROM writes WHERE checkpoint_id IN ({placeholders})
    """, keeper_ids).fetchall()
    dst.executemany("INSERT INTO writes VALUES (?,?,?,?,?,?,?,?)", write_rows)

    dst.commit()
    src.close()
    dst.close()

    old_size = os.path.getsize(OLD) / 1e9
    new_size = os.path.getsize(NEW) / 1e6
    print(f"  Kept {len(keeper_rows)} checkpoint(s), {len(write_rows)} write(s)")
    print(f"  Old file: {old_size:.2f} GB  →  New file: {new_size:.1f} MB")
    print(f"  Saved to: {NEW}")
    print(f"  Review, then run: python3 {__file__} swap")


def step_swap():
    if not os.path.exists(NEW):
        print(f"  {NEW} not found — run extract first.")
        sys.exit(1)
    os.rename(OLD, BAK)
    os.rename(NEW, OLD)
    for suffix in ("-wal", "-shm"):
        stale = OLD.replace(".sqlite", ".sqlite" + suffix)
        if os.path.exists(stale):
            os.remove(stale)
            print(f"  Removed stale {stale}")
    # Restore ownership so jarvis_user (the service account) can write the file.
    import pwd
    try:
        pw = pwd.getpwnam("jarvis_user")
        os.chown(OLD, pw.pw_uid, pw.pw_gid)
        print(f"  Ownership set to jarvis_user")
    except KeyError:
        print("  Warning: jarvis_user not found — check file ownership manually.")
    print(f"  Swapped. Old DB backed up at: {BAK}")
    print(f"  Start the service, verify it works, then run: python3 {__file__} cleanup")


def step_cleanup():
    removed = []
    for path in (BAK, BAK + "-wal", BAK + "-shm"):
        if os.path.exists(path):
            os.remove(path)
            removed.append(path)
    if removed:
        for p in removed:
            print(f"  Deleted: {p}")
    else:
        print("  Nothing to clean up.")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in STEPS:
        print(f"Usage: python3 {__file__} [{' | '.join(STEPS)}]")
        sys.exit(1)
    step = sys.argv[1]
    print(f"\n--- Step: {step} ---")
    {"inspect": step_inspect, "extract": step_extract,
     "swap": step_swap, "cleanup": step_cleanup}[step]()


if __name__ == "__main__":
    main()
