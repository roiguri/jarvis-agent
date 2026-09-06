"""Shared weekly-adherence math: week counts and the streak, in exactly one place.

Private to the skill, imported by both the chat report (tools/fitness/reports.py)
and the app dashboard (gateway/apps/fitness.py) so the two surfaces cannot drift.

Semantics both surfaces now share:

* A week is "met" when its completed-workout count reaches the target that was
  in force when the week began (`plan_target_history` via `_target_for_week`) —
  raising or lowering the goal later never rewrites an already-elapsed week.
* The streak counts consecutive met weeks walking back from *last* week, then
  adds the in-progress current week only if it has already met its target — an
  unmet current week is pending, not a miss.
* The walk ends at the first miss, at the plan's `start_date`, or at a week
  with no positive target. It is otherwise unbounded: a report or display
  window is not the streak's range.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from timeutils import israel_week_bounds
from tools.fitness._db import _target_for_week

# A defensive bound on the streak walk-back, never expected to bind — a streak
# is only as long as it has genuinely been held, but an unbounded loop still
# needs a backstop against a malformed plan row (e.g. a NULL start_date that
# never resolves to a miss).
_STREAK_SAFETY_CAP = 1500  # ~29 years


def completed_count(conn: sqlite3.Connection, plan_id: int, week_start: str, week_end: str) -> int:
    """Completed workouts for one plan within an inclusive YYYY-MM-DD week."""
    return conn.execute(
        "SELECT COUNT(*) FROM workouts WHERE plan_id=? AND status='completed' "
        "AND date(scheduled_time) BETWEEN ? AND ?",
        (plan_id, week_start, week_end),
    ).fetchone()[0]


def completed_count_all(conn: sqlite3.Connection, week_start: str, week_end: str) -> int:
    """Same as `completed_count` but across every workout, no `plan_id` filter.

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


def streak_weeks(
    conn: sqlite3.Connection,
    plan_id: int,
    current_target: int | None,
    start_date: str | None,
    now: datetime,
) -> int | None:
    """Consecutive weeks that met the target in force during that week (see module docstring).

    A plan with no current target reports `None` rather than a fabricated number.
    """
    if not current_target:
        return None
    streak = 0
    for i in range(1, _STREAK_SAFETY_CAP):
        week_start, week_end = israel_week_bounds(now - timedelta(weeks=i))
        if start_date and week_end < start_date:
            break
        week_target = _target_for_week(conn, plan_id, week_start, current_target)
        if not week_target:
            break
        if completed_count(conn, plan_id, week_start, week_end) >= week_target:
            streak += 1
        else:
            break

    this_week_start, this_week_end = israel_week_bounds(now)
    this_week_target = _target_for_week(conn, plan_id, this_week_start, current_target)
    if this_week_target and completed_count(conn, plan_id, this_week_start, this_week_end) >= this_week_target:
        streak += 1
    return streak
