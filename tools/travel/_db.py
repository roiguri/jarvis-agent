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
        -- Somewhere you travel to. A city or a country, whichever granularity
        -- suits the trip; it carries the timezone every local time is read in.
        CREATE TABLE IF NOT EXISTS destinations (
            destination_id  INTEGER PRIMARY KEY AUTOINCREMENT,
            -- Case-insensitively unique, so "Tokyo" and "tokyo" cannot become
            -- two rows and strand a returning trip with an empty wishlist.
            name            TEXT NOT NULL COLLATE NOCASE UNIQUE,
            kind            TEXT CHECK(kind IN ('city', 'region', 'country')),
            country         TEXT,
            -- NOT NULL because the whole time model defaults to it: an item's
            -- local time is meaningless without knowing whose local it is.
            timezone        TEXT NOT NULL,
            lat             REAL,
            lng             REAL,
            -- What Google called the area. Evidence about a place, never used
            -- to decide which destination it belongs to.
            google_locality TEXT,
            created_at      DATETIME DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS trips (
            trip_id        TEXT PRIMARY KEY,
            title          TEXT,
            destination_id INTEGER NOT NULL REFERENCES destinations(destination_id),
            start_date     DATE,
            end_date       DATE,
            status         TEXT NOT NULL DEFAULT 'draft'
                               CHECK(status IN ('draft', 'archived')),
            is_current     INTEGER NOT NULL DEFAULT 0,
            notes          TEXT,
            created_at     DATETIME DEFAULT (datetime('now')),
            CHECK (end_date IS NULL OR start_date IS NULL OR end_date >= start_date)
        );

        -- One current trip, enforced by the database rather than by discipline.
        CREATE UNIQUE INDEX IF NOT EXISTS one_current_trip
            ON trips(is_current) WHERE is_current = 1;

        -- A place is a fact about the world and carries no trip, so the same
        -- place is reachable from every trip that references it and its address
        -- is corrected in one row rather than in each mention.
        CREATE TABLE IF NOT EXISTS places (
            place_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            google_place_id TEXT UNIQUE,
            destination_id  INTEGER NOT NULL REFERENCES destinations(destination_id),
            title           TEXT NOT NULL,
            address         TEXT,
            maps_url        TEXT,
            lat             REAL,
            lng             REAL,
            category        TEXT,
            google_type       TEXT,
            google_type_label TEXT,
            google_types      TEXT,
            city              TEXT,
            country           TEXT,
            created_at      DATETIME DEFAULT (datetime('now'))
        );

        -- Wanting to go somewhere on a given trip. Independent of whether it is
        -- also scheduled — scheduling never consumes its wishlist row.
        -- Wanting to go somewhere. Anchored to the DESTINATION, not a trip, so
        -- returning to a city finds the list you left rather than an empty page.
        CREATE TABLE IF NOT EXISTS wishlist (
            wishlist_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            destination_id INTEGER NOT NULL REFERENCES destinations(destination_id),
            -- NULL for an intention with no place yet: "somewhere with a view".
            place_id       INTEGER REFERENCES places(place_id),
            -- Overrides the place's name; the row's own name when there is none.
            title          TEXT,
            -- Overrides the place's city, which groups the list. Google files a
            -- Tokyo venue under its ward, so this is how one gets grouped where
            -- the owner would look for it without touching the shared place.
            city           TEXT,
            notes          TEXT,
            priority       INTEGER DEFAULT 3 CHECK(priority BETWEEN 1 AND 5),
            -- Set when you have actually been. remove means "changed my mind";
            -- this means "went" — without it a destination list never resolves
            -- and reads as archaeology after the third visit.
            done_at        DATE,
            added_at       DATETIME DEFAULT (datetime('now')),
            UNIQUE(destination_id, place_id),
            -- The first UNIQUE cannot constrain placeless rows: SQLite treats
            -- NULLs as distinct, so "somewhere with a view" could be added
            -- unboundedly without this one.
            UNIQUE(destination_id, title),
            CHECK (place_id IS NOT NULL OR title IS NOT NULL)
        );

        -- Something happening at a time. A place may have several of these; a
        -- transit leg or a note has none, which is why place_id is nullable and
        -- title covers those rows.
        CREATE TABLE IF NOT EXISTS itinerary (
            entry_id          INTEGER PRIMARY KEY AUTOINCREMENT,
            trip_id           TEXT    NOT NULL REFERENCES trips(trip_id),
            place_id          INTEGER REFERENCES places(place_id),
            item_type         TEXT    NOT NULL
                                  CHECK(item_type IN ('place','lodging','transit','note')),
            title             TEXT,
            start_date        DATE    NOT NULL,
            end_date          DATE,
            -- For every item_type except lodging, both times sit within the
            -- same day (or roll into end_date past midnight). For lodging they
            -- do not: start_time is check-in, on start_date; end_time is
            -- check-out, on end_date. Those are independent facts about two
            -- different dates, not one interval — a stay can carry a checkout
            -- time with no checkin time on file, or vice versa.
            start_time        TEXT,
            end_time          TEXT,
            -- Transit is the only thing with two ends, so it is the only thing
            -- that carries zones. NULL means the trip's own, which is right for
            -- everything internal and wrong only for the flights in and out.
            departure_timezone TEXT,
            arrival_timezone   TEXT,
            -- from_/to_ rather than origin/destination_loc: `destination` names
            -- a destinations row everywhere else in this schema, and one word
            -- meaning two kinds of thing is what gets them crossed.
            from_location     TEXT,
            to_location       TEXT,
            confirmation_code TEXT,
            notes             TEXT,
            created_at        DATETIME DEFAULT (datetime('now')),
            CHECK (place_id IS NOT NULL OR title IS NOT NULL),
            CHECK (end_date IS NULL OR end_date >= start_date),
            -- An end with no start is not a time, it is half of one — true for
            -- an interval, false for a stay: lodging's checkout and checkin are
            -- independent facts on different dates, and knowing one without the
            -- other is normal, so lodging is exempted.
            CHECK (item_type = 'lodging' OR start_time IS NOT NULL OR end_time IS NULL)
        );

        CREATE INDEX IF NOT EXISTS itinerary_by_trip_date
            ON itinerary(trip_id, start_date);
        CREATE INDEX IF NOT EXISTS wishlist_by_destination
            ON wishlist(destination_id);
        CREATE INDEX IF NOT EXISTS places_by_destination
            ON places(destination_id);
    """)
    conn.commit()
    _migrate_lodging_check(conn)
    conn.close()


def _migrate_lodging_check(conn: sqlite3.Connection) -> None:
    """Loosen the itinerary CHECK for lodging on a database created before it
    existed. SQLite has no ALTER TABLE for a CHECK constraint — only a full
    rebuild changes one — so this is a one-time, idempotent table swap, gated
    on the live schema text rather than a version number that could drift from
    what actually ran.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'itinerary'"
    ).fetchone()
    if row is None or "item_type = 'lodging'" in row[0]:
        return
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript("""
        BEGIN;
        CREATE TABLE itinerary_new (
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
            departure_timezone TEXT,
            arrival_timezone   TEXT,
            from_location     TEXT,
            to_location       TEXT,
            confirmation_code TEXT,
            notes             TEXT,
            created_at        DATETIME DEFAULT (datetime('now')),
            CHECK (place_id IS NOT NULL OR title IS NOT NULL),
            CHECK (end_date IS NULL OR end_date >= start_date),
            CHECK (item_type = 'lodging' OR start_time IS NOT NULL OR end_time IS NULL)
        );
        INSERT INTO itinerary_new SELECT * FROM itinerary;
        DROP TABLE itinerary;
        ALTER TABLE itinerary_new RENAME TO itinerary;
        CREATE INDEX IF NOT EXISTS itinerary_by_trip_date
            ON itinerary(trip_id, start_date);
        COMMIT;
    """)
    conn.execute("PRAGMA foreign_keys = ON")


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
        "SELECT t.trip_id, t.title, t.status, t.start_date, t.end_date, t.is_current, "
        "       d.name AS destination, d.timezone "
        "FROM trips t JOIN destinations d ON d.destination_id = t.destination_id "
        "ORDER BY t.is_current DESC, COALESCE(t.start_date, '9999'), t.trip_id"
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
        label = r["title"] or r["destination"]
        out.append(f"- {r['trip_id']}: {label} — {when}{suffix}")
    return "\n".join(out)


def _require_trip(conn: sqlite3.Connection, trip_id: str) -> sqlite3.Row:
    trip_id = (trip_id or "").strip()
    if not trip_id:
        raise TravelError(f"A trip_id is required. Existing trips:\n{_trip_lines(conn)}")
    # Joined, not raw: the timezone lives on the destination now, and every
    # caller that has a trip needs it. One place, so nothing has to remember.
    row = conn.execute(
        "SELECT t.*, d.name AS destination, d.timezone, d.country, d.kind "
        "FROM trips t JOIN destinations d ON d.destination_id = t.destination_id "
        "WHERE t.trip_id = ?",
        (trip_id,),
    ).fetchone()
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
