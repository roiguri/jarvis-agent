"""The itinerary table: things happening at a time, on a trip.

Scheduling a place never consumes its wishlist row — the two tables are
independent facts, so nothing here writes to the wishlist except `unschedule`,
which puts a place back on it deliberately.
"""

import re
import sqlite3
from datetime import date

from langchain_core.tools import tool

from tools.registry import tool_register
from tools.travel._db import TravelError, _get_db, _parse_date, _require_trip
from tools.travel.places import _resolve_place

ITEM_TYPES = ("place", "lodging", "transit", "note")

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _parse_time(value: str, field: str) -> str | None:
    t = (value or "").strip()
    if not t:
        return None
    if not _TIME_RE.match(t):
        raise TravelError(f"{field} must be 24-hour HH:MM, got {value!r}.")
    return t


def _day_number(trip: sqlite3.Row, when: str) -> int | None:
    """Which day of the trip a date falls on, counting the first day as 1.

    Derived on read and never stored, so it cannot disagree with the dates.
    None when the trip has none — a dateless trip has no day 1 to count from.
    Can be <= 0 for something scheduled before the trip officially starts, which
    is legitimate (a red-eye the night before) and shown as such.
    """
    if not trip["start_date"] or not when:
        return None
    return (date.fromisoformat(when) - date.fromisoformat(trip["start_date"])).days + 1


def _entry_line(r: sqlite3.Row) -> str:
    """One scheduled thing, as the model reads it back."""
    when = r["start_time"] or ""
    if r["start_time"] and r["end_time"]:
        when = f"{r['start_time']}-{r['end_time']}"
    head = f"  [{r['entry_id']}] {when:<11} {r['label']}"
    bits = []
    if r["item_type"] == "transit" and (r["origin"] or r["destination_loc"]):
        bits.append(f"{r['origin'] or '?'} → {r['destination_loc'] or '?'}")
    if r["item_type"] != "place":
        bits.append(r["item_type"])
    if r["confirmation_code"]:
        bits.append(f"conf {r['confirmation_code']}")
    if r["address"]:
        bits.append(r["address"])
    out = head + (f"  ({' · '.join(bits)})" if bits else "")
    if r["notes"]:
        out += f"\n        note: {r['notes']}"
    return out


def _itinerary_lines(conn: sqlite3.Connection, trip: sqlite3.Row) -> str:
    """The trip's schedule: stays first, then a section per day.

    Stays are lifted out because a multi-night booking is not a slot in a day —
    repeating it under every date it covers would bury the days it does not.
    """
    tid = trip["trip_id"]
    rows = conn.execute(
        "SELECT i.*, COALESCE(i.title, p.title) AS label, p.address "
        "FROM itinerary i LEFT JOIN places p ON p.place_id = i.place_id "
        "WHERE i.trip_id = ? ORDER BY i.start_date, "
        "  CASE WHEN i.start_time IS NULL THEN 1 ELSE 0 END, i.start_time",
        (tid,),
    ).fetchall()
    if not rows:
        return f"{tid} has nothing scheduled yet."

    stays = [r for r in rows if r["item_type"] == "lodging"]
    rest = [r for r in rows if r["item_type"] != "lodging"]
    out = [f"{tid} itinerary ({len(rows)} item(s)):"]

    if stays:
        out.append("\nSTAYS")
        for r in stays:
            span = r["start_date"] + (f" → {r['end_date']}" if r["end_date"] else "")
            code = f"  (conf {r['confirmation_code']})" if r["confirmation_code"] else ""
            out.append(f"  [{r['entry_id']}] {span}  {r['label']}{code}")

    current = object()
    for r in rest:
        if r["start_date"] != current:
            current = r["start_date"]
            n = _day_number(trip, r["start_date"])
            if n is None:
                head = r["start_date"]
            elif n < 1:
                head = f"{r['start_date']}  (before day 1)"
            else:
                head = f"Day {n} · {r['start_date']}"
            if trip["end_date"] and r["start_date"] > trip["end_date"]:
                head += "  (after the trip ends)"
            out.append(f"\n{head}")
        out.append(_entry_line(r))
    return "\n".join(out)


def _require_entry(conn: sqlite3.Connection, trip: sqlite3.Row, entry_id: int) -> sqlite3.Row:
    if not entry_id:
        raise TravelError(
            f"An entry_id is required. {trip['trip_id']}'s itinerary:\n"
            f"{_itinerary_lines(conn, trip)}"
        )
    row = conn.execute(
        "SELECT i.*, COALESCE(i.title, p.title) AS label FROM itinerary i "
        "LEFT JOIN places p ON p.place_id = i.place_id "
        "WHERE i.entry_id = ? AND i.trip_id = ?",
        (entry_id, trip["trip_id"]),
    ).fetchone()
    if row is None:
        raise TravelError(
            f"No entry {entry_id} on {trip['trip_id']}. Its itinerary:\n"
            f"{_itinerary_lines(conn, trip)}"
        )
    return row


def _window_note(trip: sqlite3.Row, start: str, end: str | None) -> str:
    """Say so when something lands outside the trip's own dates.

    Accepted, never refused: a trip genuinely starts with a red-eye the night
    before and ends after a late checkout. Silence would be the failure — the
    owner should hear it, in case it was a mistyped year.
    """
    if not trip["start_date"]:
        return ""
    last = end or start
    if start < trip["start_date"] or last > trip["end_date"]:
        return (
            f" Note: this falls outside the trip window "
            f"({trip['start_date']} to {trip['end_date']}) — kept as an edge day."
        )
    return ""


@tool_register(namespace="travel", destructive=True)
@tool
def manage_itinerary(
    action: str,
    trip_id: str = "",
    entry_id: int = 0,
    place_id: int = 0,
    google_place_id: str = "",
    title: str = "",
    item_type: str = "",
    date: str = "",
    end_date: str = "",
    start_time: str = "",
    end_time: str = "",
    origin: str = "",
    destination_loc: str = "",
    confirmation_code: str = "",
    notes: str = "",
) -> str:
    """Build and adjust a trip's day-by-day schedule.

    Scheduling a place does NOT remove it from the wishlist — they are separate
    lists — and the same place may be scheduled on more than one day.

    Actions:
    - list: the whole schedule, stays first then a section per day.
    - schedule: put something on a day. Needs a dated trip. Identify a place by
      place_id, google_place_id, or a bare title; a transit leg or a note needs
      only a title. ALWAYS pass confirmation_code when a booking has one — it is
      what stops the row being moved if the trip's dates shift.
    - reschedule: change an existing entry's date or times.
    - unschedule: take it off the schedule but keep wanting to go — the place
      goes back on the trip's wishlist.
    - remove: delete the entry outright, wishlist untouched.

    Args:
        action: list | schedule | reschedule | unschedule | remove
        trip_id: which trip. Required. Call manage_trip(action='list') if unsure.
        entry_id: the entry to change, from a listing.
        place_id: an existing place's id.
        google_place_id: Google's id, from a manage_place search.
        title: name of the thing — required for transit and notes, which have no
            place; optional for a place you are creating inline.
        item_type: place | lodging | transit | note. Defaults to place when a
            place is given, note otherwise. Use lodging for a stay spanning days,
            transit for a leg between two points.
        date: the day it happens, YYYY-MM-DD.
        end_date: last day, for a stay spanning several days.
        start_time: 24-hour HH:MM.
        end_time: 24-hour HH:MM.
        origin: where a transit leg starts.
        destination_loc: where a transit leg ends.
        confirmation_code: booking reference. Also marks the row as booked, so a
            trip date change reports it for rebooking instead of moving it.
        notes: anything else worth remembering about this item.
    """
    action = (action or "").strip().lower()
    if action not in ("list", "schedule", "reschedule", "unschedule", "remove",):
        # Checked before anything else is required: an unknown action must not
        # be reported as a missing id, which is what the model would then try to
        # fix.
        return f"Error: Unknown action {action!r}. Use one of: list, schedule, reschedule, unschedule, remove."
    conn = _get_db()
    try:
        try:
            trip = _require_trip(conn, trip_id)
            tid = trip["trip_id"]

            if action == "list":
                return _itinerary_lines(conn, trip)

            if action == "schedule":
                return _schedule(
                    conn, trip, place_id, google_place_id, title, item_type, date,
                    end_date, start_time, end_time, origin, destination_loc,
                    confirmation_code, notes,
                )

            entry = _require_entry(conn, trip, entry_id)

            if action == "reschedule":
                return _reschedule(conn, trip, entry, date, end_date, start_time, end_time)

            if action in ("unschedule", "remove"):
                conn.execute("DELETE FROM itinerary WHERE entry_id = ?", (entry["entry_id"],))
                if action == "remove":
                    conn.commit()
                    return f"Removed '{entry['label']}' from {tid}'s schedule."
                if not entry["place_id"]:
                    conn.commit()
                    return (
                        f"Removed '{entry['label']}' from {tid}'s schedule. It has no "
                        "place behind it, so there was nothing to put back on the wishlist."
                    )
                conn.execute(
                    "INSERT OR IGNORE INTO wishlist(trip_id, place_id) VALUES(?,?)",
                    (tid, entry["place_id"]),
                )
                conn.commit()
                return (
                    f"Unscheduled '{entry['label']}' — it is back on {tid}'s wishlist."
                )

        except TravelError as e:
            return f"Error: {e}"
    finally:
        conn.close()


def _schedule(
    conn: sqlite3.Connection,
    trip: sqlite3.Row,
    place_id: int,
    google_place_id: str,
    title: str,
    item_type: str,
    when: str,
    end_date: str,
    start_time: str,
    end_time: str,
    origin: str,
    destination_loc: str,
    confirmation_code: str,
    notes: str,
) -> str:
    tid = trip["trip_id"]
    if not trip["start_date"]:
        raise TravelError(
            f"{tid} has no dates yet, so nothing can be scheduled into it. Set them "
            "with manage_trip(action='update', start_date=..., end_date=...), or keep "
            "collecting places on its wishlist."
        )
    if not when.strip():
        raise TravelError("schedule needs a date (YYYY-MM-DD).")
    start = _parse_date(when, "date").isoformat()
    finish = _parse_date(end_date, "end_date").isoformat() if end_date.strip() else None
    if finish and finish < start:
        raise TravelError(f"end_date {finish} is before date {start}.")
    st = _parse_time(start_time, "start_time")
    et = _parse_time(end_time, "end_time")
    if st and et and not finish and et < st:
        raise TravelError(f"end_time {et} is before start_time {st} on the same day.")

    kind = (item_type or "").strip().lower()
    if kind and kind not in ITEM_TYPES:
        raise TravelError(
            f"Unknown item_type {kind!r}. Use one of: {', '.join(ITEM_TYPES)}."
        )

    # A transit leg or a note is not a place and must not be turned into one —
    # inventing a places row for "train to the airport" would pollute a table
    # every trip shares.
    pid = None
    if kind in ("transit", "note"):
        if not title.strip():
            raise TravelError(f"a {kind} needs a title.")
    elif place_id or google_place_id.strip() or (kind in ("place", "lodging")):
        pid = _resolve_place(conn, place_id, google_place_id, title, "", "", "")
    elif title.strip():
        kind = kind or "note"
    else:
        raise TravelError(
            "schedule needs something to schedule: a place_id, a google_place_id, "
            "or a title."
        )
    if not kind:
        kind = "place" if pid else "note"

    cur = conn.execute(
        "INSERT INTO itinerary(trip_id, place_id, item_type, title, start_date, end_date, "
        "start_time, end_time, origin, destination_loc, confirmation_code, notes) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            tid, pid, kind, title.strip() or None, start, finish, st, et,
            origin.strip() or None, destination_loc.strip() or None,
            confirmation_code.strip() or None, notes.strip() or None,
        ),
    )
    conn.commit()
    label = title.strip() or conn.execute(
        "SELECT title FROM places WHERE place_id = ?", (pid,)
    ).fetchone()["title"]
    n = _day_number(trip, start)
    where = f"day {n}, {start}" if n and n >= 1 else start
    at = f" at {st}" if st else ""
    booked = " Booked — it will not be moved if the trip's dates change." if confirmation_code.strip() else ""
    return (
        f"Scheduled '{label}' ({kind}) on {where}{at} [entry {cur.lastrowid}]."
        + _window_note(trip, start, finish) + booked
    )


def _reschedule(
    conn: sqlite3.Connection,
    trip: sqlite3.Row,
    entry: sqlite3.Row,
    when: str,
    end_date: str,
    start_time: str,
    end_time: str,
) -> str:
    sets, args, said = [], [], []
    start = entry["start_date"]
    finish = entry["end_date"]

    if when.strip():
        start = _parse_date(when, "date").isoformat()
        sets.append("start_date = ?"); args.append(start)
        said.append(f"date → {start}")
    if end_date.strip():
        finish = _parse_date(end_date, "end_date").isoformat()
        sets.append("end_date = ?"); args.append(finish)
        said.append(f"end date → {finish}")
    if finish and finish < start:
        raise TravelError(f"end_date {finish} is before date {start}.")
    if start_time.strip():
        st = _parse_time(start_time, "start_time")
        sets.append("start_time = ?"); args.append(st)
        said.append(f"from {st}")
    if end_time.strip():
        et = _parse_time(end_time, "end_time")
        sets.append("end_time = ?"); args.append(et)
        said.append(f"until {et}")

    if not sets:
        return (
            f"Nothing to change on entry {entry['entry_id']} — pass a date or a time."
        )
    conn.execute(
        f"UPDATE itinerary SET {', '.join(sets)} WHERE entry_id = ?",
        (*args, entry["entry_id"]),
    )
    conn.commit()
    return (
        f"Moved '{entry['label']}': " + ", ".join(said) + "."
        + _window_note(trip, start, finish)
    )
