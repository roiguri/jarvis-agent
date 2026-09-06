"""Fitness storage: the connection, the schema, and the helpers every tool shares.

Private to the skill. Each tool module imports what it needs from here, so the
schema, the database path, and the shared formatting helpers exist in exactly
one place.
"""

import os
import sqlite3
from datetime import datetime
from timeutils import ISRAEL_TZ

import config

DB_PATH = os.path.join(config.DATA_DIR, "fitness", "fitness.sqlite")

# Read-only access for the ad-hoc query tool: a separate connection opened in
# SQLite read-only URI mode, so writes are physically impossible (not policy).
_FITNESS_RO_URI = f"file:{DB_PATH}?mode=ro"


_QUERY_ROW_CAP = 200


def _get_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db():
    conn = _get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS plans (
            plan_id             INTEGER PRIMARY KEY AUTOINCREMENT,
            name                TEXT NOT NULL,
            tracking_mode       TEXT NOT NULL CHECK(tracking_mode IN ('flexible_quota', 'strict_sequential')),
            weekly_target_count INTEGER,
            status              TEXT DEFAULT 'active' CHECK(status IN ('active', 'paused', 'completed')),
            start_date          DATE,
            binding             TEXT
        );

        CREATE TABLE IF NOT EXISTS workouts (
            workout_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id         INTEGER,
            external_id     TEXT UNIQUE,
            scheduled_time  DATETIME NOT NULL,
            status          TEXT DEFAULT 'scheduled'
                                CHECK(status IN ('scheduled','completed','missed')),
            session_type    TEXT DEFAULT 'crossfit',
            wod_result      TEXT,
            description     TEXT,
            source          TEXT DEFAULT 'arbox',
            notes           TEXT,
            FOREIGN KEY(plan_id) REFERENCES plans(plan_id)
        );

        CREATE TABLE IF NOT EXISTS exercise_logs (
            log_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            workout_id    INTEGER,
            exercise_name TEXT NOT NULL,
            weight        REAL,
            sets          INTEGER,
            reps          INTEGER,
            notes         TEXT,
            logged_at     DATETIME DEFAULT (datetime('now')),
            FOREIGN KEY(workout_id) REFERENCES workouts(workout_id)
        );

        CREATE TABLE IF NOT EXISTS cardio_logs (
            log_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            workout_id    INTEGER NOT NULL,
            duration_min  REAL NOT NULL,
            distance_km   REAL,
            avg_pace_sec  INTEGER,
            avg_hr        INTEGER,
            pain_level    INTEGER DEFAULT 0 CHECK(pain_level BETWEEN 0 AND 3),
            prehab_done   INTEGER DEFAULT 0,
            prehab_notes  TEXT,
            notes         TEXT,
            logged_at     DATETIME DEFAULT (datetime('now')),
            FOREIGN KEY(workout_id) REFERENCES workouts(workout_id)
        );

        CREATE TABLE IF NOT EXISTS friends (
            friend_id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name               TEXT NOT NULL,
            membership_user_fk INTEGER NOT NULL UNIQUE,
            added_at           DATETIME DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS plan_target_history (
            history_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id        INTEGER NOT NULL,
            target         INTEGER,
            effective_from DATE NOT NULL,
            FOREIGN KEY(plan_id) REFERENCES plans(plan_id)
        );

        CREATE TABLE IF NOT EXISTS session_types (
            name       TEXT PRIMARY KEY,
            stat_shape TEXT NOT NULL CHECK(stat_shape IN ('strength', 'cardio', 'none'))
        );
    """)
    conn.commit()

    # session_types is a soft vocabulary, seeded here and only here: it drives
    # the stat-enrichers' automatic routing (which child table fits a row), it
    # does not gate workout creation — an unlisted session_type is still a
    # valid workout, it just gets no automatic stat routing. Adding a type is
    # a one-line edit to this seed.
    conn.executemany(
        "INSERT OR IGNORE INTO session_types (name, stat_shape) VALUES (?, ?)",
        [
            ("crossfit", "strength"),
            ("running", "cardio"),
            ("walking", "cardio"),
            ("hiking", "cardio"),
            ("mobility", "none"),
        ],
    )
    conn.commit()

    # Migrate existing databases: add new columns if missing. The rename
    # generalizes the Arbox idempotency key: `external_id` is unique per
    # `source` (index below), so a future non-Arbox sync (e.g. watch sessions)
    # is just another source value, not another column.
    for sql in [
        "ALTER TABLE workouts ADD COLUMN session_type TEXT DEFAULT 'crossfit'",
        "ALTER TABLE workouts ADD COLUMN wod_result TEXT",
        "ALTER TABLE workouts ADD COLUMN notes TEXT",
        "ALTER TABLE plans ADD COLUMN start_date DATE",
        "ALTER TABLE plans ADD COLUMN binding TEXT",
        "ALTER TABLE workouts RENAME COLUMN arbox_class_id TO external_id",
    ]:
        try:
            conn.execute(sql)
            conn.commit()
        except Exception:
            pass  # column already exists (or, for the rename, already renamed)

    # The declared uniqueness contract is per-source. Migrated databases also
    # keep the stricter single-column UNIQUE the renamed column carried from
    # its original definition — harmless, since distinct sources' ids don't
    # collide in practice — so fresh databases declare the same column-level
    # UNIQUE above to keep the two schema histories equivalent.
    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_workouts_source_external "
            "ON workouts(source, external_id) WHERE external_id IS NOT NULL"
        )
        conn.commit()
    except Exception:
        pass

    # One-time, idempotent backfill: a plan with no start_date but with
    # existing workouts gets dated from its earliest workout, so adherence
    # reports don't count pre-existence weeks as misses. The IS NULL guard
    # makes this a no-op on every subsequent boot and never clobbers a
    # value set via manage_fitness_plan.
    try:
        conn.execute(
            "UPDATE plans SET start_date = ("
            "  SELECT MIN(date(scheduled_time)) FROM workouts w"
            "  WHERE w.plan_id = plans.plan_id"
            ") "
            "WHERE start_date IS NULL "
            "  AND EXISTS (SELECT 1 FROM workouts w WHERE w.plan_id = plans.plan_id)"
        )
        conn.commit()
    except Exception:
        pass

    # One-time, idempotent backfill: stamp each plan's logging channel by
    # applying the retiring attachment heuristics a final time — the plan
    # named like Running gets binding='running' (any status: a paused running
    # plan should still receive runs when reactivated), and the lowest-id
    # *active* remaining plan gets binding='arbox' (exactly what the old
    # "first active plan" LIMIT-1 pick resolved to). Other plans stay NULL —
    # no channel — until set via manage_fitness_plan. The IS NULL guards make
    # this a no-op on every subsequent boot.
    try:
        conn.execute(
            "UPDATE plans SET binding='running' WHERE binding IS NULL AND name LIKE '%Running%'"
        )
        conn.execute(
            "UPDATE plans SET binding='arbox' WHERE binding IS NULL AND status='active' "
            "AND plan_id = (SELECT MIN(plan_id) FROM plans WHERE binding IS NULL AND status='active')"
        )
        conn.commit()
    except Exception:
        pass

    # One-time, idempotent backfill: any plan with no target-history rows yet
    # (pre-dating this table, or created before manage_fitness_plan started
    # writing one) gets a single opening row at its current target and
    # start_date, so retroactive per-week lookups have something to find for
    # its entire life. The NOT IN guard makes this a no-op once a plan has any
    # row, whether from this backfill or from manage_fitness_plan.
    try:
        conn.execute(
            "INSERT INTO plan_target_history (plan_id, target, effective_from) "
            "SELECT plan_id, weekly_target_count, COALESCE(start_date, '1970-01-01') FROM plans "
            "WHERE plan_id NOT IN (SELECT DISTINCT plan_id FROM plan_target_history)"
        )
        conn.commit()
    except Exception:
        pass

    conn.close()


def _fmt_pace(avg_pace_sec: int) -> str:
    """Format seconds/km as mm'ss\"/km."""
    return f"{avg_pace_sec // 60}'{avg_pace_sec % 60:02d}\"/km"


def _target_for_week(conn: sqlite3.Connection, plan_id: int, week_start: str, fallback: int | None) -> int | None:
    """The weekly target in force as of `week_start` (YYYY-MM-DD), per `plan_target_history`.

    A change made mid-week takes effect the following week, not retroactively
    for the week it happened in — `week_start` is always a week's first day,
    so this is "the target that was already in force when the week began."
    Falls back to `fallback` (normally the plan's current weekly_target_count)
    only if no history row is old enough — defensive; shouldn't happen once
    `_init_db`'s backfill has run.
    """
    row = conn.execute(
        "SELECT target FROM plan_target_history WHERE plan_id=? AND effective_from<=? "
        "ORDER BY effective_from DESC LIMIT 1",
        (plan_id, week_start),
    ).fetchone()
    return row["target"] if row is not None else fallback


def _valid_date(s: str) -> bool:
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except (ValueError, TypeError):
        return False


_init_db()
