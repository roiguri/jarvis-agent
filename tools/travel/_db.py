"""Travel storage: the connection, the schema, and the helpers every tool shares.

Private to the skill. Each tool module owns one table and imports what it needs
from here, so the schema and the refusal vocabulary exist in exactly one place.
"""

import os
import sqlite3
from datetime import date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import config

DB_PATH = os.path.join(config.DATA_DIR, "travel", "travel.sqlite")

# Read-only access for the ad-hoc query tool: a separate connection opened in
# SQLite read-only URI mode, so writes are physically impossible (not policy).
TRAVEL_RO_URI = f"file:{DB_PATH}?mode=ro"
QUERY_ROW_CAP = 200


class TravelError(Exception):
    """A refusal phrased for the model: it says what was wrong and, where the
    fix is a different argument, what the valid ones are."""


def _get_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # Off by default in SQLite, and per-connection rather than per-database, so
    # it has to be set here on every open. Without it the schema's REFERENCES
    # clauses are documentation: a wishlist row could outlive the trip it names.
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _init_db():
    conn = _get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trips (
            trip_id     TEXT PRIMARY KEY,
            destination TEXT NOT NULL,
            timezone    TEXT,
            start_date  DATE,
            end_date    DATE,
            status      TEXT NOT NULL DEFAULT 'draft'
                            CHECK(status IN ('draft', 'archived')),
            is_current  INTEGER NOT NULL DEFAULT 0,
            notes       TEXT,
            created_at  DATETIME DEFAULT (datetime('now'))
        );

        -- One current trip, enforced by the database rather than by discipline.
        -- Partial index: only rows with is_current = 1 participate, so any
        -- number of trips may sit at 0.
        CREATE UNIQUE INDEX IF NOT EXISTS one_current_trip
            ON trips(is_current) WHERE is_current = 1;

        -- A place is a fact about the world, so it carries no trip: the same
        -- place is reachable from every trip that ever references it, and its
        -- address is corrected in one row rather than in each mention.
        CREATE TABLE IF NOT EXISTS places (
            place_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            google_place_id TEXT UNIQUE,
            title           TEXT NOT NULL,
            address         TEXT,
            maps_url        TEXT,
            lat             REAL,
            lng             REAL,
            category        TEXT,
            google_type     TEXT,
            created_at      DATETIME DEFAULT (datetime('now'))
        );

        -- Wanting to go somewhere on a given trip. Independent of whether it is
        -- also scheduled — scheduling a place never consumes its wishlist row.
        CREATE TABLE IF NOT EXISTS wishlist (
            wishlist_id INTEGER PRIMARY KEY AUTOINCREMENT,
            trip_id     TEXT    NOT NULL REFERENCES trips(trip_id),
            place_id    INTEGER NOT NULL REFERENCES places(place_id),
            notes       TEXT,
            priority    INTEGER,
            added_at    DATETIME DEFAULT (datetime('now')),
            UNIQUE(trip_id, place_id)
        );

        -- Something happening at a time. A place may have several of these (the
        -- same cafe on two mornings); a transit leg or a note has none at all,
        -- which is why place_id is nullable and title covers those rows.
        -- end_date NULL means a single day; a stay spans start_date..end_date.
        CREATE TABLE IF NOT EXISTS itinerary (
            entry_id          INTEGER PRIMARY KEY AUTOINCREMENT,
            trip_id           TEXT    NOT NULL REFERENCES trips(trip_id),
            place_id          INTEGER REFERENCES places(place_id),
            item_type         TEXT    NOT NULL
                                  CHECK(item_type IN ('place','lodging','transit','note')),
            title             TEXT,
            start_date        DATE    NOT NULL,
            end_date          DATE,
            start_time        TEXT,
            end_time          TEXT,
            origin            TEXT,
            destination_loc   TEXT,
            confirmation_code TEXT,
            notes             TEXT,
            created_at        DATETIME DEFAULT (datetime('now')),
            -- A row with neither a place nor a title cannot be rendered or
            -- described; refused here rather than surfacing as a blank card.
            CHECK (place_id IS NOT NULL OR title IS NOT NULL)
        );

        CREATE INDEX IF NOT EXISTS itinerary_by_trip_date
            ON itinerary(trip_id, start_date);
        CREATE INDEX IF NOT EXISTS wishlist_by_trip
            ON wishlist(trip_id);
    """)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _parse_date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise TravelError(f"{field} must be YYYY-MM-DD, got {value!r}.")


def _validate_window(start_date: str, end_date: str) -> tuple[str | None, str | None]:
    """Both dates or neither. A half-dated trip cannot answer "is this trip
    dated?", which is the question scheduling turns on."""
    s_raw, e_raw = start_date.strip(), end_date.strip()
    if not s_raw and not e_raw:
        return None, None
    if bool(s_raw) != bool(e_raw):
        raise TravelError("Give both start_date and end_date, or neither.")
    s, e = _parse_date(s_raw, "start_date"), _parse_date(e_raw, "end_date")
    if e < s:
        raise TravelError(f"end_date {e} is before start_date {s}.")
    return s.isoformat(), e.isoformat()


def _validate_tz(timezone: str) -> str | None:
    tz = (timezone or "").strip()
    if not tz:
        return None
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise TravelError(
            f"Unknown timezone {tz!r}. Use an IANA name such as 'Europe/Lisbon' or 'Asia/Tokyo'."
        )
    return tz


# ---------------------------------------------------------------------------
# Shared reads — every tool resolves a trip the same way, and every refusal
# shows the same list, so error text and `list` output cannot drift apart.
# ---------------------------------------------------------------------------


def _trip_lines(conn: sqlite3.Connection) -> str:
    """Every trip as one line each."""
    rows = conn.execute(
        "SELECT trip_id, destination, status, start_date, end_date, is_current, timezone "
        "FROM trips ORDER BY is_current DESC, COALESCE(start_date, '9999'), trip_id"
    ).fetchall()
    if not rows:
        return "(no trips yet)"
    out = []
    for r in rows:
        when = (
            f"{r['start_date']} to {r['end_date']}"
            if r["start_date"] and r["end_date"]
            else "no dates"
        )
        marks = []
        if r["is_current"]:
            marks.append("CURRENT")
        if r["status"] == "archived":
            marks.append("archived")
        if r["timezone"]:
            marks.append(r["timezone"])
        suffix = f"  [{', '.join(marks)}]" if marks else ""
        out.append(f"- {r['trip_id']}: {r['destination']} — {when}{suffix}")
    return "\n".join(out)


def _require_trip(conn: sqlite3.Connection, trip_id: str) -> sqlite3.Row:
    trip_id = (trip_id or "").strip()
    if not trip_id:
        raise TravelError(f"A trip_id is required. Existing trips:\n{_trip_lines(conn)}")
    row = conn.execute("SELECT * FROM trips WHERE trip_id = ?", (trip_id,)).fetchone()
    if row is None:
        raise TravelError(
            f"No trip {trip_id!r}. Existing trips:\n{_trip_lines(conn)}\n"
            "Pass one of these ids, or create the trip first."
        )
    return row


def _label(row: sqlite3.Row) -> str:
    """How an itinerary row is named back to the model. A transit leg or note
    carries its own title; a row standing in for a place has none of its own
    until the join, so it falls back to its id rather than reading as blank."""
    title = row["title"] or f"entry {row['entry_id']}"
    return f"{title} on {row['start_date']}"


# At import, so the schema exists before the read-only connection is ever
# opened — mode=ro fails outright on a missing file, which on a fresh instance
# would read as a broken tool rather than an empty database.
_init_db()
