"""The travel app — a read-only view of one trip, shaped for a screen.

Reads the travel database directly rather than calling the travel *tools*: their
output is English written for a model, and parsing it here would ship a client
that breaks when a docstring is reworded. Same reasoning as `memory.py` walking
MEMORY_DIR instead of calling the memory tools.

THE SECURITY LINE differs from memory's. Nothing here resolves a path, so there
is no sandbox to escape; the only caller-supplied value is a trip id, and it
reaches SQLite exclusively as a bound parameter. The connection is opened
read-only at the URI level besides, so a device cannot write to this database
even if a handler were wrong about what it was doing.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from gateway.apps.registry import AppEntry, AppNotFound, AppSpec, register_app

# Importing this also creates the schema if it is missing (tools.travel._db runs
# _init_db at import), which is what lets the read-only connection below open at
# all on an instance that has never used the skill.
from tools.travel._db import TRAVEL_RO_URI
from tools.travel.places import CATEGORIES

# Where "today" falls when a trip names no timezone. Matches the tools: right for
# a domestic trip, and the only sane default for one that never said.
_FALLBACK_TZ = "Asia/Jerusalem"

_CATEGORY_ORDER = {name: i for i, name in enumerate(CATEGORIES)}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(TRAVEL_RO_URI, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _today_in(timezone: str | None) -> str:
    """Today's date where the trip is, not where the server is.

    The whole reason a trip stores a timezone: for the first hours of a morning
    in a far-east destination, the server's date is still yesterday, and a tile
    that highlights the wrong day is wrong exactly when it is being used.
    """
    try:
        tz = ZoneInfo(timezone or _FALLBACK_TZ)
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo(_FALLBACK_TZ)
    return datetime.now(tz).date().isoformat()


def _position(trip: sqlite3.Row, today: str) -> str:
    """Where today sits relative to the trip: before | during | after | undated.

    The client needs this to decide what to open on, and deriving it here keeps
    that decision next to the timezone it depends on.
    """
    if not trip["start_date"]:
        return "undated"
    if today < trip["start_date"]:
        return "before"
    if today > trip["end_date"]:
        return "after"
    return "during"


def _place_of(r: sqlite3.Row) -> dict[str, Any] | None:
    """The place behind a row, or None for a transit leg or a note — which have
    no place by design and must not be given an empty one to render."""
    if r["place_id"] is None:
        return None
    return {
        "place_id": r["place_id"],
        "title": r["place_title"],
        "address": r["address"],
        "maps_url": r["maps_url"],
        "lat": r["lat"],
        "lng": r["lng"],
        "category": r["category"],
        "type_label": r["google_type_label"],
    }


def _entry(r: sqlite3.Row) -> dict[str, Any]:
    return {
        "entry_id": r["entry_id"],
        "item_type": r["item_type"],
        # Always populated: a place-backed row takes the place's name, everything
        # else carries its own, so the client never has to decide what to show.
        "title": r["title"] or r["place_title"],
        "start_date": r["start_date"],
        "end_date": r["end_date"],
        "start_time": r["start_time"],
        "end_time": r["end_time"],
        "origin": r["origin"],
        "destination_loc": r["destination_loc"],
        "confirmation_code": r["confirmation_code"],
        "notes": r["notes"],
        "place": _place_of(r),
    }


def _day_span(start: str | None, end: str | None) -> list[str]:
    if not start or not end:
        return []
    d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    return [
        date.fromordinal(d0.toordinal() + n).isoformat()
        for n in range((d1 - d0).days + 1)
    ]


def _tile_sync(trip_id: str) -> dict[str, Any]:
    conn = _connect()
    try:
        if trip_id:
            trip = conn.execute(
                "SELECT * FROM trips WHERE trip_id = ?", (trip_id,)
            ).fetchone()
            if trip is None:
                raise AppNotFound(f"No trip {trip_id!r}")
        else:
            trip = conn.execute("SELECT * FROM trips WHERE is_current = 1").fetchone()
            if trip is None:
                # Not an error: having no trip pinned is a legitimate state, and
                # a 404 would make an empty screen indistinguishable from a
                # broken one. The client draws its empty state from trip: null.
                return {"trip": None, "days": [], "lodging": [], "wishlist": []}

        tid = trip["trip_id"]
        today = _today_in(trip["timezone"])

        rows = conn.execute(
            "SELECT i.*, p.title AS place_title, p.address, p.maps_url, p.lat, p.lng, "
            "       p.category, p.google_type_label "
            "FROM itinerary i LEFT JOIN places p ON p.place_id = i.place_id "
            "WHERE i.trip_id = ? "
            # NULL times sort last within a day: an item with no time is not a
            # midnight item, it is an unscheduled part of that day.
            "ORDER BY i.start_date, CASE WHEN i.start_time IS NULL THEN 1 ELSE 0 END, "
            "         i.start_time, i.entry_id",
            (tid,),
        ).fetchall()

        lodging = [_entry(r) for r in rows if r["item_type"] == "lodging"]
        scheduled = [r for r in rows if r["item_type"] != "lodging"]

        # Every day of the trip, plus any day outside it that actually holds
        # something. The date strip is drawn from this, so an empty middle day
        # has to be present or the strip would skip it.
        window = _day_span(trip["start_date"], trip["end_date"])
        extra = sorted({r["start_date"] for r in scheduled} - set(window))
        by_date: dict[str, list[dict]] = {}
        for r in scheduled:
            by_date.setdefault(r["start_date"], []).append(_entry(r))

        days = []
        for d in sorted(set(window) | set(extra)):
            n = None
            if trip["start_date"]:
                n = (date.fromisoformat(d) - date.fromisoformat(trip["start_date"])).days + 1
            days.append({
                "date": d,
                "day_number": n,
                "outside_window": d in extra,
                "is_today": d == today,
                "items": by_date.get(d, []),
            })

        wish = conn.execute(
            "SELECT w.notes, w.priority, p.place_id, p.title, p.address, p.maps_url, "
            "       p.lat, p.lng, p.category, p.google_type_label "
            "FROM wishlist w JOIN places p ON p.place_id = w.place_id "
            "WHERE w.trip_id = ?",
            (tid,),
        ).fetchall()
        groups: dict[str, list[dict]] = {}
        for r in wish:
            groups.setdefault(r["category"] or "unsorted", []).append({
                "place_id": r["place_id"],
                "title": r["title"],
                "address": r["address"],
                "maps_url": r["maps_url"],
                "lat": r["lat"],
                "lng": r["lng"],
                "type_label": r["google_type_label"],
                "notes": r["notes"],
                "priority": r["priority"],
            })
        wishlist = [
            {
                "category": cat,
                "items": sorted(
                    items, key=lambda i: (-(i["priority"] or 0), (i["title"] or "").lower())
                ),
            }
            # Unsorted last, for the same reason the tools list it last: a place
            # nothing could classify belongs at the bottom, not interleaved.
            for cat, items in sorted(
                groups.items(), key=lambda kv: _CATEGORY_ORDER.get(kv[0], len(CATEGORIES))
            )
        ]

        return {
            "trip": {
                "trip_id": tid,
                "destination": trip["destination"],
                "start_date": trip["start_date"],
                "end_date": trip["end_date"],
                "timezone": trip["timezone"],
                "status": trip["status"],
                "is_current": bool(trip["is_current"]),
                "notes": trip["notes"],
                "today": today,
                "position": _position(trip, today),
            },
            "days": days,
            "lodging": lodging,
            "wishlist": wishlist,
        }
    finally:
        conn.close()


# Every call above blocks on SQLite, and one event loop serves the poll, any
# in-flight turn and this drain. Running it inline would re-couple exactly what
# answering queries on their own task decoupled.
async def _tile(params: dict[str, str]) -> Any:
    return await asyncio.to_thread(_tile_sync, (params.get("trip_id") or "").strip())


# Read-only in v1: a GET, so a channel honouring `method` refuses a write
# outright. Writes belong to the chat path, where the confirmation plane already
# guards the destructive ones.
TRAVEL_APP = register_app(
    AppSpec(
        ns="travel",
        name="Travel",
        entries=(
            AppEntry(id="tile", method="GET", params=("trip_id",), handler=_tile),
        ),
    )
)
