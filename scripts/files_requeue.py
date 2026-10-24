#!/usr/bin/env python3
"""Queue file records again for one kind: files whose record is missing or older than
that kind's current version, failed jobs included. Use it after changing how a kind is
recorded (files/records.py) and bumping its version. Only queues; the running service
makes the records, one at a time.

Run from the code root, for one instance:
  JARVIS_ROOT=/app venv/bin/python scripts/files_requeue.py --kind image
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from files import records, worker  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", required=True, choices=sorted(records.RECORDERS))
    kind = ap.parse_args().kind
    queued = worker.requeue_outdated(kind)
    print(f"queued {queued} {kind} records (current version {records.RECORDERS[kind].version})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
