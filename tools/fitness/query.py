"""Ad-hoc read-only SQL over the fitness database."""

import re
import sqlite3

from langchain_core.tools import tool

from tools.fitness._db import _FITNESS_RO_URI, _QUERY_ROW_CAP
from tools.registry import tool_register


@tool_register(namespace="fitness")
@tool
def query_fitness_db(sql: str = "") -> str:
    """Run a read-only SELECT against the fitness DB for ad-hoc analysis.

    Use for questions the fixed tools don't cover: arbitrary date ranges,
    cross-table joins, custom aggregates, trends. For routine checks prefer
    get_weekly_fitness_summary / get_adherence_report / query_exercise_history.

    Call with an EMPTY sql to get the LIVE schema (tables, columns, row counts)
    — do this first if unsure of column names; the live schema is authoritative
    (the canonical source can drift).

    Rules:
    - Read-only: a single SELECT or WITH...SELECT only. No writes, no PRAGMA,
      no ATTACH, no multiple statements. The connection is physically read-only.
    - Results cap at 200 rows; add your own LIMIT/aggregation if you hit it.
    - workouts.scheduled_time is Israel-local 'YYYY-MM-DD HH:MM:00';
      status in ('scheduled','completed','missed'); source in ('arbox','manual').

    Args:
        sql: A single read-only SELECT/WITH. Empty string = describe schema.
    """
    try:
        conn = sqlite3.connect(_FITNESS_RO_URI, uri=True)
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
                return "Error: ATTACH/DETACH not allowed (fitness DB only)."

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
