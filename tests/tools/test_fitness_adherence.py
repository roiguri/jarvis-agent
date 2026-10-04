"""Weekly adherence: what counts toward a week, and the streak — judged against
the target in force when each week began, with the in-progress week pending
rather than a miss."""

from datetime import date, datetime, timedelta

import pytest

from timeutils import ISRAEL_TZ
from tools.fitness import _adherence, _db

# Wednesday; its Sunday-anchored week is 2026-03-08..14.
NOW = datetime(2026, 3, 11, 12, 0, tzinfo=ISRAEL_TZ)
THIS_SUNDAY = date(2026, 3, 8)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(_db, "DB_PATH", str(tmp_path / "fitness.sqlite"))
    _db._init_db()
    c = _db._get_db()
    yield c
    c.close()


def add_plan(conn, target=3, start="2025-01-05", history=None):
    """A plan; ``history`` is [(effective_from, target)], default one row from the start."""
    plan_id = conn.execute(
        "INSERT INTO plans (name, tracking_mode, weekly_target_count, start_date) VALUES (?,?,?,?)",
        ("CrossFit", "flexible_quota", target, start)).lastrowid
    for eff, t in history or [(start, target)]:
        conn.execute("INSERT INTO plan_target_history (plan_id, target, effective_from) VALUES (?,?,?)",
                     (plan_id, t, eff))
    conn.commit()
    return plan_id


def add_workouts(conn, plan_id, weeks_ago, n, status="completed"):
    """``n`` workouts on the Monday of the week ``weeks_ago`` weeks back (0 = this week)."""
    monday = THIS_SUNDAY - timedelta(weeks=weeks_ago) + timedelta(days=1)
    for i in range(n):
        conn.execute("INSERT INTO workouts (plan_id, scheduled_time, status) VALUES (?,?,?)",
                     (plan_id, f"{monday} {7 + i:02d}:00", status))
    conn.commit()


def streak(conn, plan_id, target=3, start="2025-01-05"):
    return _adherence.streak_weeks(conn, plan_id, target, start, NOW)


def test_unmet_current_week_is_pending_not_a_miss(conn):
    plan = add_plan(conn)
    for w in (1, 2, 3):
        add_workouts(conn, plan, w, 3)
    add_workouts(conn, plan, 0, 1)
    assert streak(conn, plan) == 3


def test_met_current_week_adds_to_the_streak(conn):
    plan = add_plan(conn)
    for w in (0, 1, 2, 3):
        add_workouts(conn, plan, w, 3)
    assert streak(conn, plan) == 4


def test_a_missed_week_ends_the_streak(conn):
    plan = add_plan(conn)
    add_workouts(conn, plan, 1, 3)
    add_workouts(conn, plan, 2, 2)
    add_workouts(conn, plan, 3, 3)
    assert streak(conn, plan) == 1


def test_each_week_judged_by_the_target_in_force_then(conn):
    # Target 2 until this week, raised to 4 from this Sunday.
    plan = add_plan(conn, target=4, history=[("2025-01-05", 2), (str(THIS_SUNDAY), 4)])
    for w in (1, 2):
        add_workouts(conn, plan, w, 2)
    add_workouts(conn, plan, 0, 3)
    assert streak(conn, plan, target=4) == 2, "raising the goal never rewrites elapsed weeks"


def test_streak_stops_at_the_plan_start(conn):
    start = str(THIS_SUNDAY - timedelta(weeks=2))
    plan = add_plan(conn, start=start)
    for w in (1, 2, 3, 4):
        add_workouts(conn, plan, w, 3)
    assert streak(conn, plan, start=start) == 2


def test_no_target_reports_none(conn):
    plan = add_plan(conn, target=None)
    assert _adherence.streak_weeks(conn, plan, None, "2025-01-05", NOW) is None


def test_what_counts_toward_a_week(conn):
    plan = add_plan(conn)
    other = add_plan(conn)
    add_workouts(conn, plan, 0, 2)
    add_workouts(conn, plan, 0, 1, status="scheduled")
    add_workouts(conn, plan, 0, 1, status="missed")
    add_workouts(conn, other, 0, 1)
    conn.execute("INSERT INTO workouts (plan_id, scheduled_time, status) VALUES (NULL, ?, 'completed')",
                 (f"{THIS_SUNDAY} 18:00",))
    conn.commit()
    week = ("2026-03-08", "2026-03-14")
    assert _adherence.completed_count(conn, plan, *week) == 2, "only this plan's completed sessions"
    assert _adherence.completed_count_all(conn, *week) == 4, "any plan, or none"


def test_week_edges_are_inclusive(conn):
    plan = add_plan(conn)
    for day in ("2026-03-07 23:30", "2026-03-08 00:10", "2026-03-14 23:50", "2026-03-15 00:05"):
        conn.execute("INSERT INTO workouts (plan_id, scheduled_time, status) VALUES (?, ?, 'completed')",
                     (plan, day))
    conn.commit()
    assert _adherence.completed_count(conn, plan, "2026-03-08", "2026-03-14") == 2
