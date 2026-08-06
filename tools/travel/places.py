"""The places table, and the Google Places lookup that populates it.

A place is a fact about the world and carries no trip, so this module never
touches trip_id: scoping is the wishlist's and the itinerary's business.
"""

import json
import os
import sqlite3

import requests
from langchain_core.tools import tool

from tools.registry import tool_register
from tools.travel._db import TravelError, _get_db

_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

# THE FIELD MASK IS THE PRICE. Text Search bills by SKU, and the SKU is chosen
# by the fields named here: the highest tier named wins. displayName and
# googleMapsUri put this at Pro (5,000 free lookups/month, then $32/1000), which
# is why primaryType and its label are free to include. Adding `rating` or
# `reviews` drops the free allowance to 1,000 and raises the rate — silently,
# with no error anywhere.
#
# So this is a constant and must stay one. Never build it from caller input, and
# treat adding a field as a deliberate pricing change rather than a code tweak.
_FIELD_MASK = ",".join((
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.location",
    "places.types",
    "places.primaryType",
    "places.primaryTypeDisplayName",
    "places.googleMapsUri",
    "places.addressComponents",
))

# Enough to choose between branches of a chain without turning the reply into a
# directory. The whole reason lookup is its own tool is that a human or the model
# picks from these.
_MAX_CANDIDATES = 5

_TIMEOUT_S = 15

# What the last searches returned, keyed by Google's id, so `save` can keep the
# coordinates and type without the model having to relay them through its own
# arguments — fields it would drop or garble, and which a second lookup would
# bill for again. Lost on restart, which costs nothing: save then stores exactly
# what it was passed, the same as a place added by hand.
_SEARCH_CACHE: dict[str, dict] = {}
_SEARCH_CACHE_MAX = 200

# Loop guards. The failure being defended against is a model retrying inside one
# turn, not steady volume — so both guards are per-turn, and the account-level
# ceiling stays where it belongs, as a quota in the Cloud console.
#
# Repeating a query costs nothing: a loop almost always asks the same thing
# again, so an identical search inside one turn is replayed from memory without
# a request. Only genuinely different searches count toward the cap.
_MAX_SEARCHES_PER_TURN = 5
_TURN_SEARCHES: dict[str, dict] = {}
_TURN_SEARCHES_MAX = 20


def _search_places(query: str, near: str) -> list[dict]:
    """Text Search, returning at most _MAX_CANDIDATES raw candidate dicts.

    Every failure here raises TravelError carrying the API's own words: a dead
    or unbilled key is something the owner has to fix, and paraphrasing it would
    hide which of the several possible causes it actually was.
    """
    key = os.environ.get("GOOGLE_PLACES_API_KEY", "")
    if not key:
        raise TravelError(
            "Place lookup is unavailable: GOOGLE_PLACES_API_KEY is not configured. "
            "You can still save a place by hand with action='save' if you know its "
            "name and address."
        )
    text = f"{query} {near}".strip() if near.strip() else query.strip()
    try:
        resp = requests.post(
            _SEARCH_URL,
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": key,
                "X-Goog-FieldMask": _FIELD_MASK,
            },
            json={"textQuery": text, "maxResultCount": _MAX_CANDIDATES},
            timeout=_TIMEOUT_S,
        )
    except requests.RequestException as e:
        raise TravelError(f"Place lookup failed to reach Google ({type(e).__name__}: {e}).")
    if resp.status_code != 200:
        raise TravelError(
            f"Place lookup rejected by Google (HTTP {resp.status_code}): {resp.text[:400]}"
        )
    return (resp.json() or {}).get("places", [])[:_MAX_CANDIDATES]


def _flatten(candidate: dict) -> dict:
    """One Places result reduced to the columns this database keeps.

    Nothing here is mapped to a display category: that vocabulary is still open,
    and deciding it at write time would bake a guess into every row saved before
    the real answer exists. What Google said is kept instead, in full.
    """
    loc = candidate.get("location") or {}
    types = candidate.get("types") or []
    city, country = _locality_of(candidate)
    # primaryType is Google's own single answer to "what is this"; types[0] only
    # happens to be it most of the time. Falling back to types[0] keeps a place
    # classified when Google declines to name a primary one.
    primary = (candidate.get("primaryType") or "").strip() or (types[0] if types else None)
    label = ((candidate.get("primaryTypeDisplayName") or {}).get("text") or "").strip()
    return {
        "google_place_id": candidate.get("id") or None,
        "title": ((candidate.get("displayName") or {}).get("text") or "").strip(),
        "address": (candidate.get("formattedAddress") or "").strip() or None,
        "maps_url": (candidate.get("googleMapsUri") or "").strip() or None,
        "lat": loc.get("latitude"),
        "lng": loc.get("longitude"),
        "google_type": primary,
        "google_type_label": label or None,
        # The whole array, verbatim. A place is 'italian_restaurant' AND
        # 'restaurant' AND 'food', and which of those matters is a question we
        # have not answered yet — so none of it is thrown away at write time,
        # when re-fetching it would mean paying for the lookup twice.
        "google_types": json.dumps(types) if types else None,
        # What Google calls the surrounding area. Evidence about a place, never
        # used to decide which destination it belongs to — for a Tokyo venue the
        # locality is a ward (Shibuya, Shinjuku), so choosing on it would file
        # one city under several names.
        "city": city,
        "country": country,
    }


def _locality_of(candidate: dict) -> tuple[str | None, str | None]:
    """(city, country) from addressComponents, or (None, None).

    `locality` is the ordinary city component. `postal_town` is its stand-in
    where a country doesn't use locality (the UK, mainly), and
    administrative_area_level_2 catches the rest — tried in that order, so the
    most specific answer available wins rather than the first one present.
    """
    parts = candidate.get("addressComponents") or []

    def _first(*wanted: str) -> str | None:
        for want in wanted:
            for c in parts:
                if want in (c.get("types") or []):
                    return (c.get("longText") or "").strip() or None
        return None

    return (
        _first("locality", "postal_town", "administrative_area_level_2"),
        _first("country"),
    )


# The display vocabulary — what the wishlist groups by and the tile draws icons
# for. Closed, because an open one accumulates "Food"/"food"/"restaurants" as
# separate headings and nothing notices.
#
# Google's own 19 doc categories are the skeleton, with Food and Drink split
# five ways: on a trip, a cafe and a restaurant are different errands, which is
# the one place Google's grouping is too coarse to be useful. Everything finer
# survives in google_type / google_type_label / google_types, so re-cutting
# these buckets later is a migration, not a re-fetch.
CATEGORIES = (
    "restaurant", "cafe", "dessert", "bar", "market",
    "sights", "outdoors", "shopping", "lodging", "transit", "other",
)

# Exact type -> bucket. Only types worth naming individually; the suffix rules
# below catch the long tail, including cuisines Google has not invented yet.
_TYPE_TO_CATEGORY = {
    # eating
    "restaurant": "restaurant", "fine_dining_restaurant": "restaurant",
    "fast_food_restaurant": "restaurant", "food_court": "restaurant",
    "steak_house": "restaurant", "buffet_restaurant": "restaurant",
    # drinking coffee
    "cafe": "cafe", "coffee_shop": "cafe", "cafeteria": "cafe",
    "tea_house": "cafe", "juice_shop": "cafe", "bubble_tea_shop": "cafe",
    "breakfast_restaurant": "cafe", "brunch_restaurant": "cafe",
    "sandwich_shop": "restaurant", "bagel_shop": "restaurant",
    "kebab_shop": "restaurant", "pizza_shop": "restaurant",
    "acai_shop": "dessert",
    # sweet things
    "bakery": "dessert", "pastry_shop": "dessert", "dessert_shop": "dessert",
    "dessert_restaurant": "dessert", "ice_cream_shop": "dessert",
    "chocolate_shop": "dessert", "chocolate_factory": "dessert",
    "candy_store": "dessert", "donut_shop": "dessert", "confectionery": "dessert",
    # drinking otherwise
    "bar": "bar", "pub": "bar", "wine_bar": "bar", "bar_and_grill": "bar",
    "night_club": "bar", "brewery": "bar", "distillery": "bar",
    # buying food
    "market": "market", "farmers_market": "market", "supermarket": "market",
    "grocery_store": "market", "food_store": "market",
    # things to look at
    "museum": "sights", "art_gallery": "sights", "tourist_attraction": "sights",
    "historical_landmark": "sights", "historical_place": "sights",
    "monument": "sights", "cultural_landmark": "sights", "cultural_center": "sights",
    "church": "sights", "mosque": "sights", "synagogue": "sights",
    "hindu_temple": "sights", "place_of_worship": "sights",
    "performing_arts_theater": "sights", "opera_house": "sights",
    "concert_hall": "sights", "observation_deck": "sights", "planetarium": "sights",
    "zoo": "sights", "aquarium": "sights", "amusement_park": "sights",
    "castle": "sights", "palace": "sights",
    # things to be outside in
    "park": "outdoors", "national_park": "outdoors", "state_park": "outdoors",
    "garden": "outdoors", "botanical_garden": "outdoors", "beach": "outdoors",
    "hiking_area": "outdoors", "wildlife_park": "outdoors", "campground": "outdoors",
    "natural_feature": "outdoors", "marina": "outdoors",
    # sleeping
    "hotel": "lodging", "lodging": "lodging", "motel": "lodging",
    "hostel": "lodging", "guest_house": "lodging", "resort_hotel": "lodging",
    "bed_and_breakfast": "lodging", "inn": "lodging", "cottage": "lodging",
    # moving
    "airport": "transit", "international_airport": "transit",
    "transit_station": "transit", "bus_stop": "transit", "ferry_terminal": "transit",
    "taxi_stand": "transit", "car_rental": "transit", "parking": "transit",
    # buying otherwise
    "shopping_mall": "shopping", "market_place": "shopping",
}

# Checked in order, after the exact table misses. `_restaurant` alone covers
# every cuisine Google lists and every one it adds later, which is why the table
# above does not try to enumerate them.
_SUFFIX_RULES = (
    ("_restaurant", "restaurant"),
    ("_station", "transit"),
    ("_store", "shopping"),
)
# Deliberately no `_shop` rule: it reads as retail but Google uses it for food
# too (sandwich_shop, coffee_shop, ice_cream_shop). The food ones are named
# above; anything else ending in _shop is left undecided rather than confidently
# filed under shopping.


def _categorize(google_type: str | None) -> str | None:
    """Bucket a Google type, or None when nothing here recognises it.

    None is deliberate rather than 'other': it means *undecided*, so the model
    can supply a category for a place Google could not classify, and a genuinely
    miscellaneous place can still be labelled 'other' on purpose. Collapsing the
    two would make "we don't know" indistinguishable from "we looked and it's
    miscellaneous", and the first is the one worth revisiting.
    """
    if not google_type:
        return None
    t = google_type.strip().lower()
    if t in _TYPE_TO_CATEGORY:
        return _TYPE_TO_CATEGORY[t]
    for suffix, bucket in _SUFFIX_RULES:
        if t.endswith(suffix):
            return bucket
    return None


def _validate_category(category: str) -> str | None:
    c = (category or "").strip().lower()
    if not c:
        return None
    if c not in CATEGORIES:
        raise TravelError(
            f"Unknown category {c!r}. Use one of: {', '.join(CATEGORIES)}."
        )
    return c


def _turn_state() -> dict:
    """Per-turn search bookkeeping, keyed by the turn id the agent binds.

    Outside a turn — a script, a REPL — every call shares one bucket, which is
    the conservative reading: a runaway loop there is capped too.

    Cleared wholesale past a small cap rather than evicted per entry: it holds
    at most a handful of live turns, and losing it only resets a counter.
    """
    from observability import telemetry

    turn = telemetry.TURN_ID.get() or "no-turn"
    if turn not in _TURN_SEARCHES and len(_TURN_SEARCHES) >= _TURN_SEARCHES_MAX:
        _TURN_SEARCHES.clear()
    return _TURN_SEARCHES.setdefault(turn, {"n": 0, "queries": {}})


def _search_guarded(query: str, near: str) -> list[dict]:
    """One lookup, subject to the two per-turn guards.

    The attempt is counted before the request, not after, so a loop that keeps
    failing — a dead key, a network fault — is capped exactly like a loop that
    keeps succeeding. Counting only successes would leave the noisiest case
    unbounded.
    """
    state = _turn_state()
    key = (query.strip().lower(), near.strip().lower())
    if key in state["queries"]:
        return state["queries"][key]
    if state["n"] >= _MAX_SEARCHES_PER_TURN:
        raise TravelError(
            f"Already searched {state['n']} times in this turn, which is the limit. "
            "Don't try another wording — ask the owner which place they meant, or "
            "save it by hand with action='save' and a title."
        )
    state["n"] += 1
    found = _search_places(query, near)
    state["queries"][key] = found
    return found


def _remember(fields: dict) -> None:
    """Cache one flattened candidate. Cleared wholesale when it grows past the
    cap rather than evicted one by one: this only ever holds the results of the
    last few searches, and losing it degrades to what a hand-added place does."""
    gid = fields.get("google_place_id")
    if not gid:
        return
    if len(_SEARCH_CACHE) >= _SEARCH_CACHE_MAX:
        _SEARCH_CACHE.clear()
    _SEARCH_CACHE[gid] = fields


def _place_lines(conn: sqlite3.Connection, limit: int = 50) -> str:
    rows = conn.execute(
        "SELECT place_id, title, address, category, google_type_label FROM places "
        "ORDER BY title LIMIT ?",
        (limit,),
    ).fetchall()
    if not rows:
        return "(no saved places yet)"
    out = []
    for r in rows:
        tag = " · ".join(x for x in (r["category"], r["google_type_label"]) if x)
        where = f" — {r['address']}" if r["address"] else ""
        out.append(f"- [{r['place_id']}] {r['title']}{where}" + (f"  ({tag})" if tag else ""))
    return "\n".join(out)


def _require_place(conn: sqlite3.Connection, place_id: int) -> sqlite3.Row:
    if not place_id:
        raise TravelError(f"A place_id is required. Saved places:\n{_place_lines(conn)}")
    row = conn.execute("SELECT * FROM places WHERE place_id = ?", (place_id,)).fetchone()
    if row is None:
        raise TravelError(
            f"No place with id {place_id}. Saved places:\n{_place_lines(conn)}"
        )
    return row


def _upsert_place(conn: sqlite3.Connection, fields: dict) -> tuple[int, bool]:
    """Insert, or return the existing row when Google's id is already known.

    Dedupe is on google_place_id because it is exact: the same place saved for a
    second trip must be the same row, or a corrected address would only ever fix
    one of them. A hand-added place has no such id and is always a new row —
    there is nothing reliable to match on, and merging by name would silently
    join two different cafes with the same name.
    """
    gid = fields.get("google_place_id")
    if gid:
        existing = conn.execute(
            "SELECT place_id FROM places WHERE google_place_id = ?", (gid,)
        ).fetchone()
        if existing:
            return existing["place_id"], False
    cur = conn.execute(
        "INSERT INTO places(google_place_id, destination_id, title, address, maps_url, "
        "lat, lng, category, google_type, google_type_label, google_types, city, country) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            gid,
            fields["destination_id"],
            fields["title"],
            fields.get("address"),
            fields.get("maps_url"),
            fields.get("lat"),
            fields.get("lng"),
            fields.get("category"),
            fields.get("google_type"),
            fields.get("google_type_label"),
            fields.get("google_types"),
            fields.get("city"),
            fields.get("country"),
        ),
    )
    return cur.lastrowid, True


@tool_register(namespace="travel", destructive=True)
@tool
def manage_place(
    action: str,
    query: str = "",
    near: str = "",
    place_id: int = 0,
    google_place_id: str = "",
    title: str = "",
    address: str = "",
    maps_url: str = "",
    category: str = "",
    destination: str = "",
) -> str:
    """Look up and keep places — restaurants, sights, hotels, stations.

    A place belongs to no trip. Saving one makes it available to every trip; use
    manage_wishlist or manage_itinerary to attach it to a particular one.

    Actions:
    - search: look the place up with Google and return numbered candidates, each
      with a google_place_id. Use this before saving anything real — it gets the
      address and map link right, and it is how you tell branches of a chain
      apart. Then call save with the chosen candidate's fields.
    - save: store a place. Pass google_place_id plus the fields from a search
      result, or just title and address to record somewhere Google doesn't know.
      Saving a google_place_id that is already stored returns the existing place
      rather than duplicating it.
    - list: saved places with their place_id — use when you need an id.
    - update: correct a saved place. Because a place is shared, fixing an address
      here fixes it everywhere it appears.
    - delete: remove a saved place. Refused while any wishlist or itinerary row
      still points at it, so nothing is orphaned.

    Args:
        action: search | save | list | update | delete
        query: what to look up, e.g. "Time Out Market". search only.
        near: a city or neighbourhood to disambiguate. search only.
        place_id: the id from list/save. Required for update and delete.
        google_place_id: Google's id, taken from a search result.
        title: the place's name. Required when saving without a search result.
        address: street address.
        maps_url: link to the place on Google Maps.
        category: one of restaurant, cafe, dessert, bar, market, sights, outdoors,
            shopping, lodging, transit, other. Usually derived automatically from
            what Google says the place is — pass it only for a place Google does
            not know, or to correct a bucket that came out wrong.
    """
    action = (action or "").strip().lower()
    if action not in ("search", "save", "list", "update", "delete",):
        # Checked before anything else is required: an unknown action must not
        # be reported as a missing id, which is what the model would then try to
        # fix.
        return f"Error: Unknown action {action!r}. Use one of: search, save, list, update, delete."
    conn = _get_db()
    try:
        try:
            if action == "search":
                if not query.strip():
                    raise TravelError("search needs a query.")
                found = _search_guarded(query, near)
                if not found:
                    return (
                        f"No places found for {query!r}"
                        + (f" near {near!r}" if near.strip() else "")
                        + ". Try a different spelling, or save it by hand with action='save'."
                    )
                out = [f"{len(found)} candidate(s) — save the right one with action='save':"]
                for i, c in enumerate(found, 1):
                    f = _flatten(c)
                    _remember(f)
                    out.append(
                        f"{i}. {f['title']}\n"
                        f"   {f['address'] or '(no address)'}\n"
                        f"   {f['google_type_label'] or f['google_type'] or 'place'}"
                        f"  ·  google_place_id={f['google_place_id']}"
                    )
                return "\n".join(out)

            if action == "list":
                return _place_lines(conn)

            if action == "save":
                if not title.strip():
                    raise TravelError(
                        "save needs at least a title. Run action='search' first to get the "
                        "address and map link right."
                    )
                gid = google_place_id.strip() or None
                # Start from what the search saw, then let anything explicitly
                # passed win — the caller correcting a name is more current than
                # the lookup that produced it.
                fields = dict(_SEARCH_CACHE.get(gid, {})) if gid else {}
                fields.update({
                    "google_place_id": gid,
                    "title": title.strip(),
                    "address": address.strip() or fields.get("address"),
                    "maps_url": maps_url.strip() or fields.get("maps_url"),
                    "category": _validate_category(category)
                                or _categorize(fields.get("google_type")),
                })
                fields.setdefault("lat", None)
                fields.setdefault("lng", None)
                fields.setdefault("google_type", None)
                fields.setdefault("google_type_label", None)
                fields.setdefault("google_types", None)
                fields.setdefault("city", None)
                fields.setdefault("country", None)
                fields["destination_id"] = _resolve_destination(
                    conn, destination, "", fields
                )
                pid, created = _upsert_place(conn, fields)
                conn.commit()
                if not created:
                    return f"Already saved as place {pid} — {title.strip()}. Nothing duplicated."
                return f"Saved place {pid} — {title.strip()}."

            place = _require_place(conn, place_id)

            if action == "update":
                sets, args, said = [], [], []
                if category.strip():
                    _validate_category(category)
                for col, val in (
                    ("title", title), ("address", address),
                    ("maps_url", maps_url), ("category", category),
                ):
                    if val.strip():
                        value = val.strip().lower() if col == "category" else val.strip()
                        sets.append(f"{col} = ?"); args.append(value)
                        said.append(f"{col} → {value}")
                if not sets:
                    return f"Nothing to update on place {place_id} — pass a field to change."
                conn.execute(
                    f"UPDATE places SET {', '.join(sets)} WHERE place_id = ?", (*args, place_id)
                )
                conn.commit()
                return f"Updated place {place_id} ({place['title']}): " + "; ".join(said) + "."

            if action == "delete":
                return _delete_place(conn, place)

        except TravelError as e:
            return f"Error: {e}"
    finally:
        conn.close()


def _delete_place(conn: sqlite3.Connection, place: sqlite3.Row) -> str:
    """Refused while referenced, and the refusal says by what.

    No confirmation button here, unlike deleting a trip: an unreferenced place is
    a name and an address, re-fetchable in one lookup, so a button would be
    friction protecting nothing. Everything that would actually hurt to lose is
    what makes this refuse in the first place.
    """
    pid = place["place_id"]
    n_wish = conn.execute(
        "SELECT COUNT(*) FROM wishlist WHERE place_id = ?", (pid,)
    ).fetchone()[0]
    n_itin = conn.execute(
        "SELECT COUNT(*) FROM itinerary WHERE place_id = ?", (pid,)
    ).fetchone()[0]
    if n_wish or n_itin:
        raise TravelError(
            f"Place {pid} ({place['title']}) is still used by {n_wish} wishlist item(s) "
            f"and {n_itin} scheduled item(s). Remove those first, or leave the place —"
            " an unused saved place costs nothing."
        )
    conn.execute("DELETE FROM places WHERE place_id = ?", (pid,))
    conn.commit()
    return f"Deleted place {pid} — {place['title']}."

def _resolve_destination(
    conn, destination: str, trip_id: str = "", place_fields: dict | None = None
) -> int:
    """Which destination a place being saved belongs to.

    Named by the caller, else taken from the trip in context, else refused. It is
    deliberately NOT guessed from the place's own city: Google reports a ward for
    a Tokyo venue — Shibuya, Shinjuku — so guessing would file one city under
    several names, and the wishlist that hangs off a destination would quietly
    split in two between one visit and the next. Asking costs one turn; guessing
    costs a list.
    """
    from tools.travel.destinations import _destination_lines, _require_destination

    if destination.strip():
        return _require_destination(conn, destination.strip())["destination_id"]
    if trip_id:
        row = conn.execute(
            "SELECT destination_id FROM trips WHERE trip_id = ?", (trip_id,)
        ).fetchone()
        if row:
            return row["destination_id"]
    city = (place_fields or {}).get("city")
    hint = f" Google filed it under {city!r}, which may or may not be the name you use." if city else ""
    raise TravelError(
        "Which destination is this place in? Pass destination=<name>." + hint
        + "\nExisting:\n" + _destination_lines(conn)
    )


def _resolve_place(
    conn: sqlite3.Connection,
    place_id: int,
    google_place_id: str,
    title: str,
    address: str,
    maps_url: str,
    category: str,
    destination: str = "",
    trip_id: str = "",
) -> int:
    """Find or create the place this wishlist row will point at.

    Three ways in, in order of how much they can be trusted: an id already in
    hand, Google's id (which dedupes exactly), or a bare title for somewhere
    Google does not know. The inline forms exist so the common path is one call
    — the model should not have to orchestrate two tools to write down a
    restaurant.
    """
    if place_id:
        row = conn.execute(
            "SELECT place_id FROM places WHERE place_id = ?", (place_id,)
        ).fetchone()
        if row is None:
            raise TravelError(
                f"No place with id {place_id}. Saved places:\n{_place_lines(conn)}"
            )
        return place_id

    gid = google_place_id.strip() or None
    if not gid and not title.strip():
        raise TravelError(
            "add needs a place: pass place_id, or google_place_id from a "
            "manage_place search, or at least a title."
        )

    fields = dict(_SEARCH_CACHE.get(gid, {})) if gid else {}
    resolved_title = title.strip() or fields.get("title") or ""
    if not resolved_title:
        raise TravelError(
            f"google_place_id {gid!r} is not from a recent search, so there is no "
            "title for it. Search for the place again, or pass a title."
        )
    fields.update({
        "google_place_id": gid,
        "title": resolved_title,
        "address": address.strip() or fields.get("address"),
        "maps_url": maps_url.strip() or fields.get("maps_url"),
        "category": _validate_category(category) or _categorize(fields.get("google_type")),
    })
    for k in ("lat", "lng", "google_type", "google_type_label", "google_types",
              "city", "country"):
        fields.setdefault(k, None)
    fields["destination_id"] = _resolve_destination(conn, destination, trip_id, fields)
    new_id, _ = _upsert_place(conn, fields)
    return new_id
