"""The trips table: creating trips, pointing the tile at one, moving dates."""

import sqlite3
from datetime import date

from langchain_core.tools import tool

from tools.registry import tool_register
from tools.travel.destinations import _require_destination
from tools.travel._db import (
    TravelError,
    _get_db,
    _label,
    _require_trip,
    _trip_lines,
    _validate_window,
)


def _set_current(conn: sqlite3.Connection, trip_id: str) -> None:
    """Clear the flag, then set it. Order matters: the partial unique index is
    checked per statement, not at commit, so setting first would collide with
    the trip already holding it."""
    conn.execute("UPDATE trips SET is_current = 0 WHERE is_current = 1")
    conn.execute("UPDATE trips SET is_current = 1 WHERE trip_id = ?", (trip_id,))


def _shift_itinerary(conn: sqlite3.Connection, trip_id: str, delta_days: int) -> tuple[int, list[str]]:
    """Move this trip's scheduled rows by delta_days, leaving booked ones put.

    A row carrying a confirmation_code is a reservation someone else holds: the
    trip moving does not move the flight, so those are reported for rebooking
    rather than quietly re-dated into a lie. Returns (moved, [booked labels])."""
    booked = conn.execute(
        "SELECT entry_id, title, place_id, start_date, confirmation_code FROM itinerary "
        "WHERE trip_id = ? AND confirmation_code IS NOT NULL AND TRIM(confirmation_code) != ''",
        (trip_id,),
    ).fetchall()
    labels = [_label(r) + f" ({r['confirmation_code']})" for r in booked]
    cur = conn.execute(
        "UPDATE itinerary SET "
        "  start_date = date(start_date, ?), "
        "  end_date   = CASE WHEN end_date IS NULL THEN NULL ELSE date(end_date, ?) END "
        "WHERE trip_id = ? "
        "  AND (confirmation_code IS NULL OR TRIM(confirmation_code) = '')",
        (f"{delta_days:+d} days", f"{delta_days:+d} days", trip_id),
    )
    return cur.rowcount, labels


def _outside_window(conn: sqlite3.Connection, trip_id: str, start: str, end: str) -> list[str]:
    """Scheduled rows that fall outside start..end. Reported, never moved — an
    edge day is legitimate (a red-eye the night before), so this is information
    rather than an error."""
    rows = conn.execute(
        "SELECT entry_id, title, start_date FROM itinerary "
        "WHERE trip_id = ? AND (start_date < ? OR start_date > ?) ORDER BY start_date",
        (trip_id, start, end),
    ).fetchall()
    return [_label(r) for r in rows]


@tool_register(namespace="travel", destructive=True)
@tool
def manage_trip(
    action: str,
    trip_id: str = "",
    destination: str = "",
    start_date: str = "",
    end_date: str = "",
    notes: str = "",
    title: str = "",
) -> str:
    """Create and manage trips. One trip is 'current' at a time — that is the one
    the travel tile shows.

    Actions:
    - list: every trip, with dates, timezone, and which is current. Call this
      first whenever you don't already know the exact trip_id.
    - create: needs trip_id and destination. trip_id is a short slug you choose
      (e.g. 'lisbon_spring'). destination must be one that already exists — call
      manage_destination(action='list') to see them, and create it there first if
      it is new. The timezone comes from the destination, so it is not set here.
      Dates are optional — a trip with no dates is a "someday" bucket you can
      add wishlist places to but cannot schedule into. Becomes current only if
      no other trip is; creating a trip never steals the pointer from the trip
      the owner is currently looking at.
    - update: change destination, dates, title or notes. Moving a dated trip
      by a fixed offset drags its scheduled items along, EXCEPT items holding a
      confirmation_code — a booking does not move because plans changed, so
      those are listed for rebooking instead.
    - set_current: point the tile at this trip. Use when the owner asks to look
      at a different trip.
    - archive: mark a trip finished. Destroys nothing and is reversible via
      update. Prefer this over delete.
    - delete: permanently remove the trip AND its wishlist and itinerary rows.
      Saved places survive (they belong to no trip). Asks the owner to confirm.

    Args:
        action: list | create | update | set_current | archive | delete
        trip_id: the trip's id — required for everything except list.
        destination: the name of an existing destination. Never invent one — list
            them first, and create a missing one with manage_destination.
        start_date: YYYY-MM-DD. Give both dates or neither.
        end_date: YYYY-MM-DD.
        notes: free text about the trip as a whole.
    """
    action = (action or "").strip().lower()
    if action not in ("list", "create", "update", "set_current", "archive", "delete",):
        # Checked before anything else is required: an unknown action must not
        # be reported as a missing id, which is what the model would then try to
        # fix.
        return f"Error: Unknown action {action!r}. Use one of: list, create, update, set_current, archive, delete."
    conn = _get_db()
    try:
        try:
            if action == "list":
                return _trip_lines(conn)

            if action == "create":
                return _create_trip(
                    conn, trip_id, destination, start_date, end_date, notes, title
                )

            trip = _require_trip(conn, trip_id)
            tid = trip["trip_id"]

            if action == "set_current":
                _set_current(conn, tid)
                conn.commit()
                return f"{tid} is now the current trip."

            if action == "archive":
                # An archived trip must not stay pinned: the tile would keep
                # presenting something explicitly marked finished.
                conn.execute(
                    "UPDATE trips SET status = 'archived', is_current = 0 WHERE trip_id = ?",
                    (tid,),
                )
                conn.commit()
                freed = " It is no longer the current trip." if trip["is_current"] else ""
                return f"Archived {tid}.{freed}"

            if action == "update":
                return _update_trip(conn, trip, destination, start_date, end_date, notes, title)

            if action == "delete":
                return _delete_trip(conn, trip)

        except TravelError as e:
            return f"Error: {e}"
    finally:
        conn.close()


def _create_trip(
    conn: sqlite3.Connection,
    trip_id: str,
    destination: str,
    start_date: str,
    end_date: str,
    notes: str,
    title: str,
) -> str:
    tid = (trip_id or "").strip()
    if not tid or not destination.strip():
        raise TravelError("create needs both trip_id and destination.")
    dest = _require_destination(conn, destination.strip())
    if conn.execute("SELECT 1 FROM trips WHERE trip_id = ?", (tid,)).fetchone():
        raise TravelError(
            f"Trip {tid!r} already exists. Use action='update', or pick another id."
        )
    s, e = _validate_window(start_date, end_date)
    # Claimed only when nothing holds it: a new trip must not yank the tile away
    # from the one being looked at.
    held = conn.execute("SELECT 1 FROM trips WHERE is_current = 1").fetchone()
    conn.execute(
        "INSERT INTO trips(trip_id, title, destination_id, start_date, end_date, "
        "notes, is_current) VALUES(?,?,?,?,?,?,?)",
        (tid, title.strip() or None, dest["destination_id"], s, e,
         notes.strip() or None, 0 if held else 1),
    )
    conn.commit()
    claim = "" if held else " It is now the current trip."
    when = f"{s} to {e}" if s else "no dates yet (cannot schedule until dates are set)"
    return (
        f"Created trip {tid!r} — {dest['name']} ({dest['timezone']}), {when}.{claim}"
    )


def _update_trip(
    conn: sqlite3.Connection,
    trip: sqlite3.Row,
    destination: str,
    start_date: str,
    end_date: str,
    notes: str,
    title: str,
) -> str:
    tid = trip["trip_id"]
    sets, args, said = [], [], []

    if destination.strip():
        dest = _require_destination(conn, destination.strip())
        sets.append("destination_id = ?"); args.append(dest["destination_id"])
        said.append(f"destination → {dest['name']}")
    if title.strip():
        sets.append("title = ?"); args.append(title.strip())
        said.append(f"title → {title.strip()}")
    if notes.strip():
        sets.append("notes = ?"); args.append(notes.strip())
        said.append("notes updated")

    if start_date.strip() or end_date.strip():
        new_s, new_e = _validate_window(start_date, end_date)
        old_s, old_e = trip["start_date"], trip["end_date"]
        sets.append("start_date = ?"); args.append(new_s)
        sets.append("end_date = ?"); args.append(new_e)
        said.append(f"dates → {new_s} to {new_e}")

        if old_s and old_e:
            delta = (date.fromisoformat(new_s) - date.fromisoformat(old_s)).days
            same_length = (
                date.fromisoformat(new_e) - date.fromisoformat(new_s)
            ) == (date.fromisoformat(old_e) - date.fromisoformat(old_s))
            if delta and same_length:
                moved, booked = _shift_itinerary(conn, tid, delta)
                said.append(f"moved {moved} scheduled item(s) by {delta:+d} day(s)")
                if booked:
                    said.append(
                        "did NOT move these — they carry a confirmation code and need "
                        "rebooking:\n  " + "\n  ".join(booked)
                    )
            elif not same_length:
                stray = _outside_window(conn, tid, new_s, new_e)
                said.append("trip length changed, so no items were moved")
                if stray:
                    said.append(
                        "these now fall outside the trip window:\n  " + "\n  ".join(stray)
                    )

    if not sets:
        return f"Nothing to update on {tid} — pass a field to change."
    conn.execute(f"UPDATE trips SET {', '.join(sets)} WHERE trip_id = ?", (*args, tid))
    conn.commit()
    return f"Updated {tid}: " + "; ".join(said) + "."


def _delete_trip(conn: sqlite3.Connection, trip: sqlite3.Row) -> str:
    tid = trip["trip_id"]
    n_wish = conn.execute("SELECT COUNT(*) FROM wishlist WHERE trip_id = ?", (tid,)).fetchone()[0]
    n_itin = conn.execute("SELECT COUNT(*) FROM itinerary WHERE trip_id = ?", (tid,)).fetchone()[0]

    from gateway.factory import get_confirmation

    async def _do_delete() -> str:
        import asyncio

        return await asyncio.to_thread(_exec_delete_trip, tid)

    try:
        return get_confirmation().request_confirmation_sync(
            description=(
                f"Permanently delete trip '{tid}' ({trip['destination']}).\n\n"
                f"This also removes {n_wish} wishlist item(s) and {n_itin} scheduled item(s).\n"
                "Saved places are kept — they belong to no trip."
            ),
            action_fn=_do_delete,
            result_ok_text=f"Trip '{tid}' deleted.",
            result_cancel_text=f"Deletion of '{tid}' cancelled — nothing changed.",
        )
    except Exception as e:
        return f"Error requesting delete confirmation: {e}"


def _exec_delete_trip(trip_id: str) -> str:
    """Children first: the foreign keys are enforced, so deleting the trip while
    a wishlist or itinerary row still points at it would be rejected.

    Opens its own connection because it runs later, on another thread, only if
    the owner taps Confirm — the connection that built the request is long gone.
    """
    conn = _get_db()
    try:
        n_itin = conn.execute("DELETE FROM itinerary WHERE trip_id = ?", (trip_id,)).rowcount
        n_wish = conn.execute("DELETE FROM wishlist WHERE trip_id = ?", (trip_id,)).rowcount
        conn.execute("DELETE FROM trips WHERE trip_id = ?", (trip_id,))
        conn.commit()
        return f"Deleted {trip_id} with {n_wish} wishlist and {n_itin} scheduled item(s)."
    except Exception as e:
        return f"Delete failed: {e}"
    finally:
        conn.close()
