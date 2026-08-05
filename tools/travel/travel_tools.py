import os
import re
import sqlite3

from langchain_core.tools import tool

import config
from tools.registry import tool_register

DB_PATH = os.path.join(config.DATA_DIR, "travel", "travel.sqlite")

# Read-only access for the ad-hoc query tool: a separate connection opened in
# SQLite read-only URI mode, so writes are physically impossible (not policy).
_TRAVEL_RO_URI = f"file:{DB_PATH}?mode=ro"
_QUERY_ROW_CAP = 200


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


@tool_register(namespace="travel")
@tool
def query_travel_db(sql: str = "") -> str:
    """Run a read-only SELECT against the travel DB for ad-hoc analysis.

    Use for questions the fixed tools don't cover: cross-trip totals, custom
    aggregates, "which places have I saved but never scheduled".

    Call with an EMPTY sql to get the LIVE schema (tables, columns, row counts)
    — do this first if unsure of column names; the live schema is authoritative.

    Rules:
    - Read-only: a single SELECT or WITH...SELECT only. No writes, no PRAGMA,
      no ATTACH, no multiple statements. The connection is physically read-only.
    - Results cap at 200 rows; add your own LIMIT/aggregation if you hit it.
    - Times (start_time/end_time) are 'HH:MM' local to the destination and are
      never converted; dates are 'YYYY-MM-DD'.
    - A place carries no trip_id — join through wishlist or itinerary to scope
      it to one. itinerary.place_id is NULL for transit legs and notes.

    Args:
        sql: A single read-only SELECT/WITH. Empty string = describe schema.
    """
    try:
        conn = sqlite3.connect(_TRAVEL_RO_URI, uri=True)
        conn.row_factory = sqlite3.Row
        try:
            if not sql or not sql.strip():
                tables = conn.execute(
                    "SELECT name, sql FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall()
                parts = []
                for t in tables:
                    n = conn.execute(f"SELECT COUNT(*) FROM {t['name']}").fetchone()[0]
                    parts.append(f"-- {t['name']} ({n} rows)\n{t['sql']}")
                return "\n\n".join(parts)

            stripped = sql.strip().rstrip(";").strip()
            if ";" in stripped:
                return "Error: only a single statement is allowed (no ';')."
            low = stripped.lower()
            if not (low.startswith("select") or low.startswith("with")):
                return "Error: only SELECT / WITH queries are allowed."
            if re.search(r"\b(attach|detach)\b", low):
                return "Error: ATTACH/DETACH not allowed (travel DB only)."

            cur = conn.execute(stripped)
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchmany(_QUERY_ROW_CAP + 1)
            truncated = len(rows) > _QUERY_ROW_CAP
            rows = rows[:_QUERY_ROW_CAP]
            if not rows:
                return "(0 rows)"
            header = " | ".join(cols)
            body = "\n".join(
                " | ".join("" if v is None else str(v) for v in r) for r in rows
            )
            tail = (
                f"\n\n[truncated to {_QUERY_ROW_CAP} rows — add LIMIT or aggregate]"
                if truncated else f"\n\n({len(rows)} row(s))"
            )
            return f"{header}\n{body}{tail}"
        finally:
            conn.close()
    except sqlite3.OperationalError as e:
        return f"Error: read-only query rejected ({e})."
    except Exception as e:
        return f"Error: {e}"


# At import, so the schema exists before the read-only connection above is ever
# opened — mode=ro fails outright on a missing file, which on a fresh instance
# would read as a broken tool rather than an empty database.
_init_db()
