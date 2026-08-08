"""Logging what was actually done — lifts, runs, and WOD results."""

from datetime import datetime, timedelta

from langchain_core.tools import tool

from tools.fitness._db import ISRAEL_TZ, _fmt_pace, _get_db
from tools.registry import tool_register


@tool_register(namespace="fitness")
@tool
def log_exercise_stats(
    exercise_name: str,
    sets: int,
    reps: int,
    weight: float,
    notes: str = "",
    workout_id: int | None = None,
) -> str:
    """Log a weightlifting performance to the fitness database.

    Use this immediately when the user reports what weights they lifted after a gym session.
    Always call this tool — never just acknowledge without saving.

    Args:
        exercise_name: Name of the exercise (e.g. 'Back Squat', 'Deadlift', 'Hang Power Clean')
        sets: Number of sets performed
        reps: Number of reps per set
        weight: Weight used in kg
        notes: Optional qualitative notes (e.g. 'felt strong', 'touch and go')
        workout_id: Optional. Links this log to today's session. If omitted, auto-looks up today's workout.
    """
    try:
        conn = _get_db()

        if workout_id is None:
            today = datetime.now(ISRAEL_TZ).strftime("%Y-%m-%d")
            row = conn.execute(
                "SELECT workout_id FROM workouts WHERE date(scheduled_time) = ? ORDER BY scheduled_time DESC LIMIT 1",
                (today,),
            ).fetchone()
            if row:
                workout_id = row["workout_id"]

        conn.execute(
            "INSERT INTO exercise_logs (workout_id, exercise_name, weight, sets, reps, notes) VALUES (?, ?, ?, ?, ?, ?)",
            (workout_id, exercise_name.strip(), weight, sets, reps, notes.strip() or None),
        )
        conn.commit()
        conn.close()

        notes_str = f" ({notes})" if notes else ""
        return f"Logged: {sets}×{reps} {exercise_name} at {weight}kg{notes_str}."

    except Exception as e:
        return f"Error logging exercise: {e}"



@tool_register(namespace="fitness")
@tool
def query_exercise_history(exercise_name: str) -> str:
    """Look up the personal best AND most recent session for a given exercise.

    Use before a class to check what weights Roi has hit before.
    Does a case-insensitive fuzzy match on the exercise name.

    Args:
        exercise_name: Exercise to look up (e.g. 'deadlift', 'Back Squat', 'clean')
    """
    try:
        conn = _get_db()
        pattern = f"%{exercise_name.strip()}%"
        pb_row = conn.execute(
            "SELECT weight, sets, reps, notes, logged_at FROM exercise_logs "
            "WHERE LOWER(exercise_name) LIKE LOWER(?) ORDER BY weight DESC LIMIT 1",
            (pattern,),
        ).fetchone()
        last_row = conn.execute(
            "SELECT weight, sets, reps, notes, logged_at FROM exercise_logs "
            "WHERE LOWER(exercise_name) LIKE LOWER(?) ORDER BY logged_at DESC LIMIT 1",
            (pattern,),
        ).fetchone()
        conn.close()

        if not pb_row:
            return f"No data for '{exercise_name}' yet. Log a session first."

        def _fmt(row):
            date_str = row["logged_at"][:10] if row["logged_at"] else "unknown"
            notes_str = f" ({row['notes']})" if row["notes"] else ""
            return f"{row['weight']}kg — {row['sets']}×{row['reps']} on {date_str}{notes_str}"

        if pb_row["logged_at"] == last_row["logged_at"]:
            return f"{exercise_name}:\n  PB / Last: {_fmt(pb_row)}"
        return f"{exercise_name}:\n  PB:   {_fmt(pb_row)}\n  Last: {_fmt(last_row)}"

    except Exception as e:
        return f"Error querying history: {e}"



@tool_register(namespace="fitness")
@tool
def log_running_session(
    duration_min: float,
    description: str,
    distance_km: float | None = None,
    avg_hr: int | None = None,
    pain_level: int = 0,
    prehab_done: bool = False,
    prehab_notes: str = "",
    notes: str = "",
    date: str | None = None,
) -> str:
    """Log a running or walking session to the fitness database.

    Call this immediately when Roi reports completing a running/walking session.
    Always call this tool — never just acknowledge without saving.
    Check your running program memory notes to determine the correct description before calling.

    Args:
        duration_min: Total session duration in minutes (e.g. 32.0)
        description: Session description from the running program
            (e.g. 'Phase 0 Session 1: 30-min brisk walk')
        distance_km: Distance covered in km (from watch GPS)
        avg_hr: Average heart rate in bpm
        pain_level: 0=none, 1=slight, 2=moderate, 3=stop-sign
        prehab_done: Whether pre-hab exercises were completed after the session
        prehab_notes: Description of pre-hab done (e.g. 'Tibialis 3×15, Calf raises 3×15')
        notes: Any additional session notes
        date: Session date as YYYY-MM-DD (defaults to today in Israel time)
    """
    try:
        conn = _get_db()
        session_date = date or datetime.now(ISRAEL_TZ).strftime("%Y-%m-%d")
        scheduled_time = f"{session_date} 00:00:00"

        plan = conn.execute(
            "SELECT plan_id FROM plans WHERE name LIKE '%Running%' AND status='active' LIMIT 1"
        ).fetchone()
        plan_id = plan["plan_id"] if plan else None

        conn.execute(
            "INSERT INTO workouts (plan_id, session_type, scheduled_time, status, description, source) "
            "VALUES (?, 'running', ?, 'completed', ?, 'manual')",
            (plan_id, scheduled_time, description.strip()),
        )
        conn.commit()
        workout_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        avg_pace_sec = None
        if distance_km and duration_min:
            avg_pace_sec = int((duration_min * 60) / distance_km)

        conn.execute(
            "INSERT INTO cardio_logs (workout_id, duration_min, distance_km, avg_pace_sec, avg_hr, "
            "pain_level, prehab_done, prehab_notes, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (workout_id, duration_min, distance_km, avg_pace_sec, avg_hr,
             pain_level, int(prehab_done), prehab_notes.strip() or None, notes.strip() or None),
        )
        conn.commit()
        conn.close()

        parts = [f"Logged running session: {description}"]
        parts.append(f"  Duration: {duration_min} min")
        if distance_km:
            parts.append(f"  Distance: {distance_km} km")
        if avg_pace_sec:
            parts.append(f"  Avg pace: {_fmt_pace(avg_pace_sec)}")
        if avg_hr:
            parts.append(f"  Avg HR: {avg_hr} bpm")
        if pain_level > 0:
            labels = {1: "slight", 2: "moderate", 3: "STOP"}
            parts.append(f"  Pain: {labels.get(pain_level, pain_level)}")
        if prehab_done:
            parts.append(f"  Pre-hab: done ({prehab_notes})" if prehab_notes else "  Pre-hab: done")
        return "\n".join(parts)

    except Exception as e:
        return f"Error logging running session: {e}"



@tool_register(namespace="fitness")
@tool
def log_wod_result(
    result: str,
    workout_id: int | None = None,
    notes: str | None = None,
) -> str:
    """Log the CrossFit WOD result for today's session.

    Call this after the user reports their WOD performance (time, rounds, score).
    Use after log_exercise_stats — this completes the CrossFit session record.

    Args:
        result: WOD performance text (e.g. '12:43 RX', '9 rounds + 5 reps scaled', 'AMRAP: 8 rounds Rx')
        workout_id: Optional. Links to today's CrossFit session. If omitted, auto-looks up today's workout.
        notes: Optional free-text session note ('scaled to 16kg', 'shoulder felt off').
            Provided -> set/overwrite; omitted -> any existing note is preserved.
    """
    try:
        conn = _get_db()
        if workout_id is None:
            today = datetime.now(ISRAEL_TZ).strftime("%Y-%m-%d")
            row = conn.execute(
                "SELECT workout_id FROM workouts WHERE date(scheduled_time) = ? "
                "AND session_type = 'crossfit' ORDER BY scheduled_time DESC LIMIT 1",
                (today,),
            ).fetchone()
            if not row:
                conn.close()
                return "No CrossFit session found for today. Check workout_id manually."
            workout_id = row["workout_id"]

        conn.execute(
            "UPDATE workouts SET wod_result = ?, notes = COALESCE(?, notes), status = 'completed' "
            "WHERE workout_id = ?",
            (result.strip(), notes.strip() if notes else None, workout_id),
        )
        conn.commit()
        conn.close()
        note_tag = f" (note: {notes.strip()})" if notes and notes.strip() else ""
        return f"WOD result logged for session {workout_id}: {result}{note_tag}"

    except Exception as e:
        return f"Error logging WOD result: {e}"



@tool_register(namespace="fitness")
@tool
def get_today_workout_id() -> str:
    """Look up today's workout_id for use in log_exercise_stats.

    Use this when logging exercise stats if the user hasn't specified a workout context,
    to ensure the log is linked to the correct session.

    Returns the workout_id, class time, and WOD description for today's session.
    """
    try:
        conn = _get_db()
        today = datetime.now(ISRAEL_TZ).strftime("%Y-%m-%d")
        row = conn.execute(
            "SELECT workout_id, scheduled_time, status, description FROM workouts "
            "WHERE date(scheduled_time) = ? ORDER BY scheduled_time DESC LIMIT 1",
            (today,),
        ).fetchone()
        conn.close()

        if not row:
            return "No workout found for today. Run fetch_upcoming_arbox_classes first."

        desc = (row["description"] or "No WOD description")[:100]
        return (
            f"Today's workout_id: {row['workout_id']} | "
            f"scheduled: {row['scheduled_time'][11:16]} | "
            f"status: {row['status']} | WOD: {desc}"
        )

    except Exception as e:
        return f"Error: {e}"
