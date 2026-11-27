"""Tests for deterministic Arbox attendance sync (#214)."""

from datetime import datetime, timezone

import pytest

from tools.fitness import _db, classes, gates
from tools.fitness._db import ISRAEL_TZ, _get_db


@pytest.fixture(autouse=True)
def isolated_fitness_db(tmp_path, monkeypatch):
    monkeypatch.setattr(_db, "DB_PATH", str(tmp_path / "fitness.sqlite"))
    _db._init_db()


def _insert_workout(scheduled_time: str, status: str = "scheduled", source: str = "arbox") -> int:
    conn = _get_db()
    try:
        cur = conn.execute(
            "INSERT INTO workouts (scheduled_time, session_type, description, status, source) "
            "VALUES (?, 'crossfit', 'WOD', ?, ?)",
            (scheduled_time, status, source),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _status_of(workout_id: int) -> str:
    conn = _get_db()
    try:
        return conn.execute(
            "SELECT status FROM workouts WHERE workout_id = ?", (workout_id,)
        ).fetchone()["status"]
    finally:
        conn.close()


def test_sync_attendance_skips_api_when_no_stale_classes(monkeypatch):
    called = []
    monkeypatch.setattr(classes, "_arbox_post", lambda *a, **kw: called.append(a))

    now = datetime(2026, 11, 25, 20, 0, tzinfo=ISRAEL_TZ).astimezone(timezone.utc)
    _insert_workout("2026-11-25 19:00:00")
    assert classes._sync_attendance(now=now) == (0, 0)
    assert called == []


def test_sync_attendance_completed_missed_manual_and_idempotent(monkeypatch):
    now = datetime(2026, 11, 25, 22, 0, tzinfo=ISRAEL_TZ).astimezone(timezone.utc)
    attended_id = _insert_workout("2026-11-25 19:00:00")
    old_unattended_id = _insert_workout("2026-11-24 19:00:00")
    manual_id = _insert_workout("2026-11-24 08:00:00", source="manual")

    calls = []
    monkeypatch.setattr(classes, "_arbox_post", lambda *a, **kw: calls.append(a) or ["2026-11-25"])

    assert classes._sync_attendance(now=now) == (1, 1)
    assert _status_of(attended_id) == "completed"
    assert _status_of(old_unattended_id) == "missed"
    assert _status_of(manual_id) == "scheduled"
    assert len(calls) == 1

    # Second run is a DB-only no-op (zero Arbox API calls).
    assert classes._sync_attendance(now=now) == (0, 0)
    assert len(calls) == 1


def test_sync_attendance_waits_12h_grace_before_marking_missed(monkeypatch):
    now = datetime(2026, 11, 25, 22, 0, tzinfo=ISRAEL_TZ).astimezone(timezone.utc)
    recent_id = _insert_workout("2026-11-25 19:00:00")   # 3h ago
    old_id = _insert_workout("2026-11-25 08:00:00")      # 14h ago

    monkeypatch.setattr(classes, "_arbox_post", lambda *a, **kw: [])
    assert classes._sync_attendance(now=now) == (0, 1)
    assert _status_of(recent_id) == "scheduled", "within 12h grace period"
    assert _status_of(old_id) == "missed", "past 12h grace period"


def test_sync_attendance_skips_suspicious_empty_list_when_recent_completed_exists(monkeypatch):
    now = datetime(2026, 11, 25, 22, 0, tzinfo=ISRAEL_TZ).astimezone(timezone.utc)
    _insert_workout("2026-11-20 19:00:00", status="completed")
    old_id = _insert_workout("2026-11-24 19:00:00", status="scheduled")

    monkeypatch.setattr(classes, "_arbox_post", lambda *a, **kw: [])
    assert classes._sync_attendance(now=now) == (0, 0)
    assert _status_of(old_id) == "scheduled", "empty list glitch must not flip to missed"


def test_sync_attendance_does_not_overwrite_concurrent_completion(monkeypatch):
    now = datetime(2026, 11, 25, 22, 0, tzinfo=ISRAEL_TZ).astimezone(timezone.utc)
    old_id = _insert_workout("2026-11-24 19:00:00", status="scheduled")

    def arbox_during_user_log(*a, **kw):
        # Simulate user logging their WOD while the Arbox HTTP request is in flight.
        conn = _get_db()
        try:
            conn.execute("UPDATE workouts SET status = 'completed' WHERE workout_id = ?", (old_id,))
            conn.commit()
        finally:
            conn.close()
        return []

    monkeypatch.setattr(classes, "_arbox_post", arbox_during_user_log)
    assert classes._sync_attendance(now=now) == (0, 0)
    assert _status_of(old_id) == "completed", "must not overwrite completed back to missed"


def test_sync_attendance_ignores_non_list_response(monkeypatch):
    now = datetime(2026, 11, 25, 22, 0, tzinfo=ISRAEL_TZ).astimezone(timezone.utc)
    old_id = _insert_workout("2026-11-24 19:00:00")
    monkeypatch.setattr(classes, "_arbox_post", lambda *a, **kw: {"error": "transient"})
    assert classes._sync_attendance(now=now) == (0, 0)
    assert _status_of(old_id) == "scheduled"


def test_gate_runs_attendance_sync_silently_without_firing(monkeypatch):
    ran = []
    monkeypatch.setattr(gates, "_sync_attendance", lambda: ran.append(True) or (1, 0))
    monkeypatch.setattr(gates, "_fetch_registered", lambda: [])

    res = gates.check({"classes": {}})
    assert ran == [True]
    assert res.fire is False


def test_gate_survives_attendance_sync_error(monkeypatch):
    def boom():
        raise RuntimeError("arbox down")

    monkeypatch.setattr(gates, "_sync_attendance", boom)
    monkeypatch.setattr(gates, "_fetch_registered", lambda: [])

    res = gates.check({"classes": {}})
    assert res.fire is False
