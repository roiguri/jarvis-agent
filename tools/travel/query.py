"""Ad-hoc read-only SQL over the travel database."""

import re
import sqlite3

from langchain_core.tools import tool

from tools.registry import tool_register
from tools.travel._db import QUERY_ROW_CAP, TRAVEL_RO_URI


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
        conn = sqlite3.connect(TRAVEL_RO_URI, uri=True)
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
            rows = cur.fetchmany(QUERY_ROW_CAP + 1)
            truncated = len(rows) > QUERY_ROW_CAP
            rows = rows[:QUERY_ROW_CAP]
            if not rows:
                return "(0 rows)"
            header = " | ".join(cols)
            body = "\n".join(
                " | ".join("" if v is None else str(v) for v in r) for r in rows
            )
            tail = (
                f"\n\n[truncated to {QUERY_ROW_CAP} rows — add LIMIT or aggregate]"
                if truncated else f"\n\n({len(rows)} row(s))"
            )
            return f"{header}\n{body}{tail}"
        finally:
            conn.close()
    except sqlite3.OperationalError as e:
        return f"Error: read-only query rejected ({e})."
    except Exception as e:
        return f"Error: {e}"
