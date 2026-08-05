#!/usr/bin/env python3
"""
Travel tools test script — exercises the tool functions directly, no model.

Runs against a THROWAWAY JARVIS_ROOT, created and deleted per run, so it never
touches a real instance's database. That is why the root is set here, before any
project import: config.py binds every state path at import time (same reason
scripts/ci/check_paths.py sets it first).

This covers the *mechanism* only — that each action does what it claims and
refuses what it should. Whether the model reaches for the right tool with the
right arguments is judged by hand, in chat.

Usage:
    python scripts/test_travel.py            # run everything
    python scripts/test_travel.py -v         # show each tool's full output
"""

import argparse
import os
import shutil
import sys
import tempfile

# Root first, before config binds. Real secrets are never needed: nothing here
# makes a network call.
_SCRATCH = tempfile.mkdtemp(prefix="jarvis-test-travel-")
os.makedirs(os.path.join(_SCRATCH, "secrets"), exist_ok=True)
os.environ["JARVIS_ROOT"] = _SCRATCH

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.travel import manage_trip, query_travel_db  # noqa: E402

VERBOSE = False
_passed = 0
_failed = 0


def call(tool, **kwargs) -> str:
    return tool.invoke(kwargs)


def check(label: str, got: str, *, contains=(), missing=()) -> None:
    """Assert on substrings rather than exact text: these strings are written for
    a model to read and will be reworded, but the facts they must carry won't."""
    global _passed, _failed
    problems = []
    for c in (contains,) if isinstance(contains, str) else contains:
        if c.lower() not in got.lower():
            problems.append(f"expected {c!r}")
    for m in (missing,) if isinstance(missing, str) else missing:
        if m.lower() in got.lower():
            problems.append(f"should NOT contain {m!r}")
    if problems:
        _failed += 1
        print(f"  FAIL  {label}")
        for p in problems:
            print(f"          {p}")
        print(f"        got: {got!r}"[:400])
    else:
        _passed += 1
        print(f"  ok    {label}")
        if VERBOSE:
            for line in got.splitlines():
                print(f"          | {line}")


def section(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


# ---------------------------------------------------------------------------
# manage_trip
# ---------------------------------------------------------------------------


def test_manage_trip() -> None:
    section("manage_trip — creation and the current-trip pointer")

    check("empty list reads as empty", call(manage_trip, action="list"),
          contains="no trips yet")

    check("create claims current when nothing holds it",
          call(manage_trip, action="create", trip_id="alpha", destination="Alpha City",
               start_date="2026-10-10", end_date="2026-10-15", timezone="Asia/Tokyo"),
          contains=["created", "now the current trip"])

    check("second create does NOT steal the pointer",
          call(manage_trip, action="create", trip_id="beta", destination="Beta Town"),
          contains="created", missing="now the current trip")

    check("list marks exactly one current",
          call(manage_trip, action="list"), contains=["alpha", "beta", "CURRENT"])

    check("undated trip says so", call(manage_trip, action="list"),
          contains="no dates")

    check("duplicate id refused",
          call(manage_trip, action="create", trip_id="alpha", destination="Dup"),
          contains="already exists")

    check("create needs a destination",
          call(manage_trip, action="create", trip_id="gamma"),
          contains="needs both")

    section("manage_trip — refusals name the valid options")

    check("unknown id lists the real trips",
          call(manage_trip, action="set_current", trip_id="nope"),
          contains=["no trip", "alpha", "beta"])

    check("missing id lists the real trips",
          call(manage_trip, action="archive"), contains=["required", "alpha"])

    check("unknown action lists the real actions",
          call(manage_trip, action="frobnicate", trip_id="alpha"),
          contains=["unknown action", "set_current"])

    check("half a date window refused",
          call(manage_trip, action="update", trip_id="beta", start_date="2026-11-01"),
          contains="both start_date and end_date")

    check("backwards window refused",
          call(manage_trip, action="update", trip_id="beta",
               start_date="2026-11-10", end_date="2026-11-01"),
          contains="before start_date")

    check("malformed date refused",
          call(manage_trip, action="update", trip_id="beta",
               start_date="10/11/2026", end_date="2026-11-01"),
          contains="yyyy-mm-dd")

    check("unknown timezone refused",
          call(manage_trip, action="update", trip_id="beta", timezone="Mars/Olympus"),
          contains="unknown timezone")

    section("manage_trip — set_current, archive")

    check("set_current moves the pointer",
          call(manage_trip, action="set_current", trip_id="beta"),
          contains="beta is now the current trip")

    check("only one trip is current after the move",
          str(call(query_travel_db, sql="SELECT COUNT(*) AS n FROM trips WHERE is_current=1")),
          contains="1")

    check("archive frees the pointer",
          call(manage_trip, action="archive", trip_id="beta"),
          contains=["archived beta", "no longer the current trip"])

    check("nothing is current once the pinned trip is archived",
          call(query_travel_db, sql="SELECT COUNT(*) AS n FROM trips WHERE is_current=1"),
          contains="0")

    check("archiving an unpinned trip says nothing about the pointer",
          call(manage_trip, action="archive", trip_id="alpha"),
          contains="archived alpha", missing="no longer the current")

    check("update with no fields is a no-op, not an error",
          call(manage_trip, action="update", trip_id="alpha"),
          contains="nothing to update")


def test_trip_date_shift() -> None:
    section("manage_trip — moving a trip drags plans but not bookings")

    call(manage_trip, action="create", trip_id="shift", destination="Shift City",
         start_date="2026-10-10", end_date="2026-10-15")

    conn = _raw()
    conn.execute(
        "INSERT INTO itinerary(trip_id, item_type, title, start_date, start_time) "
        "VALUES('shift','note','Free walking tour','2026-10-11','09:00')"
    )
    conn.execute(
        "INSERT INTO itinerary(trip_id, item_type, title, start_date, end_date) "
        "VALUES('shift','lodging','Hotel','2026-10-10','2026-10-15')"
    )
    conn.execute(
        "INSERT INTO itinerary(trip_id, item_type, title, start_date, confirmation_code) "
        "VALUES('shift','transit','Flight out','2026-10-10','PNR-1')"
    )
    conn.commit()
    conn.close()

    out = call(manage_trip, action="update", trip_id="shift",
               start_date="2026-10-17", end_date="2026-10-22")
    check("pure translation moves the unbooked items", out,
          contains=["moved 2", "+7"])
    check("the booked item is reported, not moved", out,
          contains=["did not move", "flight out", "PNR-1"])

    check("unbooked dates advanced by exactly 7 days",
          call(query_travel_db,
               sql="SELECT title, start_date FROM itinerary WHERE trip_id='shift' "
                   "AND title='Free walking tour'"),
          contains="2026-10-18")

    check("a stay's end date moved with its start",
          call(query_travel_db,
               sql="SELECT end_date FROM itinerary WHERE trip_id='shift' AND title='Hotel'"),
          contains="2026-10-22")

    check("the booking kept its original date",
          call(query_travel_db,
               sql="SELECT start_date FROM itinerary WHERE trip_id='shift' "
                   "AND title='Flight out'"),
          contains="2026-10-10")

    out = call(manage_trip, action="update", trip_id="shift",
               start_date="2026-10-17", end_date="2026-10-30")
    check("changing trip length moves nothing", out,
          contains="length changed", missing="moved 2")
    check("but names what now falls outside the window", out,
          contains=["outside", "flight out"])


def test_delete_cascade() -> None:
    section("manage_trip — delete cascades to rows, spares places")

    conn = _raw()
    conn.execute("INSERT INTO places(title) VALUES('Somewhere')")
    conn.execute("INSERT INTO wishlist(trip_id, place_id) VALUES('shift', 1)")
    conn.commit()
    conn.close()

    # The confirmation UI needs a channel, which this script has no business
    # standing up — so the guarded body is called directly. What the button
    # protects is tested here; that it IS behind a button is read off the code.
    from tools.travel.trips import _exec_delete_trip

    check("delete reports what it removed", _exec_delete_trip("shift"),
          contains=["deleted shift", "1 wishlist", "3 scheduled"])

    check("the trip is gone",
          call(query_travel_db, sql="SELECT COUNT(*) AS n FROM trips WHERE trip_id='shift'"),
          contains="0")

    check("its scheduled rows went with it",
          call(query_travel_db, sql="SELECT COUNT(*) AS n FROM itinerary"),
          contains="0")

    check("the saved place survived",
          call(query_travel_db, sql="SELECT title FROM places"),
          contains="Somewhere")


def _raw():
    """A direct connection, for arranging rows that the tools under test don't
    write yet. Replaced by the real tools as later commits add them."""
    from tools.travel._db import _get_db

    return _get_db()


def main() -> int:
    global VERBOSE
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-v", "--verbose", action="store_true", help="print each tool's full output")
    VERBOSE = ap.parse_args().verbose

    print(f"scratch root: {_SCRATCH}")
    try:
        test_manage_trip()
        test_trip_date_shift()
        test_delete_cascade()
    finally:
        shutil.rmtree(_SCRATCH, ignore_errors=True)

    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
