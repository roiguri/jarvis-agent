import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from langchain_core.tools import tool

import config
from tools.registry import tool_register

DB_PATH = os.path.join(config.DATA_DIR, "fitness", "fitness.sqlite")
ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")
ARBOX_BASE = "https://apiappv2.arboxapp.com"

# Read-only access for the ad-hoc query tool: a separate connection opened in
# SQLite read-only URI mode, so writes are physically impossible (not policy).
_FITNESS_RO_URI = f"file:{DB_PATH}?mode=ro"
_QUERY_ROW_CAP = 200

# Arbox only allows registering for a class up to this many hours in advance
# (rolling, time-precise). This is the only window over which a complete fetch
# yields an authoritative registered set, which is what makes safely deleting
# dropped-registration "ghost" rows possible. Both the Arbox fetch window and
# the reconciliation window derive from this constant.
ARBOX_REGISTRATION_HORIZON_HOURS = 72


def _arbox_headers() -> dict:
    token = os.environ.get("ARBOX_ACCESS_TOKEN", "")
    if not token:
        raise RuntimeError("ARBOX_ACCESS_TOKEN not set in environment")
    return {
        "accesstoken": token,
        "whitelabel": os.environ.get("ARBOX_WHITELABEL", ""),
        "version": "11",
        "referername": "app",
        "content-type": "application/json",
    }


def _arbox_post(path: str, body: dict) -> dict:
    resp = requests.post(f"{ARBOX_BASE}{path}", headers=_arbox_headers(), json=body, timeout=15)
    if resp.status_code == 401:
        raise RuntimeError("Arbox session expired. Please update ARBOX_ACCESS_TOKEN in /app/secrets/.env and restart the service.")
    resp.raise_for_status()
    return resp.json()


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
            start_date          DATE
        );

        CREATE TABLE IF NOT EXISTS workouts (
            workout_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id         INTEGER,
            arbox_class_id  TEXT UNIQUE,
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
    """)
    conn.commit()

    # Migrate existing databases: add new columns if missing
    for sql in [
        "ALTER TABLE workouts ADD COLUMN session_type TEXT DEFAULT 'crossfit'",
        "ALTER TABLE workouts ADD COLUMN wod_result TEXT",
        "ALTER TABLE workouts ADD COLUMN notes TEXT",
        "ALTER TABLE plans ADD COLUMN start_date DATE",
    ]:
        try:
            conn.execute(sql)
            conn.commit()
        except Exception:
            pass  # column already exists

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

    conn.close()


def _fmt_pace(avg_pace_sec: int) -> str:
    """Format seconds/km as mm'ss\"/km."""
    return f"{avg_pace_sec // 60}'{avg_pace_sec % 60:02d}\"/km"


_init_db()


# Tracks Roi follows: the WOD (and Endurance on Saturday) at his branch. PUMP /
# W.LIFTING / other-branch tracks are dropped from briefings (reachable via
# get_daily_programming). Branch keyword is env-overridable.
ARBOX_WOD_BRANCH = os.environ.get("ARBOX_WOD_BRANCH", "neve tzedek").strip().lower()
_FOLLOWED_TRACKS = ("wod", "endurance")


def _norm_category(name: str) -> str:
    """Case-fold and collapse whitespace for tolerant matching."""
    return " ".join((name or "").split()).lower()


def _score_track(category: str, prefer_category: str | None = None) -> int:
    """Rank a track for Roi's program (0 = not followed, skip). prefer_category
    is the class he booked — an exact-match tie-breaker, not a requirement."""
    cat = _norm_category(category)
    if not cat:
        return 0
    if prefer_category and cat == _norm_category(prefer_category):
        return 100
    followed = any(k in cat for k in _FOLLOWED_TRACKS)
    branch = bool(ARBOX_WOD_BRANCH) and ARBOX_WOD_BRANCH in cat
    if followed and branch:
        return 40  # e.g. WOD/ENDURANCE NEVE TZEDEK
    if branch:
        return 30
    if followed:
        return 20  # WOD at another branch — last resort
    return 0  # PUMP / W.LIFTING / OPEN GYM


def _parse_wod_tracks(date_str: str) -> list[tuple[str, str]]:
    """All posted (category, comment) tracks for a date. [] if none/on error."""
    try:
        data = _arbox_post("/api/v2/logbook/workouts", {"date": date_str})
    except Exception:
        return []
    tracks: list[tuple[str, str]] = []
    for group_list in data.get("data", []):
        for group in group_list:
            for exercise in group:
                comment = (exercise.get("comment") or "").strip()
                if not comment:
                    continue
                category = (exercise.get("box_categories") or {}).get("name", "")
                tracks.append((category, comment))
    return tracks


def _get_session_programming(date_str: str, prefer_category: str | None = None) -> str:
    """Full text of the single track Roi follows for a date. "" if none posted.

    Picks the best-scoring track (see _score_track) instead of concatenating all
    of them; prefer_category is the registered class's category.
    """
    best: tuple[int, str, str] | None = None  # (score, category, comment)
    for category, comment in _parse_wod_tracks(date_str):
        score = _score_track(category, prefer_category)
        if score > 0 and (best is None or score > best[0]):
            best = (score, category, comment)
    if best is None:
        return ""
    _, category, comment = best
    return f"[{category}] {comment}" if category else comment


def _valid_date(s: str) -> bool:
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except (ValueError, TypeError):
        return False


@tool_register(namespace="fitness")
@tool
def manage_fitness_plan(
    action: str,
    plan_id: int | None = None,
    name: str | None = None,
    tracking_mode: str | None = None,
    weekly_target_count: int | None = None,
    status: str | None = None,
    start_date: str | None = None,
) -> str:
    """Create, update, or list fitness plans.

    action='create': Create a new plan. Requires name, tracking_mode ('flexible_quota' or
        'strict_sequential'), and weekly_target_count. start_date defaults to today.
    action='update': Update an existing plan. Requires plan_id. Provide any fields to change:
        name, weekly_target_count, status ('active', 'paused', 'completed'), or start_date.
    action='list': List all plans with current status, start date, and this-week session counts.

    Args:
        action: 'create', 'update', or 'list'
        plan_id: Required for 'update'. The plan to modify.
        name: Plan name (e.g. 'Weekly CrossFit')
        tracking_mode: 'flexible_quota' (CrossFit — weekly count goal) or 'strict_sequential' (running — ordered sessions)
        weekly_target_count: How many sessions per week to aim for
        status: 'active', 'paused', or 'completed'
        start_date: 'YYYY-MM-DD' the plan begins. Adherence reports ignore weeks before it. Create defaults to today.
    """
    try:
        if start_date is not None and not _valid_date(start_date):
            return "Error: start_date must be 'YYYY-MM-DD'."
        conn = _get_db()
        if action == "create":
            if not all([name, tracking_mode, weekly_target_count is not None]):
                return "Error: create requires name, tracking_mode, and weekly_target_count."
            if tracking_mode not in ("flexible_quota", "strict_sequential"):
                return "Error: tracking_mode must be 'flexible_quota' or 'strict_sequential'."
            sd = start_date or datetime.now(ISRAEL_TZ).strftime("%Y-%m-%d")
            conn.execute(
                "INSERT INTO plans (name, tracking_mode, weekly_target_count, start_date) VALUES (?, ?, ?, ?)",
                (name, tracking_mode, weekly_target_count, sd),
            )
            conn.commit()
            pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.close()
            return f"Created plan '{name}' (id={pid}, mode={tracking_mode}, target={weekly_target_count}/week, start={sd})."

        elif action == "update":
            if not plan_id:
                return "Error: update requires plan_id."
            fields, values = [], []
            if name:
                fields.append("name = ?"); values.append(name)
            if weekly_target_count is not None:
                fields.append("weekly_target_count = ?"); values.append(weekly_target_count)
            if status:
                fields.append("status = ?"); values.append(status)
            if start_date:
                fields.append("start_date = ?"); values.append(start_date)
            if not fields:
                return "Error: provide at least one field to update (name, weekly_target_count, status, start_date)."
            values.append(plan_id)
            conn.execute(f"UPDATE plans SET {', '.join(fields)} WHERE plan_id = ?", values)
            conn.commit()
            conn.close()
            summary = ", ".join(f.split(" = ")[0] + "=" + str(v) for f, v in zip(fields, values[:-1]))
            return f"Updated plan {plan_id}: {summary}."

        elif action == "list":
            now = datetime.now(timezone.utc)
            days_since_sunday = (now.weekday() + 1) % 7
            week_start = (now - timedelta(days=days_since_sunday)).strftime("%Y-%m-%d")
            week_end = (now + timedelta(days=6 - days_since_sunday)).strftime("%Y-%m-%d")
            plans = conn.execute("SELECT * FROM plans ORDER BY plan_id").fetchall()
            if not plans:
                return "No fitness plans found. Use action='create' to add one."
            lines = []
            for p in plans:
                done = conn.execute(
                    "SELECT COUNT(*) FROM workouts WHERE plan_id=? AND status='completed' AND date(scheduled_time) BETWEEN ? AND ?",
                    (p["plan_id"], week_start, week_end),
                ).fetchone()[0]
                target = p["weekly_target_count"] or "?"
                start = p["start_date"] or "—"
                lines.append(
                    f"[{p['plan_id']}] {p['name']} | {p['tracking_mode']} | "
                    f"target: {target}/week | this week: {done}/{target} | "
                    f"status: {p['status']} | start: {start}"
                )
            conn.close()
            return "\n".join(lines)

        else:
            return "Error: action must be 'create', 'update', or 'list'."

    except Exception as e:
        return f"Error: {e}"


def _purge_dropped_arbox_classes(conn, registered_ids, now_str, horizon_str):
    """Delete future, in-horizon, still-`scheduled` Arbox rows the user is no
    longer registered for, and return the deleted rows (for a user notice).

    The caller fetches the full 72h Arbox registration horizon, so within
    (now, now+72h] the registered set is complete and authoritative — any
    `scheduled`/arbox row not in it is a dropped registration, not a class we
    merely failed to fetch. The upper (horizon) bound is kept purely as a
    blast-radius limiter: if Arbox returns an erroneous empty set (HTTP 200,
    no rows), at most the next 72h is purged and the next successful fetch
    re-inserts any still-registered rows. Rows with logged lifts/cardio are
    never touched (defensive — a future unattended class has none anyway).
    """
    where = (
        "FROM workouts WHERE source='arbox' AND status='scheduled' "
        "AND scheduled_time > ? AND scheduled_time <= ? "
        "AND workout_id NOT IN (SELECT workout_id FROM exercise_logs WHERE workout_id IS NOT NULL) "
        "AND workout_id NOT IN (SELECT workout_id FROM cardio_logs WHERE workout_id IS NOT NULL)"
    )
    params = [now_str, horizon_str]
    if registered_ids:
        placeholders = ",".join("?" * len(registered_ids))
        where += f" AND arbox_class_id NOT IN ({placeholders})"
        params += list(registered_ids)

    doomed = conn.execute(
        f"SELECT workout_id, scheduled_time, description {where}", params
    ).fetchall()
    if doomed:
        conn.execute(f"DELETE {where}", params)
    return [dict(r) for r in doomed]


@tool_register(namespace="fitness")
@tool
def fetch_upcoming_arbox_classes() -> str:
    """Fetch the Arbox classes the user is registered for and upsert them into the fitness DB.

    Covers Arbox's full 72h registration horizon (you cannot register further
    ahead than that). Also fetches the WOD for each class date, and removes any
    local class the user is no longer registered for (un-registered/cancelled
    in Arbox) so it stops driving reminders, briefings, and attendance stats.
    Use this each morning to get today's class and WOD before a briefing.

    Returns a summary of registered classes (times + WODs), plus a notice for
    any class removed because its registration was dropped.
    """
    try:
        now = datetime.now(timezone.utc)
        to_dt = now + timedelta(hours=ARBOX_REGISTRATION_HORIZON_HOURS)
        body = {
            "from": now.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "to": to_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "locations_box_id": int(os.environ.get("ARBOX_LOCATIONS_BOX_ID", "0")),
            "boxes_id": int(os.environ.get("ARBOX_BOX_ID", "0")),
        }
        data = _arbox_post("/api/v2/schedule/betweenDates", body)
        classes = data.get("data", [])
        registered = [c for c in classes if c.get("user_booked") is not None]

        now_il = datetime.now(ISRAEL_TZ)
        now_str = now_il.strftime("%Y-%m-%d %H:%M:%S")
        horizon_str = (
            now_il + timedelta(hours=ARBOX_REGISTRATION_HORIZON_HOURS)
        ).strftime("%Y-%m-%d %H:%M:%S")

        conn = _get_db()
        try:
            plan = conn.execute("SELECT plan_id FROM plans WHERE status='active' LIMIT 1").fetchone()
            plan_id = plan["plan_id"] if plan else None

            results = []
            for cls in registered:
                schedule_id = str(cls["id"])
                date_str = cls["date"]
                time_str = cls["time"]
                category = (cls.get("box_categories") or {}).get("name", "WOD")
                scheduled_dt = f"{date_str} {time_str}:00"

                wod = _get_session_programming(date_str, prefer_category=category)

                conn.execute(
                    """INSERT OR IGNORE INTO workouts
                       (arbox_class_id, plan_id, scheduled_time, description, source)
                       VALUES (?, ?, ?, ?, 'arbox')""",
                    (schedule_id, plan_id, scheduled_dt, wod or None),
                )

                wod_text = wod or "WOD not yet posted"
                results.append(f"• {date_str} at {time_str} ({category})\n  WOD: {wod_text}")

            # Always reconcile — an empty registered set is the legitimate
            # "dropped everything" case and must still purge ghosts.
            registered_ids = {str(c["id"]) for c in registered}
            removed = _purge_dropped_arbox_classes(conn, registered_ids, now_str, horizon_str)
            conn.commit()
        finally:
            conn.close()

        if registered:
            msg = f"Found {len(registered)} registered class(es):\n" + "\n".join(results)
        else:
            msg = "No registered classes found in the next 72h."
        if removed:
            dropped = "; ".join(r["scheduled_time"] for r in removed)
            msg += (
                f"\n\nRemoved {len(removed)} class(es) you're no longer "
                f"registered for: {dropped}. This may affect your weekly quota."
            )
        return msg

    except RuntimeError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error fetching classes: {e}"


@tool_register(namespace="fitness")
@tool
def fetch_weekly_gym_schedule(days_ahead: int = 7) -> str:
    """Fetch the full gym schedule for the coming days for WOD scouting.

    Use this when the user asks 'what should I book this week?' or wants to plan
    around specific workout types. Returns ALL classes (not just registered ones)
    with their WOD descriptions so the LLM can reason about which to attend.

    Does NOT write to the database — purely for decision-making.

    Args:
        days_ahead: How many days ahead to fetch (default 7 = one week).
    """
    try:
        now = datetime.now(timezone.utc)
        to_dt = now + timedelta(days=days_ahead)
        body = {
            "from": now.strftime("%Y-%m-%dT00:00:00.000Z"),
            "to": to_dt.strftime("%Y-%m-%dT23:59:59.999Z"),
            "locations_box_id": int(os.environ.get("ARBOX_LOCATIONS_BOX_ID", "0")),
            "boxes_id": int(os.environ.get("ARBOX_BOX_ID", "0")),
        }
        data = _arbox_post("/api/v2/schedule/betweenDates", body)
        classes = data.get("data", [])

        if not classes:
            return "No classes found in the schedule."

        conn = _get_db()
        try:
            friend_map = {
                int(r["membership_user_fk"]): r["name"]
                for r in conn.execute("SELECT membership_user_fk, name FROM friends").fetchall()
            }
        finally:
            conn.close()

        wod_cache: dict[tuple[str, str], str] = {}

        lines = [f"Gym schedule for the next {days_ahead} day(s):"]
        for cls in classes:
            if cls.get("past"):
                continue
            date_str = cls["date"]
            time_str = cls["time"]
            category = (cls.get("box_categories") or {}).get("name", "")
            is_registered = cls.get("user_booked") is not None
            spots_left = cls.get("free", 0)

            cache_key = (date_str, category)
            if cache_key not in wod_cache:
                wod_cache[cache_key] = _get_session_programming(date_str, prefer_category=category)
            wod = wod_cache[cache_key]

            reg_marker = " ✓ REGISTERED" if is_registered else f" ({spots_left} spots left)"
            friend_marker = ""
            if friend_map:
                present = [
                    friend_map[bu["membership_user_fk"]]
                    for bu in (cls.get("booked_users") or [])
                    if bu.get("membership_user_fk") in friend_map
                ]
                if present:
                    friend_marker = f" + {', '.join(present)}"
            wod_line = wod or "WOD not yet posted"
            lines.append(f"\n{date_str} {time_str} | {category}{reg_marker}{friend_marker}\n  {wod_line}")

        return "\n".join(lines)

    except RuntimeError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error fetching schedule: {e}"


@tool_register(namespace="fitness")
@tool
def get_daily_programming(date: str | None = None) -> str:
    """Show ALL Arbox tracks for a date in full — WOD, Endurance, PUMP, W.LIFTING, etc.

    Briefings/scouting already surface the track Roi follows (WOD, or Saturday
    Endurance) via fetch_upcoming_arbox_classes / fetch_weekly_gym_schedule. Use
    this only when he explicitly asks about another track (e.g. "what's the PUMP
    today?") or wants to compare them.

    Args:
        date: 'YYYY-MM-DD'. Defaults to today (Israel time).
    """
    try:
        date_str = date or datetime.now(ISRAEL_TZ).strftime("%Y-%m-%d")
        if not _valid_date(date_str):
            return "Error: date must be 'YYYY-MM-DD'."
        tracks = _parse_wod_tracks(date_str)
        if not tracks:
            return f"No programming posted for {date_str} yet."
        lines = [f"All tracks for {date_str}:"]
        for category, comment in tracks:
            lines.append(f"\n[{category or 'UNLABELED'}]\n{comment}")
        return "\n".join(lines)
    except RuntimeError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error fetching programming: {e}"


@tool_register(namespace="fitness")
@tool
def sync_arbox_attendance() -> str:
    """Sync Arbox attendance data to the fitness DB.

    Fetches the list of dates the user actually attended the gym (from Arbox's attendance log)
    and marks the corresponding workouts in the DB as 'completed'. Also marks past scheduled
    workouts that were not attended as 'missed'.

    Call this periodically (e.g. during Sunday heartbeat review) to keep attendance accurate.

    Returns a summary of how many workouts were marked completed or missed.
    """
    try:
        now = datetime.now(timezone.utc)
        from_dt = (now - timedelta(days=60)).strftime("%Y-%m-%dT00:00:00.000Z")
        to_dt = now.strftime("%Y-%m-%dT23:59:59.999Z")

        body = {
            "from": from_dt,
            "to": to_dt,
            "locations_box_id": int(os.environ.get("ARBOX_LOCATIONS_BOX_ID", "0")),
        }
        resp = _arbox_post("/api/v2/schedule/weekly", body)
        attended_dates = set(resp) if isinstance(resp, list) else set()

        conn = _get_db()
        cutoff = (datetime.now(ISRAEL_TZ) - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
        scheduled = conn.execute(
            "SELECT workout_id, scheduled_time FROM workouts WHERE status='scheduled' AND scheduled_time < ?",
            (cutoff,),
        ).fetchall()

        completed_count = 0
        missed_count = 0
        for w in scheduled:
            workout_date = w["scheduled_time"][:10]
            if workout_date in attended_dates:
                conn.execute("UPDATE workouts SET status='completed' WHERE workout_id=?", (w["workout_id"],))
                completed_count += 1
            else:
                conn.execute("UPDATE workouts SET status='missed' WHERE workout_id=?", (w["workout_id"],))
                missed_count += 1

        conn.commit()
        conn.close()
        return f"Attendance synced: {completed_count} completed, {missed_count} missed."

    except RuntimeError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error syncing attendance: {e}"


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
            "UPDATE workouts SET wod_result = ?, notes = COALESCE(?, notes) WHERE workout_id = ?",
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


@tool_register(namespace="fitness")
@tool
def get_weekly_fitness_summary() -> str:
    """Get a summary of this week's fitness activity: quota progress and logged lifts.

    Use for weekly check-ins, Sunday reviews, or when the user asks 'how am I doing this week?'

    Returns: sessions completed vs target, list of workouts, and recent exercise logs.
    """
    try:
        conn = _get_db()
        now = datetime.now(timezone.utc)
        days_since_sunday = (now.weekday() + 1) % 7
        week_start = (now - timedelta(days=days_since_sunday)).strftime("%Y-%m-%d")
        week_end = (now + timedelta(days=6 - days_since_sunday)).strftime("%Y-%m-%d")

        plans = conn.execute("SELECT * FROM plans WHERE status='active'").fetchall()
        lines = [f"Week of {week_start}:"]

        for plan in plans:
            done = conn.execute(
                "SELECT COUNT(*) FROM workouts WHERE plan_id=? AND status='completed' AND date(scheduled_time) BETWEEN ? AND ?",
                (plan["plan_id"], week_start, week_end),
            ).fetchone()[0]
            target = plan["weekly_target_count"] or "?"
            pct = f"{done}/{target}"
            emoji = "✓" if isinstance(target, int) and done >= target else "·"
            lines.append(f"{emoji} {plan['name']}: {pct} sessions")

        workouts = conn.execute(
            "SELECT workout_id, scheduled_time, status, session_type, wod_result, description, notes FROM workouts "
            "WHERE date(scheduled_time) BETWEEN ? AND ? ORDER BY scheduled_time",
            (week_start, week_end),
        ).fetchall()

        if workouts:
            lines.append("\nSessions:")
            for w in workouts:
                time_str = w["scheduled_time"][5:16]
                desc = (w["description"] or "")[:70]
                session_type = w["session_type"] or "crossfit"

                if session_type == "running":
                    cardio = conn.execute(
                        "SELECT duration_min, distance_km, avg_pace_sec, avg_hr, pain_level "
                        "FROM cardio_logs WHERE workout_id = ?",
                        (w["workout_id"],),
                    ).fetchone()
                    if cardio:
                        parts = [f"{cardio['duration_min']:.0f} min"]
                        if cardio["distance_km"]:
                            parts.append(f"{cardio['distance_km']} km")
                        if cardio["avg_pace_sec"]:
                            parts.append(_fmt_pace(cardio["avg_pace_sec"]))
                        if cardio["avg_hr"]:
                            parts.append(f"HR {cardio['avg_hr']} bpm")
                        if cardio["pain_level"] and cardio["pain_level"] > 0:
                            parts.append(f"pain:{cardio['pain_level']}")
                        lines.append(f"  {time_str} [run] {desc} | {', '.join(parts)}")
                    else:
                        lines.append(f"  {time_str} [run] {desc}")
                else:
                    wod_result = f" → {w['wod_result']}" if w["wod_result"] else ""
                    note = f" — note: {w['notes']}" if w["notes"] else ""
                    lines.append(f"  {time_str} [{w['status']}] {desc}{wod_result}{note}")

        logs = conn.execute(
            "SELECT exercise_name, weight, sets, reps, notes, logged_at FROM exercise_logs "
            "WHERE date(logged_at) BETWEEN ? AND ? ORDER BY logged_at",
            (week_start, week_end),
        ).fetchall()

        if logs:
            lines.append("\nLogged lifts:")
            for lg in logs:
                notes_str = f" ({lg['notes']})" if lg["notes"] else ""
                lines.append(f"  {lg['logged_at'][:10]} — {lg['sets']}×{lg['reps']} {lg['exercise_name']} @ {lg['weight']}kg{notes_str}")
        else:
            lines.append("\nNo lifts logged this week yet.")

        conn.close()
        return "\n".join(lines)

    except Exception as e:
        return f"Error: {e}"


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


@tool_register(namespace="fitness")
@tool
def get_adherence_report(plan_id: int | None = None, weeks: int = 8) -> str:
    """Weekly adherence (completed vs target) over the last N weeks, per plan.

    Use for 'how consistent have I been?', streak/trend questions, or multi-week
    reviews. Complements get_weekly_fitness_summary (current week only). Weeks
    are Sunday-start, matching get_weekly_fitness_summary's quota math.

    Args:
        plan_id: Limit to one plan. Default: all active plans.
        weeks: Trailing weeks to report (default 8, clamped 1-52).
    """
    try:
        weeks = max(1, min(weeks, 52))
        conn = _get_db()
        if plan_id:
            plans = conn.execute("SELECT * FROM plans WHERE plan_id=?", (plan_id,)).fetchall()
        else:
            plans = conn.execute(
                "SELECT * FROM plans WHERE status='active' ORDER BY plan_id"
            ).fetchall()
        if not plans:
            conn.close()
            return "No matching plans."

        now = datetime.now(timezone.utc)
        this_sunday = now - timedelta(days=(now.weekday() + 1) % 7)
        out = []
        for p in plans:
            target = p["weekly_target_count"] or 0
            tgt_label = target if target else "?"
            sd = p["start_date"]  # None -> no clamp
            since = f" (since {sd})" if sd else ""
            lines = [f"{p['name']} (target {tgt_label}/week, status {p['status']}){since}:"]
            hits = 0
            eligible = 0
            streak = 0
            streak_open = True
            for i in range(weeks):
                wk = this_sunday - timedelta(weeks=i)
                ws = wk.strftime("%Y-%m-%d")
                we = (wk + timedelta(days=6)).strftime("%Y-%m-%d")
                if sd and we < sd:
                    continue  # week ends before the plan began — not a miss
                eligible += 1
                done = conn.execute(
                    "SELECT COUNT(*) FROM workouts WHERE plan_id=? AND status='completed' "
                    "AND date(scheduled_time) BETWEEN ? AND ?",
                    (p["plan_id"], ws, we),
                ).fetchone()[0]
                met = isinstance(target, int) and target > 0 and done >= target
                if met:
                    hits += 1
                    if streak_open:
                        streak += 1
                else:
                    streak_open = False
                tag = "✓" if met else "·"
                label = "this week" if i == 0 else f"-{i}w"
                lines.append(f"  {tag} {ws} ({label}): {done}/{tgt_label}")
            if eligible == 0:
                lines.append(f"  → plan started {sd}; no weeks in the last {weeks}w window.")
            else:
                pct = round(100 * hits / eligible)
                lines.append(
                    f"  → {hits}/{eligible} weeks met ({pct}%); current streak {streak}w"
                )
            out.append("\n".join(lines))
        conn.close()
        return "\n\n".join(out)

    except Exception as e:
        return f"Error: {e}"
