"""The wishlist table: places the owner wants to visit on a particular trip.

Wanting to go is its own fact, independent of whether the place is also
scheduled — so nothing here touches the itinerary, and scheduling never consumes
a row from this table.
"""

import sqlite3

from langchain_core.tools import tool

from tools.registry import tool_register
from tools.travel._db import TravelError, _get_db, _require_trip
from tools.travel.places import (
    CATEGORIES,
    _categorize,
    _place_lines,
    _upsert_place,
    _validate_category,
)

# Undecided sorts last: a place nothing could classify is the one you want at
# the bottom of the list, not interleaved with the headings that mean something.
_CATEGORY_ORDER = {name: i for i, name in enumerate(CATEGORIES)}


def _wishlist_lines(conn: sqlite3.Connection, trip_id: str) -> str:
    """The trip's wishlist, grouped by the place's category.

    Grouped rather than flat because that is how the list is read — "where are
    we eating" is a section, not a filter — and the tile renders the same shape.
    """
    rows = conn.execute(
        "SELECT w.place_id, w.notes, w.priority, p.title, p.address, "
        "       p.category, p.google_type_label "
        "FROM wishlist w JOIN places p ON p.place_id = w.place_id "
        "WHERE w.trip_id = ? ",
        (trip_id,),
    ).fetchall()
    if not rows:
        return f"{trip_id} has nothing on its wishlist yet."

    ordered = sorted(
        rows,
        key=lambda r: (
            _CATEGORY_ORDER.get(r["category"], len(CATEGORIES)),
            -(r["priority"] or 0),
            (r["title"] or "").lower(),
        ),
    )
    out, current = [f"{trip_id} wishlist ({len(rows)} place(s)):"], object()
    for r in ordered:
        cat = r["category"] or "unsorted"
        if cat != current:
            current = cat
            out.append(f"\n{cat.upper()}")
        # The fine type earns its place here: the heading says `restaurant`, this
        # says which kind, which is the whole reason it is stored.
        bits = [b for b in (r["google_type_label"], r["address"]) if b]
        out.append(f"  [{r['place_id']}] {r['title']}" + (f" — {' · '.join(bits)}" if bits else ""))
        if r["notes"]:
            out.append(f"        note: {r['notes']}")
    return "\n".join(out)


def _resolve_place(
    conn: sqlite3.Connection,
    place_id: int,
    google_place_id: str,
    title: str,
    address: str,
    maps_url: str,
    category: str,
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

    from tools.travel.places import _SEARCH_CACHE

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
    for k in ("lat", "lng", "google_type", "google_type_label", "google_types"):
        fields.setdefault(k, None)
    new_id, _ = _upsert_place(conn, fields)
    return new_id


@tool_register(namespace="travel", destructive=True)
@tool
def manage_wishlist(
    action: str,
    trip_id: str = "",
    place_id: int = 0,
    google_place_id: str = "",
    title: str = "",
    address: str = "",
    maps_url: str = "",
    category: str = "",
    notes: str = "",
    priority: int = 0,
) -> str:
    """Keep a per-trip list of places the owner wants to visit.

    A wishlist entry says "I want to go here, on this trip". It is independent of
    the itinerary: scheduling a place does NOT remove it from the wishlist, and a
    place can sit on two trips' wishlists at once.

    Actions:
    - list: the trip's wishlist, grouped by category.
    - add: put a place on a trip's wishlist. Pass place_id if you already know
      it, or google_place_id from a manage_place search, or just a title for
      somewhere Google doesn't know — the place is created if needed. Adding the
      same place twice is not an error; it updates the note instead.
    - remove: take a place off this trip's wishlist. The place itself is kept,
      so it stays available to other trips.

    Args:
        action: list | add | remove
        trip_id: which trip's wishlist. Required. Call manage_trip(action='list')
            if you don't know the exact id.
        place_id: an existing place's id, from manage_place or a wishlist listing.
        google_place_id: Google's id, from a manage_place search.
        title: the place's name, when adding one Google doesn't know.
        address: street address, when adding by title.
        maps_url: link to the place on Google Maps.
        category: one of restaurant, cafe, dessert, bar, market, sights,
            outdoors, shopping, lodging, transit, other. Usually derived
            automatically; pass it only for a place Google doesn't know.
        notes: why this place — "go before 11, queue after", "rainy day option".
        priority: higher sorts first within its category. Leave 0 unless asked.
    """
    action = (action or "").strip().lower()
    conn = _get_db()
    try:
        try:
            trip = _require_trip(conn, trip_id)
            tid = trip["trip_id"]

            if action == "list":
                return _wishlist_lines(conn, tid)

            if action == "add":
                pid = _resolve_place(
                    conn, place_id, google_place_id, title, address, maps_url, category
                )
                existing = conn.execute(
                    "SELECT wishlist_id FROM wishlist WHERE trip_id = ? AND place_id = ?",
                    (tid, pid),
                ).fetchone()
                name = conn.execute(
                    "SELECT title FROM places WHERE place_id = ?", (pid,)
                ).fetchone()["title"]
                if existing:
                    # Already there. Re-adding is how a note gets attached in
                    # practice, so treat it as an update rather than a refusal —
                    # a refusal would strand the note the owner just gave.
                    if notes.strip() or priority:
                        conn.execute(
                            "UPDATE wishlist SET notes = COALESCE(NULLIF(?, ''), notes), "
                            "priority = CASE WHEN ? != 0 THEN ? ELSE priority END "
                            "WHERE wishlist_id = ?",
                            (notes.strip(), priority, priority, existing["wishlist_id"]),
                        )
                        conn.commit()
                        return f"{name} was already on {tid}'s wishlist — updated its note."
                    return f"{name} is already on {tid}'s wishlist."
                conn.execute(
                    "INSERT INTO wishlist(trip_id, place_id, notes, priority) VALUES(?,?,?,?)",
                    (tid, pid, notes.strip() or None, priority or None),
                )
                conn.commit()
                return f"Added {name} (place {pid}) to {tid}'s wishlist."

            if action == "remove":
                if not place_id:
                    raise TravelError(
                        f"remove needs a place_id. {tid}'s wishlist:\n"
                        f"{_wishlist_lines(conn, tid)}"
                    )
                cur = conn.execute(
                    "DELETE FROM wishlist WHERE trip_id = ? AND place_id = ?", (tid, place_id)
                )
                conn.commit()
                if not cur.rowcount:
                    raise TravelError(
                        f"Place {place_id} is not on {tid}'s wishlist. Currently:\n"
                        f"{_wishlist_lines(conn, tid)}"
                    )
                return (
                    f"Removed place {place_id} from {tid}'s wishlist. "
                    "The saved place itself is kept."
                )

            raise TravelError(f"Unknown action {action!r}. Use one of: list, add, remove.")
        except TravelError as e:
            return f"Error: {e}"
    finally:
        conn.close()
