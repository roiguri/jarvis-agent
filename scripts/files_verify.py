#!/usr/bin/env python3
"""Check the file store: records whose bytes are gone, bytes that no longer match their
name (--hashes, reads every file), bytes no record needs, and the record queue. Read-only.

Run from the code root, for one instance:
  JARVIS_ROOT=/app venv/bin/python scripts/files_verify.py [--hashes]
Run it after any `backup_state.sh --restore`: an older index can name files whose bytes
were collected since. Exit 1 when anything is missing or corrupt.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from files import store, worker  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hashes", action="store_true", help="re-hash every stored file")
    report = store.verify(check_hashes=ap.parse_args().hashes)
    gb = 1024**3
    print(f"records: {report['records']}")
    print(f"stored : {report['stored_bytes'] / gb:.2f} GB, free {report['free_bytes'] / gb:.1f} GB"
          + ("  (LOW)" if report["low"] else ""))
    print(f"missing bytes : {len(report['missing'])}  {' '.join(report['missing'][:20])}")
    print(f"corrupt bytes : {len(report['corrupt'])}  {' '.join(report['corrupt'][:5])}")
    print(f"unused bytes  : {len(report['unused'])} (the store collects these after a grace period)")
    queue = worker.queue_status()
    print(f"records queued: {queue['queued']}, given up: {queue['given_up']}")
    for job in queue["given_up_jobs"][:10]:
        print(f"  {job['file_id']}: {job['last_error']}")
    return 1 if report["missing"] or report["corrupt"] else 0


if __name__ == "__main__":
    sys.exit(main())
