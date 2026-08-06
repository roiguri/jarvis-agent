"""The wishlist: things you want to do at a destination.

Anchored to the destination rather than to a trip, which is the whole point —
wanting to eat somewhere is a fact about the place, not about one visit, so
going back finds the list you left. A trip reaches its list through its
destination; nothing here stores a trip.

It is also independent of the itinerary. Scheduling something never consumes its
row, so a place can sit on the list and on two days at once.
"""

import sqlite3
from datetime import date

from langchain_core.tools import tool

from tools.registry import tool_register
from tools.travel._db import TravelError, _get_db
from tools.travel.destinations import _destination_lines, _require_destination
from tools.travel.places import CATEGORIES, _place_lines

# Undecided sorts last: a place nothing could classify belongs at the bottom,
# not interleaved with headings that mean something.
_CATEGORY_ORDER = {name: i for i, name in enumerate(CATEGORIES)}

# The whole row, with the place's values underneath the row's own overrides.
_SELECT = (
    "SELECT w.*, "
    "       COALESCE(w.title, p.title) AS label, "
    "       COALESCE(w.city, p.city)   AS group_city, "
    "       p.category, p.address, p.google_type_label "
    "FROM wishlist w LEFT JOIN places p ON p.place_id = w.place_id "
)


def _clearable(value: str) -> tuple[bool, str | None]:
    """An update argument, as (was it given, what to store).

    The empty string means *clear this*; an omitted argument means *leave it
    alone*. Without the distinction there is no way to retract a done_at or drop
    a wrong note, because every argument arrives as a flat scalar.
    """
    if value is None:
        return False, None
    return (True, value.strip() or None) if value != "__unset__" else (False, None)


def _wishlist_lines(
    conn: sqlite3.Connection, dest: sqlite3.Row, include_done: bool = False
) -> str:
    """The destination's list, grouped by city and then by category."""
    rows = conn.execute(
        _SELECT + "WHERE w.destination_id = ?", (dest["destination_id"],)
    ).fetchall()
    if not include_done:
        rows = [r for r in rows if not r["done_at"]]
    if not rows:
        return f"{dest['name']} has nothing on its wishlist yet."

    ordered = sorted(
        rows,
        key=lambda r: (
            (r["group_city"] or "￿").lower(),          # no city sorts last
            _CATEGORY_ORDER.get(r["category"], len(CATEGORIES)),
            r["priority"] or 3,
            (r["label"] or "").lower(),
        ),
    )
    out = [f"{dest['name']} wishlist ({len(rows)} item(s)):"]
    city = cat = object()
    for r in ordered:
        if r["group_city"] != city:
            city = r["group_city"]
            out.append(f"\n{(city or 'no city').upper()}")
            cat = object()
        if r["category"] != cat:
            cat = r["category"]
            out.append(f"  {cat or 'unsorted'}")
        bits = [b for b in (r["google_type_label"], r["address"]) if b]
        star = "!" * max(0, 3 - (r["priority"] or 3))
        done = "  (done)" if r["done_at"] else ""
        out.append(
            f"    [{r['wishlist_id']}]{star} {r['label']}{done}"
            + (f" — {' · '.join(bits)}" if bits else "")
        )
        if r["notes"]:
            out.append(f"          note: {r['notes']}")
    return "\n".join(out)


def _resolve_dest(
    conn: sqlite3.Connection, destination: str, trip_id: str, place_id: int
) -> sqlite3.Row:
    """Whose list this is: named outright, else the trip's, else the place's."""
    if destination.strip():
        return _require_destination(conn, destination.strip())
    if trip_id.strip():
        row = conn.execute(
            "SELECT destination_id FROM trips WHERE trip_id = ?", (trip_id.strip(),)
        ).fetchone()
        if row is None:
            raise TravelError(f"No trip {trip_id.strip()!r}.")
        return _require_destination(conn, row["destination_id"])
    if place_id:
        row = conn.execute(
            "SELECT destination_id FROM places WHERE place_id = ?", (place_id,)
        ).fetchone()
        if row:
            return _require_destination(conn, row["destination_id"])
    raise TravelError(
        "Which destination's list? Pass destination=<name>, or a trip_id.\n"
        f"Existing:\n{_destination_lines(conn)}"
    )


def _require_entry(conn: sqlite3.Connection, wishlist_id: int) -> sqlite3.Row:
    if not wishlist_id:
        raise TravelError("A wishlist_id is required — it is shown in the listing.")
    row = conn.execute(_SELECT + "WHERE w.wishlist_id = ?", (wishlist_id,)).fetchone()
    if row is None:
        raise TravelError(f"No wishlist entry {wishlist_id}.")
    return row


@tool_register(namespace="travel", destructive=True)
@tool
def manage_wishlist(
    action: str,
    wishlist_id: int = 0,
    destination: str = "",
    trip_id: str = "",
    place_id: int = 0,
    title: str = "__unset__",
    city: str = "__unset__",
    notes: str = "__unset__",
    priority: int = 0,
    done_at: str = "__unset__",
    include_done: bool = False,
) -> str:
    """Things the owner wants to do at a destination.

    The list belongs to the DESTINATION, not to a trip, so it survives into the
    next visit and needs no trip to exist. Scheduling something never removes it
    from here.

    Actions:
    - list: the destination's list, grouped by city then category. Give a
      destination or a trip_id. Done items are hidden unless include_done.
    - add: put something on the list. For a real place, pass place_id (save it
      with manage_place first if it isn't saved yet). For an intention that has
      no place — "somewhere with a view", "a good ramen place" — pass a title
      and a destination, and optionally a city.
    - update: change notes, priority, title, city, or mark it done. An EMPTY
      STRING clears a field; omitting an argument leaves it alone.
    - remove: take it off the list entirely. Use this for "changed my mind";
      for "we went", set done_at instead so the list resolves over repeat trips.

    Args:
        action: list | add | update | remove
        wishlist_id: the entry, from a listing. Required for update and remove.
        destination: whose list, by name. Call manage_destination(action='list')
            if you don't know it.
        trip_id: an alternative to destination — the trip's destination is used.
        place_id: an existing place, from manage_place.
        title: for a placeless intention, its name. On update, renames the entry.
        city: which city to group it under, overriding the place's.
        notes: why this — "go before 11", "only if it rains".
        priority: 1 (highest) to 5 (lowest). Leave 0 to keep the default of 3.
        done_at: YYYY-MM-DD, when the owner actually went.
        include_done: list items already done as well.
    """
    action = (action or "").strip().lower()
    if action not in ("list", "add", "update", "remove"):
        return f"Error: Unknown action {action!r}. Use one of: list, add, update, remove."
    conn = _get_db()
    try:
        try:
            if action == "list":
                dest = _resolve_dest(conn, destination, trip_id, place_id)
                return _wishlist_lines(conn, dest, include_done)

            if action == "add":
                dest = _resolve_dest(conn, destination, trip_id, place_id)
                _, name = _clearable(title)
                if not place_id and not name:
                    raise TravelError(
                        "add needs either a place_id, or a title for an intention with "
                        f"no place yet. Saved places:\n{_place_lines(conn)}"
                    )
                if place_id:
                    if conn.execute(
                        "SELECT 1 FROM places WHERE place_id = ?", (place_id,)
                    ).fetchone() is None:
                        raise TravelError(
                            f"No place with id {place_id}. Saved places:\n{_place_lines(conn)}"
                        )
                    existing = conn.execute(
                        "SELECT wishlist_id FROM wishlist WHERE destination_id = ? "
                        "AND place_id = ?",
                        (dest["destination_id"], place_id),
                    ).fetchone()
                    if existing:
                        # Re-adding is how a note gets attached in practice, so
                        # this updates rather than refusing and stranding it.
                        return _update(conn, _require_entry(conn, existing["wishlist_id"]),
                                       title, city, notes, priority, done_at,
                                       prefix="was already on the list — ")
                _, c = _clearable(city)
                _, n = _clearable(notes)
                cur = conn.execute(
                    "INSERT INTO wishlist(destination_id, place_id, title, city, notes, "
                    "priority) VALUES(?,?,?,?,?,?)",
                    (dest["destination_id"], place_id or None, name, c, n, priority or 3),
                )
                conn.commit()
                label = name or conn.execute(
                    "SELECT title FROM places WHERE place_id = ?", (place_id,)
                ).fetchone()["title"]
                return f"Added {label} to {dest['name']}'s wishlist [id {cur.lastrowid}]."

            entry = _require_entry(conn, wishlist_id)

            if action == "update":
                return _update(conn, entry, title, city, notes, priority, done_at)

            if action == "remove":
                conn.execute("DELETE FROM wishlist WHERE wishlist_id = ?", (wishlist_id,))
                conn.commit()
                return (
                    f"Removed {entry['label']} from the wishlist. The saved place is kept. "
                    "(If the owner went rather than changed their mind, done_at was the "
                    "better record.)"
                )
        except TravelError as e:
            return f"Error: {e}"
        except sqlite3.IntegrityError as e:
            return f"Error: {e}"
    finally:
        conn.close()


def _update(
    conn: sqlite3.Connection,
    entry: sqlite3.Row,
    title: str,
    city: str,
    notes: str,
    priority: int,
    done_at: str,
    prefix: str = "",
) -> str:
    sets, args, said = [], [], []
    for col, raw in (("title", title), ("city", city), ("notes", notes)):
        given, value = _clearable(raw)
        if given:
            sets.append(f"{col} = ?"); args.append(value)
            said.append(f"{col} {'cleared' if value is None else '→ ' + value}")
    if priority:
        if not 1 <= priority <= 5:
            raise TravelError(f"priority must be 1..5, got {priority}.")
        sets.append("priority = ?"); args.append(priority)
        said.append(f"priority → {priority}")
    given, value = _clearable(done_at)
    if given:
        if value:
            try:
                date.fromisoformat(value)
            except ValueError:
                raise TravelError(f"done_at must be YYYY-MM-DD, got {value!r}.")
        sets.append("done_at = ?"); args.append(value)
        said.append("marked done" if value else "no longer marked done")

    if not sets:
        return f"Nothing to change on {entry['label']} — pass a field."
    if entry["place_id"] is None and "title = ?" in sets and args[sets.index("title = ?")] is None:
        raise TravelError("An entry with no place cannot have its title cleared.")
    conn.execute(
        f"UPDATE wishlist SET {', '.join(sets)} WHERE wishlist_id = ?",
        (*args, entry["wishlist_id"]),
    )
    conn.commit()
    return f"{entry['label']} {prefix}" + "; ".join(said) + "."
