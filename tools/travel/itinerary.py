"""The itinerary table: things happening at a time, on a trip.

Scheduling a place never consumes its wishlist row — the two tables are
independent facts, so nothing here writes to the wishlist except `unschedule`,
which puts a place back on it deliberately.
"""

import re
import sqlite3
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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


def _validate_tz(value: str, field: str) -> str | None:
    tz = (value or "").strip()
    if not tz:
        return None
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise TravelError(f"{field} {tz!r} is not an IANA name, e.g. 'Asia/Tokyo'.")
    return tz


def _zones(entry, trip_tz: str | None) -> tuple[str | None, str | None]:
    """The two ends' timezones, each falling back to the trip's own."""
    return (
        (entry["departure_timezone"] or trip_tz),
        (entry["arrival_timezone"] or trip_tz),
    )


def _duration(
    start_date: str, start_time: str | None, end_date: str | None,
    end_time: str | None, dep_tz: str | None, arr_tz: str | None,
) -> str | None:
    """How long something actually takes, once both ends are resolved.

    Wall-clock times alone cannot answer this across zones: NYC 22:00 to London
    10:00 is twelve hours of clock and seven of flying, and the difference is
    exactly the offset the clocks discard. So this returns None unless both
    zones are known — a wrong duration is worse than none.
    """
    if not (start_time and end_time and dep_tz and arr_tz):
        return None
    try:
        dep = datetime.combine(
            date.fromisoformat(start_date), time.fromisoformat(start_time),
            tzinfo=ZoneInfo(dep_tz),
        )
        arr = datetime.combine(
            date.fromisoformat(end_date or start_date), time.fromisoformat(end_time),
            tzinfo=ZoneInfo(arr_tz),
        )
    except (ValueError, ZoneInfoNotFoundError):
        return None
    minutes = int((arr - dep).total_seconds() // 60)
    if minutes <= 0:
        return None
    return f"{minutes // 60}h" + (f" {minutes % 60:02d}m" if minutes % 60 else "")


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


def _entry_line(r: sqlite3.Row, role: str = "single") -> str:
    """One scheduled thing, as the model reads it back.

    A spanning item shows only the end that belongs to this day, so the day it
    lands on says when it lands rather than when it left.
    """
    when = r["start_time"] or ""
    if role == "start":
        when = f"{r['start_time'] or ''} →".strip()
    elif role == "end":
        when = f"→ {r['end_time'] or ''}".strip()
    elif role == "continuation":
        when = "all day"
    elif r["start_time"] and r["end_time"]:
        # No rollover marker is needed here: anything ending on a later date is
        # placed on both days and takes the start/end roles above, so a single
        # item always begins and ends within one day.
        when = f"{r['start_time']}-{r['end_time']}"
    head = f"  [{r['entry_id']}] {when:<11} {r['label']}"
    bits = []
    if r["arrival_timezone"] and r["arrival_timezone"] != r["departure_timezone"]:
        # Say whose clock the arrival is on. Without it two different clocks sit
        # side by side in one line and read as one.
        bits.append(f"arr {r['arrival_timezone'].split('/')[-1].replace('_', ' ')} time")
    if r["item_type"] == "transit" and (r["from_location"] or r["to_location"]):
        bits.append(f"{r['from_location'] or '?'} → {r['to_location'] or '?'}")
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


def _in_trip_clock(day: str, t: str | None, from_tz: str | None, trip_tz: str | None) -> str | None:
    """A time expressed on the clock the day is being read in.

    An arrival is stored in the zone it lands in, so sorting it against the rest
    of a day would compare two different clocks as strings. This converts it to
    the trip's clock for ordering only — what is displayed stays as the schedule
    prints it.
    """
    if not t:
        return None
    if not from_tz or not trip_tz or from_tz == trip_tz:
        return t
    try:
        dt = datetime.combine(
            date.fromisoformat(day), time.fromisoformat(t), tzinfo=ZoneInfo(from_tz)
        )
        return dt.astimezone(ZoneInfo(trip_tz)).strftime("%H:%M")
    except (ValueError, ZoneInfoNotFoundError):
        return t


def day_span(rows, trip) -> list[str]:
    """Every date the strip covers: the trip's own window, plus every day any
    item touches.

    The minimum is taken over start_date ALONE. end_date is never earlier, so
    including it here would drop exactly the case this exists for — the red-eye
    that departs the night before the trip and lands on its first morning.
    """
    edges: list[str] = []
    for r in rows:
        edges.append(r["start_date"])
        edges.append(r["end_date"] or r["start_date"])
    if trip["start_date"]:
        edges += [trip["start_date"], trip["end_date"]]
    if not edges:
        return []
    lo, hi = min(edges), max(edges)
    d0, d1 = date.fromisoformat(lo), date.fromisoformat(hi)
    return [
        date.fromordinal(d0.toordinal() + n).isoformat()
        for n in range((d1 - d0).days + 1)
    ]


def place_rows(rows, trip) -> dict[str, list[tuple[str, object]]]:
    """date -> [(role, row)] for every day each item touches.

    An item placed only on its start date leaves the day it arrives looking
    empty — land at 06:00 and the morning reads free. So a row appears on every
    day it covers, tagged with what it is doing there: `single`, `start`,
    `continuation`, or `end`.

    Lodging is excluded. It is rendered as a banner precisely because a stay is
    not a slot in a day, and repeating it under every night would bury the days
    it does not cover.
    """
    trip_tz = trip["timezone"]
    by_date: dict[str, list[tuple[str, object]]] = {}
    for r in rows:
        if r["item_type"] == "lodging":
            continue
        start = r["start_date"]
        finish = r["end_date"] or start
        d0, d1 = date.fromisoformat(start), date.fromisoformat(finish)
        n_days = (d1 - d0).days
        for n in range(n_days + 1):
            day = date.fromordinal(d0.toordinal() + n).isoformat()
            role = (
                "single" if n_days == 0
                else "start" if n == 0
                else "end" if n == n_days
                else "continuation"
            )
            by_date.setdefault(day, []).append((role, r))

    for day, items in by_date.items():
        items.sort(key=lambda ri: _sort_key(day, ri[0], ri[1], trip_tz))
    return by_date


def _sort_key(day: str, role: str, r, trip_tz: str | None):
    """Ordering inside one day.

    Something already under way comes first; then everything with a time, an
    arrival taking its arrival time rather than the departure that started it a
    day earlier; then untimed items, which are not midnight items.
    """
    if role == "continuation":
        return (0, "", r["entry_id"])
    if role == "end":
        t = _in_trip_clock(day, r["end_time"], r["arrival_timezone"] or trip_tz, trip_tz)
        return (1, t or "", r["entry_id"]) if t else (2, "", r["entry_id"])
    t = r["start_time"]
    return (1, t, r["entry_id"]) if t else (2, "", r["entry_id"])


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
    out = [f"{tid} itinerary ({len(rows)} item(s)):"]

    if stays:
        out.append("\nSTAYS")
        for r in stays:
            span = r["start_date"] + (f" → {r['end_date']}" if r["end_date"] else "")
            code = f"  (conf {r['confirmation_code']})" if r["confirmation_code"] else ""
            out.append(f"  [{r['entry_id']}] {span}  {r['label']}{code}")

    placed = place_rows(rows, trip)
    for day in day_span(rows, trip):
        n = _day_number(trip, day)
        if n is None:
            head = day
        elif n < 1:
            head = f"{day}  (before day 1)"
        else:
            head = f"Day {n} · {day}"
        if trip["end_date"] and day > trip["end_date"]:
            head += "  (after the trip ends)"
        items = placed.get(day, [])
        out.append(f"\n{head}" + ("" if items else "   (nothing scheduled)"))
        for role, r in items:
            out.append(_entry_line(r, role))
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
    title: str = "__unset__",
    item_type: str = "",
    date: str = "",
    end_date: str = "",
    arrival_date: str = "",
    start_time: str = "",
    end_time: str = "",
    departure_timezone: str = "",
    arrival_timezone: str = "",
    from_location: str = "__unset__",
    to_location: str = "__unset__",
    confirmation_code: str = "__unset__",
    notes: str = "__unset__",
    wishlist_id: int = 0,
    move_to_trip: str = "",
) -> str:
    """Build and adjust a trip's day-by-day schedule.

    Scheduling a place does NOT remove it from the wishlist — they are separate
    lists — and the same place may be scheduled on more than one day.

    Actions:
    - list: the whole schedule, stays first then a section per day.
    - schedule: put something on a day. Needs a dated trip. Identify a place by
      place_id, google_place_id, or a bare title; a transit leg or a note needs
      only a title. For a flight or train that crosses timezones, pass
      arrival_date and BOTH timezone arguments — the arrival date cannot be
      worked out from the clocks, and the duration cannot be shown without the
      zones.
    - reschedule: change an existing entry's date or times.
    - update: change anything reschedule does not — title, notes,
      confirmation_code, the transit endpoints, item_type, which place it points
      at, or which trip it belongs to. An EMPTY STRING clears a field; omitting
      an argument leaves it alone. This is how a booking reference learned after
      the fact gets recorded.
    - remove: take it off the schedule. The wishlist is a separate list and is
      never touched — a place removed from a day is still on it.

    Args:
        action: list | schedule | reschedule | update | remove
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
        arrival_date: the day a transit leg lands, when that is not the day it
            left. The same column as end_date, named for the case it fits. Give
            it whenever the journey crosses timezones — read it off the ticket
            rather than leaving it to be guessed.
        start_time: 24-hour HH:MM, local where the item starts.
        end_time: 24-hour HH:MM. For a transit leg this is the arrival time
            LOCAL TO THE DESTINATION, the way a flight schedule prints it — so a
            22:00 departure arriving 06:00 is normal, not a mistake. An end
            earlier than its start is read as running past midnight and the
            arrival date is set for you.
        departure_timezone: IANA zone the departure time is local to, e.g.
            "Asia/Jerusalem". Set it on the flights into and out of the trip;
            leave it blank for anything inside the destination.
        arrival_timezone: IANA zone the arrival time is local to.
        from_location: where a transit leg starts — free text, e.g. "Tel Aviv".
        to_location: where it ends. Not a destination row; just a label.
        confirmation_code: booking reference. Also marks the row as booked, so a
            trip date change reports it for rebooking instead of moving it.
        notes: anything else worth remembering about this item.
        wishlist_id: schedule something straight off the wishlist, using the id
            its listing shows. The wishlist entry is not consumed.
        move_to_trip: on update, the trip_id to move this entry to.
    """
    action = (action or "").strip().lower()
    if action not in ("list", "schedule", "reschedule", "update", "remove",):
        # Checked before anything else is required: an unknown action must not
        # be reported as a missing id, which is what the model would then try to
        # fix.
        return (
            f"Error: Unknown action {action!r}. Use one of: "
            "list, schedule, reschedule, update, remove."
        )
    conn = _get_db()
    try:
        try:
            trip = _require_trip(conn, trip_id)
            tid = trip["trip_id"]

            if action == "list":
                return _itinerary_lines(conn, trip)

            if action == "schedule":
                if wishlist_id:
                    # "Book the thing on my list" is the commonest flow, and the
                    # listing hands back a wishlist_id, not a place_id. Taking
                    # one avoids asking the model for an id it never saw.
                    row = conn.execute(
                        "SELECT place_id, title FROM wishlist WHERE wishlist_id = ?",
                        (wishlist_id,),
                    ).fetchone()
                    if row is None:
                        raise TravelError(f"No wishlist entry {wishlist_id}.")
                    if row["place_id"] is None:
                        raise TravelError(
                            f"Wishlist entry {wishlist_id} ({row['title']}) has no place "
                            "behind it yet — save the place first, then schedule it."
                        )
                    place_id = row["place_id"]
                return _schedule(
                    conn, trip, place_id, google_place_id, _plain(title), item_type, date,
                    end_date or arrival_date, start_time, end_time,
                    _plain(from_location), _plain(to_location),
                    _plain(confirmation_code), _plain(notes),
                    departure_timezone, arrival_timezone,
                )

            entry = _require_entry(conn, trip, entry_id)

            if action == "reschedule":
                return _reschedule(conn, trip, entry, date, end_date or arrival_date,
                                   start_time, end_time)

            if action == "update":
                return _update_entry(
                    conn, entry, title, notes, confirmation_code, from_location,
                    to_location, item_type, move_to_trip, place_id,
                )

            if action == "remove":
                # There is no `unschedule`. It existed to put a place "back" on
                # the wishlist, which made sense only while the wishlist belonged
                # to a trip and scheduling felt like moving something out of it.
                # The list belongs to the destination now and was never consumed,
                # so the row is still exactly where it was.
                conn.execute("DELETE FROM itinerary WHERE entry_id = ?", (entry["entry_id"],))
                conn.commit()
                kept = ""
                if entry["place_id"]:
                    row = conn.execute(
                        "SELECT 1 FROM wishlist w JOIN trips t "
                        "ON t.destination_id = w.destination_id "
                        "WHERE t.trip_id = ? AND w.place_id = ?",
                        (tid, entry["place_id"]),
                    ).fetchone()
                    if row:
                        kept = " It is still on the wishlist."
                return f"Removed '{entry['label']}' from {tid}'s schedule.{kept}"

        except TravelError as e:
            return f"Error: {e}"
    finally:
        conn.close()


def _plain(value: str) -> str:
    """The sentinel means "not given" to anything that isn't an update. Without
    this, schedule stores the literal string."""
    return "" if value == "__unset__" else (value or "")


def _clearable(value: str) -> tuple[bool, str | None]:
    """An update argument as (was it given, what to store). The empty string
    means clear; an omitted argument means leave alone. With flat scalars there
    is no other way to say "remove this"."""
    if value == "__unset__" or value is None:
        return False, None
    return True, value.strip() or None


def _update_entry(
    conn: sqlite3.Connection,
    entry: sqlite3.Row,
    title: str,
    notes: str,
    confirmation_code: str,
    from_location: str,
    to_location: str,
    item_type: str,
    move_to_trip: str,
    place_id: int,
) -> str:
    """Everything reschedule does not touch."""
    sets, args, said = [], [], []
    for col, raw in (
        ("title", title), ("notes", notes), ("confirmation_code", confirmation_code),
        ("from_location", from_location), ("to_location", to_location),
    ):
        given, value = _clearable(raw)
        if given:
            sets.append(f"{col} = ?"); args.append(value)
            said.append(f"{col} {'cleared' if value is None else '→ ' + value}")

    kind = (item_type or "").strip().lower()
    if kind:
        if kind not in ITEM_TYPES:
            raise TravelError(
                f"Unknown item_type {kind!r}. Use one of: {', '.join(ITEM_TYPES)}."
            )
        sets.append("item_type = ?"); args.append(kind)
        said.append(f"item_type → {kind}")

    if place_id:
        if conn.execute(
            "SELECT 1 FROM places WHERE place_id = ?", (place_id,)
        ).fetchone() is None:
            raise TravelError(f"No place with id {place_id}.")
        sets.append("place_id = ?"); args.append(place_id)
        said.append(f"now points at place {place_id}")

    if move_to_trip.strip():
        dest = _require_trip(conn, move_to_trip.strip())
        sets.append("trip_id = ?"); args.append(dest["trip_id"])
        said.append(f"moved to trip {dest['trip_id']}")

    if not sets:
        return (
            f"Nothing to change on '{entry['label']}' — pass a field. "
            "Dates and times are reschedule's job."
        )
    # The row must still name something after a title is cleared.
    if "title = ?" in sets and args[sets.index("title = ?")] is None and not entry["place_id"]:
        raise TravelError(
            "This entry has no place behind it, so clearing its title would leave "
            "nothing to show. Give it a place_id first, or remove it."
        )
    conn.execute(
        f"UPDATE itinerary SET {', '.join(sets)} WHERE entry_id = ?",
        (*args, entry["entry_id"]),
    )
    conn.commit()
    return f"Updated '{entry['label']}': " + "; ".join(said) + "."


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
    from_location: str,
    to_location: str,
    confirmation_code: str,
    notes: str,
    departure_timezone: str = "",
    arrival_timezone: str = "",
) -> str:
    tid = trip["trip_id"]
    dep_tz = _validate_tz(departure_timezone, "departure_timezone")
    arr_tz = _validate_tz(arrival_timezone, "arrival_timezone")
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
    rolled = False
    if st and et and not finish and et < st:
        # An end earlier than its start, IN ONE ZONE, has exactly one reading:
        # it ran past midnight. Across zones it has none — Tokyo 17:00 to Los
        # Angeles 10:00 is an ordinary same-day westbound flight that this test
        # would put a day out. So when the ends are in different zones the date
        # is asked for rather than guessed; the model is reading a ticket and
        # knows when it lands. Deriving it instead is circular anyway: resolving
        # the arrival instant needs the very date being derived.
        if dep_tz and arr_tz and dep_tz != arr_tz:
            raise TravelError(
                f"This crosses timezones ({dep_tz} to {arr_tz}), so the arrival date "
                "cannot be guessed from the clock — an arrival earlier than its "
                "departure may be the same day or two days later. Pass arrival_date."
            )
        finish = (date.fromisoformat(start) + timedelta(days=1)).isoformat()
        rolled = True

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
        # The trip supplies the destination when the caller did not name one —
        # a hotel scheduled on a Lisbon trip is a place in Lisbon.
        pid = _resolve_place(
            conn, place_id, google_place_id, title, "", "", "", trip_id=tid
        )
    elif title.strip():
        kind = kind or "note"
    else:
        raise TravelError(
            "schedule needs something to schedule: a place_id, a google_place_id, "
            "or a title."
        )
    if not kind:
        kind = "place" if pid else "note"

    if pid:
        clash = conn.execute(
            "SELECT entry_id, start_time FROM itinerary WHERE trip_id = ? AND place_id = ? "
            "AND start_date = ?",
            (tid, pid, start),
        ).fetchone()
        if clash:
            # Scheduling the same place on the same day twice is nearly always a
            # repeat of the request, not a second visit. Say so rather than
            # quietly building two identical cards.
            at = f" at {clash['start_time']}" if clash["start_time"] else ""
            raise TravelError(
                f"That place is already on {start}{at} [entry {clash['entry_id']}]. "
                "Reschedule that entry, or pick another day — the same place on two "
                "different days is fine."
            )
    cur = conn.execute(
        "INSERT INTO itinerary(trip_id, place_id, item_type, title, start_date, end_date, "
        "start_time, end_time, departure_timezone, arrival_timezone, from_location, "
        "to_location, confirmation_code, notes) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            tid, pid, kind, title.strip() or None, start, finish, st, et,
            dep_tz, arr_tz, from_location.strip() or None, to_location.strip() or None,
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
    over = f" Ends {et} the next day ({finish})." if rolled else ""
    dur = _duration(start, st, finish, et, dep_tz or trip["timezone"], arr_tz or trip["timezone"])
    took = f" Duration {dur}." if dur else ""
    return (
        f"Scheduled '{label}' ({kind}) on {where}{at} [entry {cur.lastrowid}]."
        + over + took + _window_note(trip, start, finish) + booked
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
        # Moving something preserves how long it lasts. Without this a stay
        # keeps its old checkout and a night flight keeps its old arrival —
        # which, once the item moves past it, is a date before its own start.
        if finish and not end_date.strip():
            delta = date.fromisoformat(start) - date.fromisoformat(entry["start_date"])
            finish = (date.fromisoformat(finish) + delta).isoformat()
            sets.append("end_date = ?"); args.append(finish)
            said.append(f"ending {finish}")
    if end_date.strip():
        finish = _parse_date(end_date, "end_date").isoformat()
        sets.append("end_date = ?"); args.append(finish)
        said.append(f"end date → {finish}")
    if finish and finish < start:
        raise TravelError(f"end_date {finish} is before date {start}.")
    st, et = entry["start_time"], entry["end_time"]
    if start_time.strip():
        st = _parse_time(start_time, "start_time")
        sets.append("start_time = ?"); args.append(st)
        said.append(f"from {st}")
    if end_time.strip():
        et = _parse_time(end_time, "end_time")
        sets.append("end_time = ?"); args.append(et)
        said.append(f"until {et}")
    # Same rollover reading as schedule. This path had no time-order check at
    # all, so it silently stored an end before its start — the two ways in must
    # agree or an item means something different depending on how it was made.
    if st and et and et < st and not finish:
        finish = (date.fromisoformat(start) + timedelta(days=1)).isoformat()
        sets.append("end_date = ?"); args.append(finish)
        said.append(f"ending the next day ({finish})")

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
