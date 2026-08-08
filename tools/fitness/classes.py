"""Arbox classes — reading the gym schedule and reconciling it into `workouts`."""

import os
from datetime import datetime, timedelta, timezone

from langchain_core.tools import tool

from tools.fitness._arbox import (
    ARBOX_REGISTRATION_HORIZON_HOURS,
    _arbox_post,
    _get_session_programming,
    _parse_wod_tracks,
)
from tools.fitness._db import ISRAEL_TZ, _get_db, _valid_date
from tools.registry import tool_register

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
