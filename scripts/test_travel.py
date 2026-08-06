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

from tools.travel import (  # noqa: E402
    manage_destination, manage_itinerary, manage_place, manage_trip, manage_wishlist,
    query_travel_db,
)

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


def test_manage_destination() -> None:
    section("manage_destination — the thing trips and places hang off")

    check("an empty list says so", call(manage_destination, action="list"),
          contains="no destinations yet")

    check("create needs a name", call(manage_destination, action="create"),
          contains="needs a name")

    check("create needs a timezone, and says why",
          call(manage_destination, action="create", name="Nowhere"),
          contains=["needs a timezone", "every local time"])

    check("an unknown timezone is refused",
          call(manage_destination, action="create", name="Nowhere",
               timezone="Mars/Olympus"),
          contains="unknown timezone")

    check("an unknown kind lists the real ones",
          call(manage_destination, action="create", name="Nowhere",
               timezone="Asia/Tokyo", kind="planet"),
          contains=["unknown kind", "city", "country"])

    check("creating reports the timezone it will be read in",
          call(manage_destination, action="create", name="Alpha City",
               timezone="Asia/Tokyo", kind="city", country="Alphaland"),
          contains=["created destination", "Asia/Tokyo"])

    check("the same name again is refused, not forked",
          call(manage_destination, action="create", name="Alpha City",
               timezone="Asia/Tokyo"),
          contains="already exists")

    check("and case does not fork it either",
          call(manage_destination, action="create", name="alpha city",
               timezone="Asia/Tokyo"),
          contains="already exists")

    for nm, tz in (("Beta Town", "Europe/Lisbon"), ("Shift City", "Europe/Lisbon"),
                   ("Itinerary City", "Europe/Lisbon"), ("Undated Town", "Europe/Lisbon"),
                   ("Wishville", "Europe/Lisbon"), ("Otherville", "Europe/Lisbon"),
                   ("Tileburg", "Europe/Lisbon"), ("Placeville", "Europe/Lisbon"),
                   ("Strayville", "Europe/Lisbon")):
        call(manage_destination, action="create", name=nm, timezone=tz)

    check("list shows what depends on each", call(manage_destination, action="list"),
          contains=["Alpha City", "0 place(s), 0 trip(s)"])

    check("an unknown name is answered with the real ones",
          call(manage_destination, action="update", name="Nowhere", timezone="Asia/Tokyo"),
          contains=["no destination", "Alpha City"])

    check("update says it reaches every trip and place",
          call(manage_destination, action="update", name="Strayville",
               country="Strayland", kind="city"),
          contains=["updated strayville", "every trip and place"])

    check("renaming onto an existing name is refused, with the way out",
          call(manage_destination, action="update", name="Strayville",
               new_name="Alpha City"),
          contains=["already exists", "merge into it"])

    check("update with no fields is a no-op",
          call(manage_destination, action="update", name="Strayville"),
          contains="nothing to update")

    check("a destination cannot be merged into itself",
          call(manage_destination, action="merge", name="Strayville", into="Strayville"),
          contains="into itself")

    check("merge moves what depended on it and removes the row",
          call(manage_destination, action="merge", name="Strayville", into="Alpha City"),
          contains=["merged strayville into alpha city", "no longer exists"])

    check("and it is really gone", call(manage_destination, action="list"),
          missing="Strayville")

    check("unknown action lists the real actions",
          call(manage_destination, action="frobnicate"),
          contains=["unknown action", "merge"])


def test_manage_trip() -> None:
    section("manage_trip — creation and the current-trip pointer")

    check("empty list reads as empty", call(manage_trip, action="list"),
          contains="no trips yet")

    check("create claims current when nothing holds it",
          call(manage_trip, action="create", trip_id="alpha", destination="Alpha City",
               start_date="2026-10-10", end_date="2026-10-15"),
          contains=["created", "now the current trip"])

    check("second create does NOT steal the pointer",
          call(manage_trip, action="create", trip_id="beta", destination="Beta Town"),
          contains="created", missing="now the current trip")

    check("list marks exactly one current",
          call(manage_trip, action="list"), contains=["alpha", "beta", "CURRENT"])

    check("undated trip says so", call(manage_trip, action="list"),
          contains="no dates")

    check("duplicate id refused",
          call(manage_trip, action="create", trip_id="alpha", destination="Alpha City"),
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

    check("an unknown destination lists the real ones",
          call(manage_trip, action="update", trip_id="beta", destination="Nowhere"),
          contains=["no destination", "Alpha City"])

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
    conn.execute(
        "INSERT INTO places(title, destination_id) "
        "SELECT 'Somewhere', destination_id FROM destinations WHERE name='Shift City'"
    )
    conn.execute(
        "INSERT INTO wishlist(destination_id, place_id) "
        "SELECT t.destination_id, 1 FROM trips t WHERE t.trip_id = 'shift'"
    )
    conn.commit()
    conn.close()

    # The confirmation UI needs a channel, which this script has no business
    # standing up — so the guarded body is called directly. What the button
    # protects is tested here; that it IS behind a button is read off the code.
    from tools.travel.trips import _exec_delete_trip

    check("delete reports what it removed, and what it spared",
          _exec_delete_trip("shift"),
          contains=["deleted shift", "3 scheduled", "wishlist is untouched"])

    check("the trip is gone",
          call(query_travel_db, sql="SELECT COUNT(*) AS n FROM trips WHERE trip_id='shift'"),
          contains="0")

    check("its scheduled rows went with it",
          call(query_travel_db,
               sql="SELECT COUNT(*) AS n FROM itinerary WHERE trip_id='shift'"),
          contains="0")

    check("the saved place survived",
          call(query_travel_db, sql="SELECT title FROM places"),
          contains="Somewhere")

    check("and so did the wishlist entry, which belongs to the destination",
          call(query_travel_db, sql="SELECT COUNT(*) AS n FROM wishlist WHERE place_id=1"),
          contains="1")


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
        "addressComponents": [
            {"longText": "Rua Augusta", "types": ["route"]},
            {"longText": "Lisboa", "types": ["locality", "political"]},
            {"longText": "Portugal", "types": ["country", "political"]},
        ],
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
        "addressComponents": [
            {"longText": "Lisboa", "types": ["locality", "political"]},
            {"longText": "Portugal", "types": ["country", "political"]},
        ],
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
          call(manage_place, action="save", title="X", category="delicious",
               destination="Beta Town"),
          contains=["unknown category", "restaurant", "other"])


def _TYPE_VALUES():
    from tools.travel.places import _TYPE_TO_CATEGORY, _SUFFIX_RULES

    return list(_TYPE_TO_CATEGORY.values()) + [b for _, b in _SUFFIX_RULES]


def test_locality() -> None:
    """Where a place's city comes from, and what stands in when Google has no
    `locality` — the UK uses postal_town, and some places give neither."""
    from tools.travel.places import _locality_of

    section("manage_place — locality, and its stand-ins")

    cases = [
        ([{"longText": "Lisboa", "types": ["locality"]},
          {"longText": "Portugal", "types": ["country"]}],            ("Lisboa", "Portugal")),
        # No locality: the UK files cities under postal_town.
        ([{"longText": "London", "types": ["postal_town"]},
          {"longText": "United Kingdom", "types": ["country"]}],      ("London", "United Kingdom")),
        # Neither: fall back to the county-level component.
        ([{"longText": "Kerry", "types": ["administrative_area_level_2"]},
          {"longText": "Ireland", "types": ["country"]}],             ("Kerry", "Ireland")),
        # locality wins over the stand-ins when both are present.
        ([{"longText": "Real City", "types": ["locality"]},
          {"longText": "Fallback", "types": ["postal_town"]}],        ("Real City", None)),
        ([], (None, None)),
    ]
    wrong = [(p, _locality_of({"addressComponents": p}), want)
             for p, want in cases if _locality_of({"addressComponents": p}) != want]
    check(f"{len(cases)} component shapes", "ok" if not wrong else f"wrong: {wrong}",
          contains="ok")

    check("a place with no components at all is not an error",
          str(_locality_of({})), contains="(None, None)")


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
                   title="Cafe Central — Baixa", destination="Beta Town"),
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
                   title="Cafe Central — Baixa", destination="Beta Town"),
              contains=["already saved", "nothing duplicated"])

        check("still exactly one row for it",
              call(query_travel_db,
                   sql="SELECT COUNT(*) AS n FROM places WHERE google_place_id='ChIJ_branch_one'"),
              contains="1")

        check("the other branch is a separate place",
              call(manage_place, action="save", google_place_id="ChIJ_branch_two",
                   title="Cafe Central — Alfama", destination="Beta Town"),
              contains="saved place")
        check("the locality and country are kept",
              call(query_travel_db,
                   sql="SELECT city, country FROM places "
                       "WHERE google_place_id='ChIJ_branch_one'"),
              contains=["Lisboa", "Portugal"])

        check("the full types array survives, so nothing finer is lost",
              call(query_travel_db,
                   sql="SELECT google_types FROM places "
                       "WHERE google_place_id='ChIJ_branch_two'"),
              contains=["italian_restaurant", "restaurant", "food"])
    finally:
        places_mod._search_places = real_search

    check("a hand-added place needs no google id",
          call(manage_place, action="save", title="Ana's kitchen",
               address="a friend's flat", destination="Beta Town"),
          contains="saved place")

    check("saving without a destination asks which one, and lists them",
          call(manage_place, action="save", title="Floating"),
          contains=["which destination", "Alpha City"])

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
    conn.execute(
        "INSERT INTO wishlist(destination_id, place_id) "
        "SELECT destination_id, 1 FROM places WHERE place_id = 1"
    )
    conn.commit()
    conn.close()

    check("a referenced place refuses to delete, and says by what",
          call(manage_place, action="delete", place_id=1),
          contains=["still used by", "1 wishlist"])

    check("and it is still there",
          call(query_travel_db, sql="SELECT COUNT(*) AS n FROM places WHERE place_id=1"),
          contains="1")


# ---------------------------------------------------------------------------
# manage_wishlist
# ---------------------------------------------------------------------------


def test_manage_wishlist() -> None:
    section("manage_wishlist — the list belongs to the destination")

    check("a list can be asked for by destination, with no trip at all",
          call(manage_wishlist, action="list", destination="Wishville"),
          contains="nothing on its wishlist")

    check("with neither destination nor trip it asks, and lists them",
          call(manage_wishlist, action="list"),
          contains=["which destination", "Wishville"])

    check("an unknown destination is answered with the real ones",
          call(manage_wishlist, action="list", destination="Nowhere"),
          contains=["no destination", "Wishville"])

    check("add by place_id needs no trip",
          call(manage_wishlist, action="add", destination="Beta Town", place_id=2,
               notes="go before 11", priority=1),
          contains="wishlist")

    check("add with nothing to identify anything is refused",
          call(manage_wishlist, action="add", destination="Wishville"),
          contains=["needs either a place_id", "title"])

    check("an unknown place_id lists the saved places",
          call(manage_wishlist, action="add", destination="Wishville", place_id=999),
          contains="no place with id 999")

    check("an intention with no place at all is allowed",
          call(manage_wishlist, action="add", destination="Wishville",
               title="somewhere with a view", city="Old Town", priority=2),
          contains="wishlist")

    check("and the same intention twice is refused, which NULLs alone would not stop",
          call(manage_wishlist, action="add", destination="Wishville",
               title="somewhere with a view"),
          contains="UNIQUE")

    section("manage_wishlist — a trip reaches its list through its destination")

    check("asking by trip finds the destination's list",
          call(manage_wishlist, action="list", trip_id="beta"),
          contains="cafe central")

    check("which is the same list the destination itself gives",
          call(manage_wishlist, action="list", destination="Beta Town"),
          contains="cafe central")

    check("grouped by city, then category",
          call(manage_wishlist, action="list", destination="Beta Town"),
          contains=["LISBOA", "cafe"])

    check("an override groups it where the owner would look",
          call(manage_wishlist, action="update", wishlist_id=1, city="Baixa"),
          contains="city → Baixa")
    check("and the listing follows",
          call(manage_wishlist, action="list", destination="Beta Town"),
          contains="BAIXA")

    section("manage_wishlist — updating, clearing, and going")

    check("notes and priority change",
          call(manage_wishlist, action="update", wishlist_id=1,
               notes="actually go at dawn", priority=2),
          contains=["notes → actually go at dawn", "priority → 2"])

    check("an out-of-range priority is refused",
          call(manage_wishlist, action="update", wishlist_id=1, priority=9),
          contains="1..5")

    check("an empty string clears a field",
          call(manage_wishlist, action="update", wishlist_id=1, notes=""),
          contains="notes cleared")

    check("update with nothing given is a no-op",
          call(manage_wishlist, action="update", wishlist_id=1),
          contains="nothing to change")

    check("a bad done_at is refused",
          call(manage_wishlist, action="update", wishlist_id=1, done_at="last tuesday"),
          contains="yyyy-mm-dd")

    check("marking it done says so",
          call(manage_wishlist, action="update", wishlist_id=1, done_at="2027-05-24"),
          contains="marked done")

    # Both fixture places are called "Cafe Central"; the branch is what separates
    # the entry marked done from the one that is not.
    check("a done item drops out of the list",
          call(manage_wishlist, action="list", destination="Beta Town"),
          contains="Alfama", missing="Baixa")

    check("but is still there when asked for",
          call(manage_wishlist, action="list", destination="Beta Town", include_done=True),
          contains=["Baixa", "(done)"])

    check("and it can be retracted",
          call(manage_wishlist, action="update", wishlist_id=1, done_at=""),
          contains="no longer marked done")

    check("re-adding an already-listed place updates rather than raising",
          call(manage_wishlist, action="add", destination="Beta Town", place_id=1,
               notes="new note"),
          contains=["already on the list", "notes"])

    section("manage_wishlist — removal, and what survives it")

    check("remove needs the id from the listing",
          call(manage_wishlist, action="remove"), contains="wishlist_id is required")

    check("an unknown id says so",
          call(manage_wishlist, action="remove", wishlist_id=999),
          contains="no wishlist entry 999")

    check("remove says the place is kept, and nudges toward done_at",
          call(manage_wishlist, action="remove", wishlist_id=1),
          contains=["saved place is kept", "done_at"])

    check("the place itself survived",
          call(query_travel_db, sql="SELECT title FROM places WHERE place_id=1"),
          contains="cafe central")

    check("unknown action lists the real actions",
          call(manage_wishlist, action="frobnicate"),
          contains=["unknown action", "add", "remove"])


# ---------------------------------------------------------------------------
# manage_itinerary
# ---------------------------------------------------------------------------


def test_manage_itinerary() -> None:
    section("manage_itinerary — scheduling needs a dated trip")

    call(manage_trip, action="create", trip_id="it0", destination="Undated Town")
    check("an undated trip refuses, and says how to fix it",
          call(manage_itinerary, action="schedule", trip_id="it0",
               title="Anything", date="2027-01-01"),
          contains=["no dates yet", "manage_trip", "wishlist"])

    call(manage_trip, action="create", trip_id="it", destination="Itinerary City",
         start_date="2027-03-10", end_date="2027-03-14")

    check("an empty schedule says so", call(manage_itinerary, action="list", trip_id="it"),
          contains="nothing scheduled")

    check("schedule needs a date",
          call(manage_itinerary, action="schedule", trip_id="it", title="X"),
          contains="needs a date")

    check("schedule needs something to schedule",
          call(manage_itinerary, action="schedule", trip_id="it", date="2027-03-11"),
          contains="needs something to schedule")

    check("a bad time is refused",
          call(manage_itinerary, action="schedule", trip_id="it", title="X",
               date="2027-03-11", start_time="9am"),
          contains="24-hour hh:mm")

    check("an unknown item_type lists the real ones",
          call(manage_itinerary, action="schedule", trip_id="it", title="X",
               date="2027-03-11", item_type="reservation"),
          contains=["unknown item_type", "lodging", "transit"])

    check("a transit leg with no title is refused",
          call(manage_itinerary, action="schedule", trip_id="it",
               date="2027-03-11", item_type="transit"),
          contains="needs a title")

    section("manage_itinerary — day numbers derive, edges are flagged")

    check("scheduling reports the derived day number",
          call(manage_itinerary, action="schedule", trip_id="it", place_id=1,
               date="2027-03-11", start_time="13:00", end_time="14:30"),
          contains=["scheduled", "day 2", "13:00"])

    check("a night-before item is accepted and flagged, not refused",
          call(manage_itinerary, action="schedule", trip_id="it", title="Red-eye out",
               item_type="transit", date="2027-03-09", start_time="23:40",
               origin="Home", destination_loc="Airport"),
          contains=["scheduled", "outside the trip window", "edge day"])

    check("a booking says it will not be moved",
          call(manage_itinerary, action="schedule", trip_id="it", title="Hotel Splendid",
               item_type="lodging", date="2027-03-10", end_date="2027-03-14",
               confirmation_code="BK-1"),
          contains=["scheduled", "not be moved"])

    check("a transit leg is not turned into a saved place",
          call(query_travel_db, sql="SELECT COUNT(*) AS n FROM places WHERE title='Red-eye out'"),
          contains="0")

    out = call(manage_itinerary, action="list", trip_id="it")
    check("stays are lifted out of the day list", out, contains=["STAYS", "Hotel Splendid"])
    check("the stay shows its span", out, contains="2027-03-10 → 2027-03-14")
    check("days are numbered from the trip's start", out, contains="Day 2 · 2027-03-11")
    check("a pre-trip day is labelled, not numbered", out, contains="before day 1")
    check("transit shows its endpoints", out, contains="Home → Airport")

    section("manage_itinerary — the wishlist is never consumed")

    call(manage_wishlist, action="add", trip_id="it", place_id=2)
    _wl_count = ("SELECT COUNT(*) AS n FROM wishlist w JOIN trips t "
                 "ON t.destination_id = w.destination_id "
                 "WHERE t.trip_id='it' AND w.place_id=2")
    before = call(query_travel_db, sql=_wl_count)
    call(manage_itinerary, action="schedule", trip_id="it", place_id=2, date="2027-03-12")
    check("scheduling a wishlisted place leaves the wishlist row alone",
          call(query_travel_db, sql=_wl_count), contains="1")
    check("(it was there before too)", before, contains="1")

    check("the same place can be scheduled twice",
          call(manage_itinerary, action="schedule", trip_id="it", place_id=2,
               date="2027-03-13", start_time="19:00"),
          contains="scheduled")
    check("two entries for one place",
          call(query_travel_db,
               sql="SELECT COUNT(*) AS n FROM itinerary WHERE trip_id='it' AND place_id=2"),
          contains="2")

    section("manage_itinerary — reschedule, unschedule, remove")

    check("a wrong entry_id shows the itinerary",
          call(manage_itinerary, action="reschedule", trip_id="it", entry_id=9999,
               date="2027-03-12"),
          contains=["no entry 9999", "itinerary"])

    check("reschedule with nothing to change is a no-op",
          call(manage_itinerary, action="reschedule", trip_id="it", entry_id=1),
          contains="nothing to change")

    check("moving an entry reports what moved",
          call(manage_itinerary, action="reschedule", trip_id="it", entry_id=1,
               date="2027-03-13", start_time="18:00"),
          contains=["moved", "2027-03-13", "18:00"])

    check("moving something outside the window flags it",
          call(manage_itinerary, action="reschedule", trip_id="it", entry_id=1,
               date="2027-04-01"),
          contains="outside the trip window")

    check("removing something not on the list says nothing about one",
          call(manage_itinerary, action="remove", trip_id="it", entry_id=1),
          contains="removed", missing="wishlist")

    check("and it really is there",
          call(query_travel_db,
               sql="SELECT COUNT(*) AS n FROM wishlist w JOIN trips t "
                   "ON t.destination_id = w.destination_id "
                   "WHERE t.trip_id='it' AND w.place_id=1"),
          contains="1")

    check("removing a note says nothing about a wishlist it never had",
          call(manage_itinerary, action="remove", trip_id="it", entry_id=2),
          contains="removed", missing="wishlist")

    check("removing a wishlisted place says the list still has it",
          call(manage_itinerary, action="remove", trip_id="it", entry_id=4),
          contains=["removed", "still on the wishlist"])

    check("the place it pointed at is still wishlisted",
          call(query_travel_db, sql=_wl_count), contains="1")

    check("unknown action lists the real actions",
          call(manage_itinerary, action="frobnicate", trip_id="it"),
          contains=["unknown action", "schedule", "reschedule"])

    section("manage_itinerary — items that run past midnight")

    check("an overnight is inferred, not refused",
          call(manage_itinerary, action="schedule", trip_id="it", title="Night bus",
               item_type="transit", date="2027-03-11", start_time="22:00",
               end_time="06:00", origin="A", destination_loc="B"),
          contains=["scheduled", "next day", "2027-03-12"])

    check("the rollover is stored as the arrival date",
          call(query_travel_db,
               sql="SELECT start_date, end_date FROM itinerary WHERE title='Night bus'"),
          contains=["2027-03-11", "2027-03-12"])

    check("it renders with a +1 rather than reading backwards",
          call(manage_itinerary, action="list", trip_id="it"),
          contains="22:00-06:00+1")

    check("the rollover applies to any item type, not just transit",
          call(manage_itinerary, action="schedule", trip_id="it", title="Late bar",
               date="2027-03-11", start_time="22:30", end_time="01:00"),
          contains="next day")

    check("an explicit end_date is respected, not overwritten",
          call(manage_itinerary, action="schedule", trip_id="it", title="Long haul",
               item_type="transit", date="2027-03-11", end_date="2027-03-13",
               start_time="22:00", end_time="06:00"),
          contains="scheduled", missing="next day")

    ov = call(query_travel_db,
              sql="SELECT entry_id FROM itinerary WHERE title='Night bus'").split("\n")[1]
    check("moving it carries its arrival along",
          call(manage_itinerary, action="reschedule", trip_id="it",
               entry_id=int(ov), date="2027-03-13"),
          contains=["moved", "2027-03-14"])


# ---------------------------------------------------------------------------
# the travel app surface (gateway/apps/travel.py)
# ---------------------------------------------------------------------------


def test_tile() -> None:
    """The tile payload. Deterministic dispatch, no model — so this is the whole
    contract the app client will be written against."""
    import asyncio

    from gateway.apps import AppError, dispatch

    def tile(**params):
        return asyncio.run(dispatch("travel", "tile", params))

    def fails(**params):
        try:
            asyncio.run(dispatch("travel", "tile", params))
            return "NO ERROR"
        except AppError as e:
            return f"{e.code}: {e}"

    # This test owns its data: borrowing another test's leftovers made these
    # assertions depend on what that test happened to delete.
    call(manage_trip, action="create", trip_id="tile", destination="Tileburg",
         start_date="2027-06-10", end_date="2027-06-14", timezone="Europe/Lisbon")
    call(manage_itinerary, action="schedule", trip_id="tile", title="Night train in",
         item_type="transit", date="2027-06-09", start_time="23:10",
         origin="Home", destination_loc="Tileburg Centraal")
    call(manage_itinerary, action="schedule", trip_id="tile", title="Hotel Tile",
         item_type="lodging", date="2027-06-10", end_date="2027-06-14",
         confirmation_code="BK-TILE")
    call(manage_itinerary, action="schedule", trip_id="tile", place_id=1,
         date="2027-06-11", start_time="13:00", end_time="14:30")
    call(manage_itinerary, action="schedule", trip_id="tile", title="Nap",
         item_type="note", date="2027-06-11")
    call(manage_wishlist, action="add", trip_id="tile", place_id=2)
    call(manage_wishlist, action="add", trip_id="tile", place_id=1, notes="go at dawn")

    section("travel tile — addressing a trip")

    check("an unknown trip is not_found", fails(trip_id="nope"),
          contains="not_found")

    check("an undeclared param is refused before any handler runs",
          fails(path="../../secrets/.env"), contains="invalid_request")

    d = tile(trip_id="tile")
    check("the addressed trip comes back", str(d["trip"]["trip_id"]), contains="tile")
    check("its timezone and position are resolved",
          f"{d['trip']['position']} {d['trip']['timezone']}",
          contains=["before", "Europe/Lisbon"])

    section("travel tile — the day strip")

    dates = [x["date"] for x in d["days"]]
    check("every day of the trip is present, including empty ones",
          str(len(dates)), contains="6")   # 5 in-window + 1 edge day
    check("empty middle days are not skipped",
          str([x["date"] for x in d["days"] if not x["items"]]),
          contains=["2027-06-12", "2027-06-13", "2027-06-14"])
    check("an out-of-window day is included and flagged",
          str([x["outside_window"] for x in d["days"] if x["date"] == "2027-06-09"]),
          contains="True")
    check("in-window days are not flagged",
          str([x["outside_window"] for x in d["days"] if x["date"] == "2027-06-11"]),
          contains="False")
    check("day numbers count from the trip's start",
          str([x["day_number"] for x in d["days"] if x["date"] == "2027-06-11"]),
          contains="2")
    check("a pre-trip day gets a number below 1 rather than a fake one",
          str([x["day_number"] for x in d["days"] if x["date"] == "2027-06-09"]),
          contains="0")
    check("days are sorted", str(dates == sorted(dates)), contains="true")

    section("travel tile — stays, items, wishlist")

    check("a stay is lifted out of the day list",
          str([x["title"] for x in d["lodging"]]), contains="Hotel Tile")
    check("and does not also appear inside a day",
          str([i["item_type"] for day in d["days"] for i in day["items"]]),
          missing="lodging")
    check("a stay carries its span and its booking",
          f"{d['lodging'][0]['end_date']} {d['lodging'][0]['confirmation_code']}",
          contains=["2027-06-14", "BK-TILE"])

    items = [i for day in d["days"] for i in day["items"]]
    check("a transit leg has no place object, and keeps its endpoints",
          str([(i["place"], i["origin"], i["destination_loc"])
               for i in items if i["item_type"] == "transit"]),
          contains=["None", "Tileburg Centraal"])
    check("a place-backed item carries its place",
          str([i["place"]["title"] for i in items if i["place"]]),
          contains="cafe central")
    check("every item has a title the client can render",
          "ok" if all(i["title"] for i in items) else "missing", contains="ok")
    check("an untimed item still appears, sorted after timed ones",
          str([i["title"] for day in d["days"] if day["date"] == "2027-06-11"
               for i in day["items"]]),
          contains="Nap")

    check("the wishlist is grouped, in the vocabulary's own order",
          str([g["category"] for g in d["wishlist"]]),
          contains="['restaurant', 'cafe']")
    check("wishlist items carry their note and place details",
          str(d["wishlist"][1]["items"][0]),
          contains=["go at dawn", "Coffee Shop"])

    section("travel tile — empty states are not errors")

    d0 = tile(trip_id="it0")
    check("an undated trip returns a trip with no days, not an error",
          f"{d0['trip']['trip_id']} days={len(d0['days'])} pos={d0['trip']['position']}",
          contains=["it0", "days=0", "undated"])


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
        test_manage_destination()
        test_manage_trip()
        test_manage_place()
        test_categories()
        test_locality()
        test_search_guards()
        test_manage_wishlist()
        test_manage_itinerary()
        test_tile()
        test_trip_date_shift()
        test_delete_cascade()
    finally:
        shutil.rmtree(_SCRATCH, ignore_errors=True)

    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
