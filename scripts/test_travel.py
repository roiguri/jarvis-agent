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

from tools.travel import manage_place, manage_trip, query_travel_db  # noqa: E402

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


# ---------------------------------------------------------------------------
# manage_place
# ---------------------------------------------------------------------------

# Two branches of one chain plus an unrelated hit — the case the whole
# search-then-save split exists for. Shaped exactly like a Text Search reply so
# _flatten is exercised on the real structure, not a convenient one.
FAKE_PLACES = [
    {
        "id": "ChIJ_branch_one",
        "displayName": {"text": "Cafe Central — Baixa"},
        "formattedAddress": "Rua Augusta 1, Lisboa",
        "location": {"latitude": 38.7107, "longitude": -9.1373},
        "types": ["coffee_shop", "cafe", "food", "point_of_interest"],
        "primaryType": "coffee_shop",
        "primaryTypeDisplayName": {"text": "Coffee Shop"},
        "googleMapsUri": "https://maps.google.com/?cid=1",
    },
    {
        "id": "ChIJ_branch_two",
        "displayName": {"text": "Cafe Central — Alfama"},
        "formattedAddress": "Largo do Chafariz 8, Lisboa",
        "location": {"latitude": 38.7128, "longitude": -9.1281},
        "types": ["italian_restaurant", "restaurant", "food"],
        "primaryType": "italian_restaurant",
        "primaryTypeDisplayName": {"text": "Italian Restaurant"},
        "googleMapsUri": "https://maps.google.com/?cid=2",
    },
]


_api_calls = 0


def _fake_search(query: str, near: str) -> list:
    global _api_calls
    _api_calls += 1
    return FAKE_PLACES


def test_categories() -> None:
    """Bucketing: the exact table, the suffix fallback, and the cases that must
    stay undecided rather than be filed wrongly."""
    from tools.travel.places import CATEGORIES, _categorize

    section("manage_place — bucketing Google's types")

    cases = [
        ("pastry_shop", "dessert"), ("ice_cream_shop", "dessert"),
        ("coffee_shop", "cafe"), ("cafe", "cafe"),
        ("kebab_shop", "restaurant"),       # a real type we have actually seen
        ("barber_shop", None),              # unknown _shop: undecided, not shopping
        ("italian_restaurant", "restaurant"),   # via suffix
        ("ramen_restaurant", "restaurant"),     # a cuisine nobody enumerated
        ("sandwich_shop", "restaurant"),        # named, because the suffix would lie
        ("wine_bar", "bar"), ("farmers_market", "market"),
        ("museum", "sights"), ("historical_landmark", "sights"),
        ("botanical_garden", "outdoors"), ("beach", "outdoors"),
        ("subway_station", "transit"),          # via suffix
        ("book_store", "shopping"),             # via suffix
        ("hotel", "lodging"),
        ("dentist", None), ("", None), (None, None),
    ]
    wrong = [(t, _categorize(t), want) for t, want in cases if _categorize(t) != want]
    check(f"{len(cases)} type mappings", "ok" if not wrong else f"wrong: {wrong}",
          contains="ok")

    check("every mapped bucket is in the closed vocabulary",
          "ok" if all(v in CATEGORIES for v in _TYPE_VALUES()) else "drifted",
          contains="ok")

    check("a category outside the vocabulary is refused",
          call(manage_place, action="save", title="X", category="delicious"),
          contains=["unknown category", "restaurant", "other"])


def _TYPE_VALUES():
    from tools.travel.places import _TYPE_TO_CATEGORY, _SUFFIX_RULES

    return list(_TYPE_TO_CATEGORY.values()) + [b for _, b in _SUFFIX_RULES]


def test_search_guards() -> None:
    """The loop guards: a repeated query must not reach the API, and a run of
    distinct ones must stop with advice rather than an error to retry."""
    from tools.travel import places as places_mod

    section("manage_place — loop guards")

    global _api_calls
    real_search = places_mod._search_places
    places_mod._search_places = _fake_search
    places_mod._TURN_SEARCHES.clear()
    try:
        _api_calls = 0
        call(manage_place, action="search", query="ramen", near="Lisbon")
        call(manage_place, action="search", query="ramen", near="Lisbon")
        call(manage_place, action="search", query="RAMEN", near="lisbon")
        check(f"a repeated query never reaches the API (calls={_api_calls})",
              str(_api_calls), contains="1")

        check("the repeat still returns the candidates",
              call(manage_place, action="search", query="ramen", near="Lisbon"),
              contains=["2 candidate", "Baixa"])

        for i in range(2, 6):
            call(manage_place, action="search", query=f"distinct query {i}")
        check(f"four more distinct queries hit the API (calls={_api_calls})",
              str(_api_calls), contains="5")

        check("the sixth distinct query is refused, with a way out",
              call(manage_place, action="search", query="one too many"),
              contains=["limit", "ask the owner", "by hand"])

        check("and it did NOT reach the API", str(_api_calls), contains="5")

        check("an already-seen query still works after the cap",
              call(manage_place, action="search", query="ramen", near="Lisbon"),
              contains="2 candidate")
    finally:
        places_mod._search_places = real_search
        places_mod._TURN_SEARCHES.clear()


def test_manage_place() -> None:
    from tools.travel import places as places_mod

    section("manage_place — lookup returns candidates, never picks for you")

    check("no API key is a refusal that still offers a way forward",
          call(manage_place, action="search", query="Cafe Central"),
          contains=["not configured", "by hand"])

    real_search = places_mod._search_places
    places_mod._search_places = _fake_search
    try:
        out = call(manage_place, action="search", query="Cafe Central", near="Lisbon")
        check("both branches are offered, not silently resolved", out,
              contains=["2 candidate", "Baixa", "Alfama"])
        check("each candidate carries the id save needs", out,
              contains=["ChIJ_branch_one", "ChIJ_branch_two"])
        check("search alone saves nothing", call(manage_place, action="list"),
              contains="no saved places")

        check("search needs a query", call(manage_place, action="search"),
              contains="needs a query")

        section("manage_place — saving keeps what the search knew")

        check("saving a candidate reports a new place",
              call(manage_place, action="save", google_place_id="ChIJ_branch_one",
                   title="Cafe Central — Baixa"),
              contains="saved place")

        check("coordinates and type came from the search, not the arguments",
              call(query_travel_db,
                   sql="SELECT lat, lng, google_type, address FROM places "
                       "WHERE google_place_id='ChIJ_branch_one'"),
              contains=["38.7107", "-9.1373", "coffee_shop", "Rua Augusta"])

        check("primaryType wins over types[0], and its label is kept",
              call(query_travel_db,
                   sql="SELECT google_type, google_type_label FROM places "
                       "WHERE google_place_id='ChIJ_branch_one'"),
              contains=["coffee_shop", "Coffee Shop"])


        check("re-saving the same google id does not duplicate",
              call(manage_place, action="save", google_place_id="ChIJ_branch_one",
                   title="Cafe Central — Baixa"),
              contains=["already saved", "nothing duplicated"])

        check("still exactly one row for it",
              call(query_travel_db,
                   sql="SELECT COUNT(*) AS n FROM places WHERE google_place_id='ChIJ_branch_one'"),
              contains="1")

        check("the other branch is a separate place",
              call(manage_place, action="save", google_place_id="ChIJ_branch_two",
                   title="Cafe Central — Alfama"),
              contains="saved place")
        check("the full types array survives, so nothing finer is lost",
              call(query_travel_db,
                   sql="SELECT google_types FROM places "
                       "WHERE google_place_id='ChIJ_branch_two'"),
              contains=["italian_restaurant", "restaurant", "food"])
    finally:
        places_mod._search_places = real_search

    check("a hand-added place needs no google id",
          call(manage_place, action="save", title="Ana's kitchen",
               address="a friend's flat"),
          contains="saved place")

    check("save needs at least a title", call(manage_place, action="save"),
          contains="needs at least a title")

    section("manage_place — corrections are shared, deletion is guarded")

    check("list shows ids to work with", call(manage_place, action="list"),
          contains=["[1]", "[2]", "[3]"])

    check("unknown id lists the saved places",
          call(manage_place, action="update", place_id=999, title="x"),
          contains=["no place with id 999", "cafe central"])

    check("missing id lists the saved places",
          call(manage_place, action="update", title="x"),
          contains=["place_id is required", "cafe central"])

    check("correcting an address is one edit",
          call(manage_place, action="update", place_id=1, address="Rua Augusta 2, Lisboa"),
          contains=["updated place 1", "rua augusta 2"])

    check("update with no fields is a no-op, not an error",
          call(manage_place, action="update", place_id=1), contains="nothing to update")

    check("unknown action lists the real actions",
          call(manage_place, action="frobnicate", place_id=1),
          contains=["unknown action", "search"])

    check("an unreferenced place deletes without a button",
          call(manage_place, action="delete", place_id=3), contains="deleted place 3")

    # A reference is what makes deletion refuse, so make one.
    call(manage_trip, action="create", trip_id="pl", destination="Placeville")
    conn = _raw()
    conn.execute("INSERT INTO wishlist(trip_id, place_id) VALUES('pl', 1)")
    conn.commit()
    conn.close()

    check("a referenced place refuses to delete, and says by what",
          call(manage_place, action="delete", place_id=1),
          contains=["still used by", "1 wishlist"])

    check("and it is still there",
          call(query_travel_db, sql="SELECT COUNT(*) AS n FROM places WHERE place_id=1"),
          contains="1")


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
        test_manage_place()
        test_categories()
        test_search_guards()
        test_trip_date_shift()
        test_delete_cascade()
    finally:
        shutil.rmtree(_SCRATCH, ignore_errors=True)

    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
