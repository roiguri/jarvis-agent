#!/usr/bin/env python3
"""Point old history at the file store: each `[attached: <old cache path>]` in the
owner's messages becomes that file's `[file:…]` marker. One-off, per instance.

Run from the code root with the instance's service stopped:
  JARVIS_ROOT=/app venv/bin/python scripts/files_migrate_history.py --dry-run
  JARVIS_ROOT=/app venv/bin/python scripts/files_migrate_history.py
It backs up episodes.sqlite next to itself first. A path with no file stays as it
is and is listed. Running it again changes nothing.

TODO(#185): remove with the index's legacy_paths table, once every instance has run it.
"""
import argparse
import os
import re
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from episodes import store as episodes  # noqa: E402
from files import index, store  # noqa: E402

OLD_PATH = re.compile(r"\[attached: ([^\]\n]*/media_cache/[^\]\n]*)\]")


def file_for(path: str, turn_id: str | None) -> store.FileRecord | None:
    """The file an old path became in this turn; the import made one per arrival."""
    found = index.rows(
        "SELECT l.file_id FROM legacy_paths l JOIN files f ON f.id = l.file_id"
        " WHERE l.path = ? ORDER BY (f.turn_id IS ?) DESC, f.created DESC LIMIT 1",
        (path, turn_id))
    return store.get(found[0]["file_id"]) if found else None


def plan() -> tuple[list[tuple[int, str, str]], list[str]]:
    """(id, old content, new content) for each owner message to change, and the
    paths no file matches."""
    changes, unmatched = [], []
    for row in episodes.rows("SELECT id, turn_id, content FROM episodes WHERE role = 'owner'"
                             " AND content LIKE '%media_cache%' ORDER BY id"):
        def marker(m: re.Match) -> str:
            rec = file_for(m.group(1), row["turn_id"])
            if rec is None:
                unmatched.append(m.group(1))
                return m.group(0)
            return rec.marker()
        new = OLD_PATH.sub(marker, row["content"])
        if new != row["content"]:
            changes.append((row["id"], row["content"], new))
    return changes, unmatched


def apply(changes: list[tuple[int, str, str]]) -> None:
    """Rewrite the rows and their search entries in one transaction (the search
    index follows inserts and deletes only, so an edit updates it by hand)."""
    with episodes.write() as con:
        for rowid, old, new in changes:
            con.execute("INSERT INTO episodes_fts(episodes_fts, rowid, content)"
                        " VALUES ('delete', ?, ?)", (rowid, old))
            con.execute("UPDATE episodes SET content = ? WHERE id = ?", (new, rowid))
            con.execute("INSERT INTO episodes_fts(rowid, content) VALUES (?, ?)", (rowid, new))


def backup() -> str:
    dest = f"{episodes.STORE_PATH}.before-migrate-{datetime.now():%Y%m%d-%H%M%S}"
    with closing(sqlite3.connect(episodes.STORE_PATH)) as src, closing(sqlite3.connect(dest)) as out:
        src.backup(out)
    return dest


def service_running() -> bool:
    unit = "jarvis" if os.path.realpath(config.ROOT) == "/app" else "jarvis-staging"
    try:
        return subprocess.run(["systemctl", "is-active", "--quiet", unit]).returncode == 0
    except OSError:
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="list the changes, write nothing")
    dry = ap.parse_args().dry_run
    changes, unmatched = plan()
    for rowid, old, new in changes:
        print(f"#{rowid}")
        for a, b in zip(OLD_PATH.findall(old), re.findall(r"\[file:[^\]]*\]", new)):
            print(f"  {os.path.basename(a)} → {b}")
    print(f"{len(changes)} message(s) to change; {len(unmatched)} path(s) with no file")
    for path in unmatched:
        print(f"  no file: {path}")
    if dry or not changes:
        return 0
    if service_running():
        print("The service is running; stop it first.")
        return 1
    print(f"backup: {backup()}")
    apply(changes)
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
