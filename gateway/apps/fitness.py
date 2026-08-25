"""The fitness app — a read-only dashboard, history browser, and exercise search.

Reads `fitness.sqlite` directly rather than calling the fitness *tools*: their output
is English written for a model, and parsing it here would ship a client that breaks
when a docstring is reworded. Same reasoning as `travel.py` reading `travel.sqlite`
and `memory.py` walking `MEMORY_DIR` instead of calling their respective tools.

`_FITNESS_RO_URI` and `_fmt_pace` are imported from `tools.fitness._db` rather than
reimplemented — private-to-public across a package boundary, on purpose: a second
read-only URI or a second pace formatter is one that can drift from the original.
Importing that module also runs its `_init_db()`, so the schema exists even on an
instance that has never used the fitness skill in chat.

THE SECURITY LINE differs from memory's. Nothing here resolves a caller-supplied
path; the only caller-supplied values are a date cursor, a workout id, and an
exercise name, and each reaches SQLite exclusively as a bound parameter. The
connection is opened read-only at the URI level besides, so a device cannot write
to this database even if a handler were wrong about what it was doing.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from gateway.apps.registry import (
    AppEntry,
    AppInvalidRequest,
    AppNotFound,
    AppSpec,
    register_app,
)
from timeutils import israel_week_bounds
from tools.fitness._db import _FITNESS_RO_URI, _fmt_pace, _target_for_week, ISRAEL_TZ

# Marks cover a full year, one block per week (a plan's cadence is weekly, not
# daily — unlike GitHub's per-day contribution graph). The streak below is
# deliberately NOT bounded by this: it is a display window, not the streak's
# actual range.
_MARKS_WEEKS = 52

# A defensive bound on the streak walk-back, never expected to bind — a streak is
# only as long as it has genuinely been held, but an unbounded loop still needs a
# backstop against a malformed plan row (e.g. a NULL start_date that never
# resolves to a miss).
_STREAK_SAFETY_CAP = 1500  # ~29 years

_HISTORY_PAGE_SIZE = 20


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_FITNESS_RO_URI, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _weekly_completed_count(conn: sqlite3.Connection, plan_id: int, week_start: str, week_end: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM workouts WHERE plan_id=? AND status='completed' "
        "AND date(scheduled_time) BETWEEN ? AND ?",
        (plan_id, week_start, week_end),
    ).fetchone()[0]


def _weekly_completed_count_all(conn: sqlite3.Connection, week_start: str, week_end: str) -> int:
    """Same as `_weekly_completed_count` but across every workout, no `plan_id` filter.

    A workout counts here regardless of whether its plan is active, paused,
    completed, or it was never attached to a plan at all (`plan_id IS NULL`) —
    the per-plan count structurally can't see any of those, since it always
    filters on one specific `plan_id`.
    """
    return conn.execute(
        "SELECT COUNT(*) FROM workouts WHERE status='completed' "
        "AND date(scheduled_time) BETWEEN ? AND ?",
        (week_start, week_end),
    ).fetchone()[0]


def _streak_weeks(conn: sqlite3.Connection, plan: sqlite3.Row, now: datetime) -> int | None:
    """Consecutive weeks that met the target *in force during that week*, counting back from last week.

    Each week is judged against its own historical target
    (`plan_target_history`, via `_target_for_week`) rather than the plan's
    current one — raising or lowering the goal later never rewrites whether an
    already-elapsed week counted. The in-progress current week is judged
    separately and added on top only if it has already met its target — an
    unmet current week is pending, not a miss, so it doesn't zero out a streak
    just because the week isn't over yet. Walks backward from last week until
    the first miss or the plan's `start_date`, whichever comes first — no upper
    bound beyond the safety cap. A plan with no current target reports `None`
    rather than a fabricated number.
    """
    target = plan["weekly_target_count"]
    if not target:
        return None
    start_date = plan["start_date"]
    streak = 0
    for i in range(1, _STREAK_SAFETY_CAP):
        week_start, week_end = israel_week_bounds(now - timedelta(weeks=i))
        if start_date and week_end < start_date:
            break
        week_target = _target_for_week(conn, plan["plan_id"], week_start, target)
        if not week_target:
            break
        if _weekly_completed_count(conn, plan["plan_id"], week_start, week_end) >= week_target:
            streak += 1
        else:
            break

    this_week_start, this_week_end = israel_week_bounds(now)
    this_week_target = _target_for_week(conn, plan["plan_id"], this_week_start, target)
    if this_week_target and _weekly_completed_count(conn, plan["plan_id"], this_week_start, this_week_end) >= this_week_target:
        streak += 1
    return streak


def _truncate(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _crossfit_summary(wod_result: str | None, notes: str | None, description: str | None) -> str:
    """`wod_result` if logged, else `notes`, else the assigned WOD text itself.

    Real data has all three states: most sessions have a `wod_result`; some have
    only `notes`; a few (WOD posted, no personal result recorded) have neither,
    and only `description` — the text Arbox posted — is there to show.
    """
    for candidate in (wod_result, notes, description):
        if candidate and candidate.strip():
            return _truncate(candidate, 70)
    return "No result logged"


def _running_summary(cardio: sqlite3.Row) -> str:
    """Duration always present; distance/pace/HR each independently optional."""
    parts = [f"{cardio['duration_min']:.0f} min"]
    if cardio["distance_km"]:
        parts.append(f"{cardio['distance_km']} km")
    if cardio["avg_pace_sec"]:
        parts.append(_fmt_pace(cardio["avg_pace_sec"]))
    if cardio["avg_hr"]:
        parts.append(f"HR {cardio['avg_hr']}")
    return " · ".join(parts)


def _dashboard_sync() -> dict[str, Any]:
    now = datetime.now(ISRAEL_TZ)
    conn = _connect()
    try:
        plans = conn.execute("SELECT * FROM plans WHERE status='active' ORDER BY plan_id").fetchall()

        # Plan-agnostic: every completed workout counts here, whether it
        # belongs to an active plan, a stopped one, or no plan at all. This is
        # the total/chart for "how much did I actually train," which a
        # per-plan count can't answer by construction.
        overall_marks = []
        overall_total_year = 0
        for i in range(_MARKS_WEEKS - 1, -1, -1):
            week_start, week_end = israel_week_bounds(now - timedelta(weeks=i))
            count = _weekly_completed_count_all(conn, week_start, week_end)
            overall_marks.append({"week_start": week_start, "count": count})
            overall_total_year += count

        plan_rows = []
        for p in plans:
            marks = []
            total_year = 0
            # Oldest first: i counts down from 51 weeks ago to this week (i=0).
            for i in range(_MARKS_WEEKS - 1, -1, -1):
                week_start, week_end = israel_week_bounds(now - timedelta(weeks=i))
                count = _weekly_completed_count(conn, p["plan_id"], week_start, week_end)
                marks.append({"week_start": week_start, "count": count})
                total_year += count

            this_week_start, this_week_end = israel_week_bounds(now)
            plan_rows.append(
                {
                    "plan_id": p["plan_id"],
                    "name": p["name"],
                    "tracking_mode": p["tracking_mode"],
                    "weekly_target_count": p["weekly_target_count"],
                    "this_week_done": _weekly_completed_count(conn, p["plan_id"], this_week_start, this_week_end),
                    "streak_weeks": _streak_weeks(conn, p, now),
                    "total_year": total_year,
                    "marks": marks,
                }
            )

        now_il = datetime.now(ISRAEL_TZ).strftime("%Y-%m-%d %H:%M:%S")
        upcoming_rows = conn.execute(
            "SELECT workout_id, scheduled_time, session_type, description, source FROM workouts "
            "WHERE status='scheduled' AND scheduled_time >= ? ORDER BY scheduled_time ASC",
            (now_il,),
        ).fetchall()
    finally:
        conn.close()

    upcoming = [
        {
            "workout_id": r["workout_id"],
            "scheduled_time": r["scheduled_time"],
            "session_type": r["session_type"] or "crossfit",
            "description": r["description"],
            "source": r["source"],
        }
        for r in upcoming_rows
    ]
    return {
        "plans": plan_rows,
        "upcoming": upcoming,
        "total_year": overall_total_year,
        "marks": overall_marks,
    }


def _history_sync(before: str) -> dict[str, Any]:
    if before:
        try:
            datetime.strptime(before, "%Y-%m-%d %H:%M:%S")
        except ValueError as exc:
            raise AppInvalidRequest(f"'before' must be 'YYYY-MM-DD HH:MM:SS', got {before!r}") from exc

    conn = _connect()
    try:
        where = "WHERE w.status IN ('completed', 'missed')"
        params: list[Any] = []
        if before:
            where += " AND w.scheduled_time < ?"
            params.append(before)
        # LIMIT +1, not COUNT(*): one extra row tells us a next page exists
        # without a second query, same idiom query_fitness_db uses for its cap.
        rows = conn.execute(
            "SELECT w.workout_id, w.plan_id, w.scheduled_time, w.session_type, w.status, "
            "w.wod_result, w.description, w.notes, "
            "c.duration_min, c.distance_km, c.avg_pace_sec, c.avg_hr "
            "FROM workouts w LEFT JOIN cardio_logs c ON c.workout_id = w.workout_id "
            f"{where} ORDER BY w.scheduled_time DESC LIMIT ?",
            (*params, _HISTORY_PAGE_SIZE + 1),
        ).fetchall()
    finally:
        conn.close()

    has_more = len(rows) > _HISTORY_PAGE_SIZE
    rows = rows[:_HISTORY_PAGE_SIZE]

    sessions = []
    for r in rows:
        session_type = r["session_type"] or "crossfit"
        summary = _running_summary(r) if session_type == "running" else _crossfit_summary(
            r["wod_result"], r["notes"], r["description"]
        )
        sessions.append(
            {
                "workout_id": r["workout_id"],
                "plan_id": r["plan_id"],
                "scheduled_time": r["scheduled_time"],
                "session_type": session_type,
                "status": r["status"],
                "summary": summary,
            }
        )
    return {
        "sessions": sessions,
        "next_before": rows[-1]["scheduled_time"] if has_more and rows else None,
    }


def _session_sync(workout_id_str: str) -> dict[str, Any]:
    if not workout_id_str.isdigit():
        raise AppInvalidRequest(f"workout_id must be a positive integer, got {workout_id_str!r}")
    workout_id = int(workout_id_str)

    conn = _connect()
    try:
        w = conn.execute(
            "SELECT workout_id, plan_id, scheduled_time, session_type, status, "
            "description, notes, wod_result FROM workouts WHERE workout_id = ?",
            (workout_id,),
        ).fetchone()
        if w is None:
            raise AppNotFound(f"No workout {workout_id}")

        exercise_logs = conn.execute(
            "SELECT exercise_name, weight, sets, reps, notes, logged_at FROM exercise_logs "
            "WHERE workout_id = ? ORDER BY logged_at",
            (workout_id,),
        ).fetchall()
        cardio = conn.execute(
            "SELECT duration_min, distance_km, avg_pace_sec, avg_hr, pain_level, "
            "prehab_done, prehab_notes, notes FROM cardio_logs WHERE workout_id = ? LIMIT 1",
            (workout_id,),
        ).fetchone()
    finally:
        conn.close()

    return {
        "workout_id": w["workout_id"],
        "plan_id": w["plan_id"],
        "scheduled_time": w["scheduled_time"],
        "session_type": w["session_type"] or "crossfit",
        "status": w["status"],
        "description": w["description"],
        "notes": w["notes"],
        "wod_result": w["wod_result"],
        "exercise_logs": [dict(e) for e in exercise_logs],
        "cardio": dict(cardio) if cardio is not None else None,
    }


def _exercises_sync() -> dict[str, Any]:
    conn = _connect()
    try:
        # DESC so the first row seen per LOWER()-key is the most recent one —
        # that's the casing and last_logged_at we want, with no second pass.
        rows = conn.execute(
            "SELECT exercise_name, logged_at FROM exercise_logs ORDER BY logged_at DESC"
        ).fetchall()
    finally:
        conn.close()

    seen: dict[str, dict[str, Any]] = {}
    for r in rows:
        key = r["exercise_name"].strip().lower()
        entry = seen.get(key)
        if entry is None:
            seen[key] = {"exercise_name": r["exercise_name"], "last_logged_at": r["logged_at"], "log_count": 1}
        else:
            entry["log_count"] += 1

    exercises = sorted(seen.values(), key=lambda e: e["last_logged_at"], reverse=True)
    return {"exercises": exercises}


def _exercise_log_sync(exercise_name: str) -> dict[str, Any]:
    if not exercise_name:
        raise AppInvalidRequest("exercise_name is required")

    conn = _connect()
    try:
        # Exact match, not the chat tool's substring LIKE: the client always got
        # this name from the `exercises` entry, so it already has the canonical
        # spelling, and exact keeps "Press" from also pulling in every other press.
        rows = conn.execute(
            "SELECT weight, sets, reps, notes, logged_at FROM exercise_logs "
            "WHERE LOWER(exercise_name) = LOWER(?) ORDER BY logged_at DESC",
            (exercise_name,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        raise AppNotFound(f"No logs for exercise {exercise_name!r}")
    return {"exercise_name": exercise_name, "entries": [dict(r) for r in rows]}


# Every call above blocks on SQLite, and one event loop serves the poll, any
# in-flight turn, and this drain — matching travel.py and memory.py.
async def _dashboard(params: dict[str, str]) -> Any:
    return await asyncio.to_thread(_dashboard_sync)


async def _history(params: dict[str, str]) -> Any:
    return await asyncio.to_thread(_history_sync, (params.get("before") or "").strip())


async def _session(params: dict[str, str]) -> Any:
    return await asyncio.to_thread(_session_sync, (params.get("workout_id") or "").strip())


async def _exercises(params: dict[str, str]) -> Any:
    return await asyncio.to_thread(_exercises_sync)


async def _exercise_log(params: dict[str, str]) -> Any:
    return await asyncio.to_thread(_exercise_log_sync, (params.get("exercise_name") or "").strip())


# Read-only in v1: every entry is GET, so a channel honouring `method` refuses a
# write outright. Logging stays chat-only — see FITNESS_APP_PLAN.md.
FITNESS_APP = register_app(
    AppSpec(
        ns="fitness",
        name="Fitness",
        entries=(
            AppEntry(id="dashboard", method="GET", params=(), handler=_dashboard),
            AppEntry(id="history", method="GET", params=("before",), handler=_history),
            AppEntry(id="session", method="GET", params=("workout_id",), handler=_session),
            AppEntry(id="exercises", method="GET", params=(), handler=_exercises),
            AppEntry(id="exercise_log", method="GET", params=("exercise_name",), handler=_exercise_log),
        ),
    )
)
