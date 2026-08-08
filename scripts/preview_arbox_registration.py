#!/usr/bin/env python3
"""Show what a register/cancel WOULD do, against the live gym schedule, without
sending anything.

Reads the real schedule, resolves the class the way the tool does, runs every
precondition, renders the confirmation the owner would be shown, and prints the
exact payload that would go on the wire — with the write intercepted, so no
booking is ever made.

This is the layer between the offline suite (fixtures, no network) and a real
booking: it proves the resolver picks the right class out of the live schedule
and that the confirmation describes it correctly, at zero risk.

Only `/api/v2/schedule/betweenDates` is called — the same read the daily
briefing already makes. `scheduleUser/insert` and `scheduleUser/delete` are
replaced with a recorder before anything runs.

Usage:
    python scripts/preview_arbox_registration.py register 2026-08-11 20:00 WOD
    python scripts/preview_arbox_registration.py cancel   2026-08-11 20:00 WOD
"""

import argparse
import json
import os
import sys

os.environ.setdefault("JARVIS_ROOT", "/app/jarvis_staging")
os.environ.setdefault("GOOGLE_API_KEY", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime  # noqa: E402

from dotenv import load_dotenv  # noqa: E402

import config  # noqa: E402

load_dotenv(config.ENV_FILE)

from tools.fitness import registration as R  # noqa: E402
from tools.fitness._arbox import ARBOX_BASE  # noqa: E402


class _Intercepted(Exception):
    def __init__(self, path, body):
        super().__init__(path)
        self.path, self.body = path, body


def _no_writes(path, body):
    raise _Intercepted(path, body)


# Swap the write path out before anything can call it. Reads are untouched:
# they live on tools.fitness.classes, which keeps the real _arbox_post.
R._arbox_post = _no_writes
R._sync_registered_classes = lambda: "(sync skipped in preview)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["register", "cancel"])
    ap.add_argument("date", help="YYYY-MM-DD")
    ap.add_argument("time", help="HH:MM")
    ap.add_argument("category", nargs="?", default=None)
    a = ap.parse_args()

    print(f"resolving {a.action}: {a.date} {a.time} {a.category or '(no category given)'}\n")

    try:
        classes = R._fetch_day(a.date)
    except Exception as e:
        sys.exit(f"schedule fetch failed: {e}")

    print(f"{len(classes)} class(es) on {a.date}")
    try:
        cls = R._resolve_class(classes, a.date, a.time, a.category)
    except R._ClassLookupError as e:
        sys.exit(f"\nRESOLVER REFUSED: {e}")

    print(f"resolved -> schedule_id={cls['id']}  {R._category_of(cls)}\n")

    now_il = datetime.now(R.ISRAEL_TZ)
    booked = cls.get("user_booked") is not None
    print(f"currently booked: {booked}"
          + (f" (schedule_user_id={cls['user_booked']})" if booked else ""))

    if a.action == "register" and booked:
        sys.exit("\nNO-OP: already registered for this class.")
    if a.action == "cancel" and not booked:
        sys.exit("\nNO-OP: not registered, nothing to cancel.")

    blocker = R._blocker(a.action, cls, now_il)
    print(f"preconditions: {blocker or 'pass'}")
    if blocker:
        sys.exit("\nREFUSED before any prompt — nothing would be sent.")

    # Render the confirmation exactly as the tool builds it.
    deadline, limit_h = R._cancel_deadline(cls)
    identity = R._describe_class(cls)
    if a.action == "register":
        desc = f"Book this class on Arbox?\n\n{identity}"
        if deadline is not None:
            desc += f"\n\nFree cancellation until {deadline:%a %H:%M} ({limit_h}h before)."
    else:
        desc = f"Cancel this Arbox booking?\n\n{identity}"
        if deadline is not None:
            if now_il > deadline:
                desc += (f"\n\n⚠ This is a late cancel — the free window closed at "
                         f"{deadline:%a %H:%M} ({limit_h}h before the class).")
            else:
                desc += (f"\n\nStill free to cancel — the window closes "
                         f"{deadline:%a %H:%M} ({limit_h}h before).")
        if cls.get("stand_by"):
            desc += f"\n{cls['stand_by']} on the waitlist would get the place."

    print("\n" + "=" * 64)
    print("CONFIRMATION THE OWNER WOULD SEE")
    print("=" * 64)
    print(desc)

    print("\n" + "=" * 64)
    print("PAYLOAD THAT WOULD BE SENT  (intercepted — nothing was sent)")
    print("=" * 64)
    try:
        R._exec_registration(a.action, int(cls["id"]), a.date, a.time)
        print("no write was attempted")
    except _Intercepted as i:
        print(f"POST {ARBOX_BASE}{i.path}")
        print(json.dumps(i.body, indent=2))
    except Exception as e:
        print(f"stopped before the write: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
