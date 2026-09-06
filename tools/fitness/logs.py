"""Logging what was actually done — sessions, lifts, cardio stats, and WOD results.

The write model is create-then-enrich: `log_workout` records that a session
happened (one row in `workouts`), and the per-shape enrichers attach stats to
it — `log_exercise_stats` (strength), `log_cardio_stats` (cardio),
`log_wod_result` (WOD score). The cardio enricher can also conjure its parent
row when none exists yet, so the common one-utterance run report stays one
call. Which shape auto-routes to which rows comes from the `session_types`
table, never from the type string alone.
"""

from datetime import datetime, timedelta

from langchain_core.tools import tool

from tools.fitness._db import ISRAEL_TZ, _fmt_pace, _get_db, _valid_date
from tools.registry import tool_register

# A session type's "natural home": which plan binding receives its manual
# workouts when the caller names no plan. Types not listed here belong to the
# generic manual channel.
_BINDING_HOME = {"running": "running", "crossfit": "arbox"}

# Manual rows carry a midday sentinel time: date-only input has no real clock,
# and midday keeps these rows distinguishable from (and stably ordered
# against) Arbox rows, which carry the class's actual start time.
_MANUAL_TIME = "12:00:00"


def _resolve_plan(conn, plan: str | None, session_type: str) -> tuple[int | None, str | None]:
    """Which plan does a manual workout belong to? Returns (plan_id, error).

    An explicit `plan` (id or name, any status) always wins — the owner's
    choice. Otherwise the session type's natural-home binding picks among
    *active* plans, and anything but exactly one match is an error naming the
    candidates — never a silent NULL, because an unattached row shows up in
    the year totals but in no plan's quota or streak.
    """
    if plan:
        p = plan.strip()
        if p.isdigit():
            row = conn.execute("SELECT plan_id FROM plans WHERE plan_id=?", (int(p),)).fetchone()
            if row:
                return row["plan_id"], None
        rows = conn.execute(
            "SELECT plan_id, name FROM plans WHERE LOWER(name) LIKE LOWER(?)", (f"%{p}%",)
        ).fetchall()
        if len(rows) == 1:
            return rows[0]["plan_id"], None
        if rows:
            names = ", ".join(f"[{r['plan_id']}] {r['name']}" for r in rows)
            return None, f"Plan {plan!r} is ambiguous: {names}. Pass the plan_id."
        return None, f"No plan matching {plan!r}. Check manage_fitness_plan(action='list')."

    home = _BINDING_HOME.get(session_type, "manual")
    rows = conn.execute(
        "SELECT plan_id FROM plans WHERE status='active' AND binding=?", (home,)
    ).fetchall()
    if len(rows) == 1:
        return rows[0]["plan_id"], None
    active = conn.execute("SELECT plan_id, name, binding FROM plans WHERE status='active'").fetchall()
    listing = "; ".join(f"[{r['plan_id']}] {r['name']} (binding: {r['binding'] or '—'})" for r in active) or "none"
    problem = "no active plan has" if not rows else f"{len(rows)} active plans have"
    return None, (
        f"Can't attach this {session_type} session: {problem} binding='{home}'. "
        f"Active plans: {listing}. Pass plan=<id or name>, or fix bindings via manage_fitness_plan."
    )


def _stat_shape(conn, session_type: str) -> str | None:
    row = conn.execute("SELECT stat_shape FROM session_types WHERE name=?", (session_type,)).fetchone()
    return row["stat_shape"] if row else None


def _create_workout(conn, description: str, session_date: str, session_type: str, plan_id: int | None) -> int:
    """Insert one completed manual workout row and return its id."""
    conn.execute(
        "INSERT INTO workouts (plan_id, session_type, scheduled_time, status, description, source) "
        "VALUES (?, ?, ?, 'completed', ?, 'manual')",
        (plan_id, session_type, f"{session_date} {_MANUAL_TIME}", description.strip()),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _insert_cardio(
    conn, workout_id: int, duration_min: float, distance_km, avg_hr,
    pain_level: int, prehab_done: bool, prehab_notes: str, notes: str,
) -> int | None:
    """Insert the cardio row (pace derived), mark the workout completed; returns avg_pace_sec."""
    avg_pace_sec = None
    if distance_km and duration_min:
        avg_pace_sec = int((duration_min * 60) / distance_km)
    conn.execute(
        "INSERT INTO cardio_logs (workout_id, duration_min, distance_km, avg_pace_sec, avg_hr, "
        "pain_level, prehab_done, prehab_notes, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (workout_id, duration_min, distance_km, avg_pace_sec, avg_hr,
         pain_level, int(prehab_done), prehab_notes.strip() or None, notes.strip() or None),
    )
    # Stats arriving means the session happened — a still-'scheduled' row
    # (e.g. a synced endurance class) completes on enrichment, mirroring
    # log_wod_result's flip.
    conn.execute("UPDATE workouts SET status='completed' WHERE workout_id=?", (workout_id,))
    conn.commit()
    return avg_pace_sec


def _cardio_parts(duration_min, distance_km, avg_pace_sec, avg_hr, pain_level, prehab_done, prehab_notes) -> list[str]:
    parts = [f"  Duration: {duration_min} min"]
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
    return parts


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
            # Strength-shaped sessions only (session_types.stat_shape): on a
            # double-session day this must never grab the run.
            row = conn.execute(
                "SELECT w.workout_id FROM workouts w "
                "JOIN session_types st ON st.name = w.session_type AND st.stat_shape='strength' "
                "WHERE date(w.scheduled_time) = ? ORDER BY w.scheduled_time DESC LIMIT 1",
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
def log_workout(
    description: str,
    date: str | None = None,
    session_type: str = "crossfit",
    plan: str | None = None,
    allow_duplicate: bool = False,
) -> str:
    """Record that a workout happened — for sessions with no Arbox class behind them.

    Use for off-gym training: a hotel WOD, a session at another gym, travel
    workouts, or a new activity type. NEVER for a class the Arbox sync already
    tracks — that row exists; enrich it with log_wod_result / log_exercise_stats
    / log_cardio_stats instead. For a run, prefer log_cardio_stats directly —
    it records the session and the stats in one call.

    Any session_type string is accepted (e.g. 'climbing'); an unlisted type
    still counts toward its plan, it just gets no automatic stat routing.
    The reply gives the workout_id — pass it to the stat tools when logging
    stats for anything but today's session.

    Args:
        description: What the session was (e.g. 'Hotel WOD: 5 rounds burpees/push-ups/squats')
        date: Session date as YYYY-MM-DD (defaults to today in Israel time)
        session_type: 'crossfit', 'running', 'walking', 'hiking', 'mobility', or any other activity name
        plan: Plan to attach to, by id or name. Omit to attach by the plan's
            binding (crossfit → the arbox-bound plan, running → the running-bound
            plan, anything else → the manual-bound plan).
        allow_duplicate: Pass true only if this really is a second session of the
            same type on the same day.
    """
    try:
        if not description or not description.strip():
            return "Error: description is required."
        if date is not None and not _valid_date(date):
            return "Error: date must be 'YYYY-MM-DD'."
        session_type = session_type.strip().lower()
        session_date = date or datetime.now(ISRAEL_TZ).strftime("%Y-%m-%d")

        conn = _get_db()
        if not allow_duplicate:
            existing = conn.execute(
                "SELECT workout_id, status, description FROM workouts "
                "WHERE date(scheduled_time)=? AND session_type=?",
                (session_date, session_type),
            ).fetchall()
            if existing:
                listing = "; ".join(
                    f"workout {r['workout_id']} ({r['status']}): {(r['description'] or '')[:60]}"
                    for r in existing
                )
                conn.close()
                return (
                    f"A {session_type} session already exists on {session_date} — {listing}. "
                    "If this is that session, enrich it (log_wod_result / log_exercise_stats / "
                    "log_cardio_stats with its workout_id) instead of creating a duplicate. "
                    "If it really was a separate second session, call again with allow_duplicate=true."
                )

        plan_id, err = _resolve_plan(conn, plan, session_type)
        if err:
            conn.close()
            return f"Error: {err}"

        workout_id = _create_workout(conn, description, session_date, session_type, plan_id)
        shape = _stat_shape(conn, session_type)
        plan_name = conn.execute("SELECT name FROM plans WHERE plan_id=?", (plan_id,)).fetchone()["name"]
        conn.close()

        reply = (
            f"Logged workout {workout_id}: {description.strip()} "
            f"({session_type}, {session_date}, plan: {plan_name})."
        )
        if shape == "cardio":
            reply += f" Now call log_cardio_stats(workout_id={workout_id}) with the session stats."
        elif shape == "strength":
            reply += " Attach lifts with log_exercise_stats and the WOD score with log_wod_result."
        else:
            reply += (
                f" Note: '{session_type}' has no automatic stat routing; to attach stats anyway, "
                f"pass workout_id={workout_id} explicitly."
            )
        return reply

    except Exception as e:
        return f"Error logging workout: {e}"


@tool_register(namespace="fitness")
@tool
def log_cardio_stats(
    duration_min: float,
    distance_km: float | None = None,
    avg_hr: int | None = None,
    pain_level: int = 0,
    prehab_done: bool = False,
    prehab_notes: str = "",
    notes: str = "",
    workout_id: int | None = None,
    description: str = "",
    date: str | None = None,
) -> str:
    """Attach cardio stats (run/walk/row/hike) to a workout — creating the session if needed.

    Call this immediately when Roi reports a cardio session's numbers.
    Always call this tool — never just acknowledge without saving.

    Auto-links to the day's most recent cardio-shaped workout that has no
    stats yet (e.g. a synced endurance class, or a log_workout row). If none
    exists, it records the session itself as a running workout — check the
    running program memory notes first and pass the program-matched
    description (e.g. 'Phase 0 Session 1: 30-min brisk walk'). For cardio
    stats on a non-running session (a hike, a row), create it first with
    log_workout and pass its workout_id.

    Args:
        duration_min: Total duration in minutes (e.g. 32.0) — required.
        distance_km: Distance in km (from watch GPS); pace is derived.
        avg_hr: Average heart rate in bpm
        pain_level: 0=none, 1=slight, 2=moderate, 3=stop-sign
        prehab_done: Whether pre-hab exercises were completed after the session
        prehab_notes: Description of pre-hab done (e.g. 'Tibialis 3×15, Calf raises 3×15')
        notes: Any additional session notes
        workout_id: Attach to this specific workout. Omit to auto-link (or create).
        description: Session description, used only when creating a new running
            workout (required then; must match the running program phase).
        date: Session date as YYYY-MM-DD (defaults to today in Israel time)
    """
    try:
        if date is not None and not _valid_date(date):
            return "Error: date must be 'YYYY-MM-DD'."
        session_date = date or datetime.now(ISRAEL_TZ).strftime("%Y-%m-%d")

        conn = _get_db()
        created = False
        if workout_id is None:
            row = conn.execute(
                "SELECT w.workout_id, w.description FROM workouts w "
                "JOIN session_types st ON st.name = w.session_type AND st.stat_shape='cardio' "
                "WHERE date(w.scheduled_time)=? AND w.workout_id NOT IN "
                "(SELECT workout_id FROM cardio_logs WHERE workout_id IS NOT NULL) "
                "ORDER BY w.scheduled_time DESC LIMIT 1",
                (session_date,),
            ).fetchone()
            if row:
                workout_id = row["workout_id"]
                target_desc = row["description"] or ""
            else:
                # No stats-less cardio row — but if the day already has cardio
                # sessions *with* stats, creating another here would silently
                # double-count the day. A genuine second session goes through
                # log_workout's explicit allow_duplicate gate instead.
                enriched = conn.execute(
                    "SELECT w.workout_id FROM workouts w "
                    "JOIN session_types st ON st.name = w.session_type AND st.stat_shape='cardio' "
                    "WHERE date(w.scheduled_time)=?",
                    (session_date,),
                ).fetchall()
                if enriched:
                    ids = ", ".join(str(r["workout_id"]) for r in enriched)
                    conn.close()
                    return (
                        f"Error: every cardio session on {session_date} (workout {ids}) already has "
                        "stats — this tool never overwrites. If this really was a separate second "
                        "session, create it with log_workout(session_type='running', "
                        "allow_duplicate=true) and pass its workout_id here."
                    )
                if not description.strip():
                    conn.close()
                    return (
                        f"Error: no cardio-shaped workout on {session_date} to attach to. "
                        "Pass description (matched to the running program notes) to record the "
                        "session, or workout_id to attach to a specific one."
                    )
                plan_id, err = _resolve_plan(conn, None, "running")
                if err:
                    conn.close()
                    return f"Error: {err}"
                workout_id = _create_workout(conn, description, session_date, "running", plan_id)
                target_desc = description.strip()
                created = True
        else:
            row = conn.execute(
                "SELECT workout_id, description FROM workouts WHERE workout_id=?", (workout_id,)
            ).fetchone()
            if not row:
                conn.close()
                return f"Error: no workout {workout_id}."
            target_desc = row["description"] or ""

        if conn.execute(
            "SELECT 1 FROM cardio_logs WHERE workout_id=?", (workout_id,)
        ).fetchone():
            conn.close()
            return (
                f"Error: workout {workout_id} already has cardio stats — this tool never "
                "overwrites. Use query_fitness_db to inspect them."
            )

        avg_pace_sec = _insert_cardio(
            conn, workout_id, duration_min, distance_km, avg_hr,
            pain_level, prehab_done, prehab_notes, notes,
        )
        conn.close()

        verb = "Logged" if created else f"Stats attached to workout {workout_id}:"
        head = f"{verb} {target_desc}".rstrip() if created or target_desc else f"Stats attached to workout {workout_id}"
        parts = [head + (f" (workout {workout_id})" if created else "")]
        parts += _cardio_parts(duration_min, distance_km, avg_pace_sec, avg_hr, pain_level, prehab_done, prehab_notes)
        return "\n".join(parts)

    except Exception as e:
        return f"Error logging cardio stats: {e}"


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
    """Deprecated alias for log_cardio_stats — prefer that tool.

    Kept so recent conversation history stays executable; records the session
    and stats exactly as log_cardio_stats(description=...) does.

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
    return log_cardio_stats.func(
        duration_min=duration_min,
        distance_km=distance_km,
        avg_hr=avg_hr,
        pain_level=pain_level,
        prehab_done=prehab_done,
        prehab_notes=prehab_notes,
        notes=notes,
        description=description,
        date=date,
    )


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
