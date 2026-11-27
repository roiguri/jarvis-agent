"""Arbox classes — reading the gym schedule and reconciling it into `workouts`."""

import logging
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

logger = logging.getLogger(__name__)


def _fetch_schedule(from_dt: datetime, to_dt: datetime) -> list[dict]:
    """The gym's class list for a UTC window. Every schedule read goes through here."""
    body = {
        "from": from_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "to": to_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "locations_box_id": int(os.environ.get("ARBOX_LOCATIONS_BOX_ID", "0")),
        "boxes_id": int(os.environ.get("ARBOX_BOX_ID", "0")),
    }
    resp = _arbox_post("/api/v2/schedule/betweenDates", body)
    # A reply without its data field is an error, never an empty schedule:
    # read as empty it would look like every booking was dropped.
    if not isinstance(resp, dict) or "data" not in resp:
        raise RuntimeError("Arbox schedule reply had no 'data' field")
    return resp["data"] or []


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
        where += f" AND external_id NOT IN ({placeholders})"
        params += list(registered_ids)

    doomed = conn.execute(
        f"SELECT workout_id, scheduled_time, description {where}", params
    ).fetchall()
    if doomed:
        conn.execute(f"DELETE {where}", params)
    return [dict(r) for r in doomed]


def _sync_registered_classes() -> str:
    """Re-read the registration horizon and reconcile the local `workouts` rows.

    Both `fetch_upcoming_arbox_classes` and the register/cancel write path call
    this, so a booking made through Jarvis reaches the database by exactly the
    same route as one made in the gym's own app: insert-on-register and
    purge-on-cancel both fall out of the authoritative-set logic below rather
    than a second copy of it that could drift.

    Raises on Arbox failure; callers own the error wording.
    """
    return _apply_registered(_fetch_registered())


def _fetch_registered() -> list[dict]:
    """The classes the user is registered for across the registration
    horizon. Only reads Arbox — never touches the database — so a gate can
    call it every tick without consuming the change it looks for."""
    now = datetime.now(timezone.utc)
    to_dt = now + timedelta(hours=ARBOX_REGISTRATION_HORIZON_HOURS)
    classes = _fetch_schedule(now, to_dt)
    return [c for c in classes if c.get("user_booked") is not None]


def _apply_registered(registered: list[dict]) -> str:
    """Reconcile `workouts` with a complete registered set from
    `_fetch_registered`: insert new classes, purge dropped ones. Returns the
    user-facing summary."""
    now_il = datetime.now(ISRAEL_TZ)
    now_str = now_il.strftime("%Y-%m-%d %H:%M:%S")
    horizon_str = (
        now_il + timedelta(hours=ARBOX_REGISTRATION_HORIZON_HOURS)
    ).strftime("%Y-%m-%d %H:%M:%S")

    conn = _get_db()
    try:
        # Synced classes attach to the one active arbox-bound plan — declared
        # via plans.binding, never guessed. Zero or several matches is a
        # configuration problem the owner has to resolve; erroring beats
        # attaching a class to the wrong plan's quota.
        bound = conn.execute(
            "SELECT plan_id FROM plans WHERE status='active' AND binding='arbox'"
        ).fetchall()
        if len(bound) != 1:
            raise RuntimeError(
                f"expected exactly one active plan with binding='arbox', found {len(bound)} — "
                "fix with manage_fitness_plan (set binding / status) and retry"
            )
        plan_id = bound[0]["plan_id"]

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
                   (external_id, plan_id, scheduled_time, description, source)
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


@tool_register(namespace="fitness/schedule")
@tool
def fetch_upcoming_arbox_classes() -> str:
    """Fetch Roi's registered Arbox classes (72h horizon) and their WODs, upsert to the
    fitness DB, and remove locally cancelled bookings.

    Returns registered classes (times + WODs) plus any dropped-class notices.
    """
    try:
        return _sync_registered_classes()
    except RuntimeError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error fetching classes: {e}"


@tool_register(namespace="fitness/schedule")
@tool
def fetch_weekly_gym_schedule(days_ahead: int = 7) -> str:
    """Read-only: fetch all upcoming gym classes (booked or not) with WODs for scouting
    and booking decisions. Does not write to the DB.

    Args:
        days_ahead: Days ahead to fetch (default 7).
    """
    try:
        # Whole days at both ends: from midnight today through the end of the
        # last day, so the final day's evening classes are included.
        now = datetime.now(timezone.utc)
        from_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        to_dt = (now + timedelta(days=days_ahead)).replace(
            hour=23, minute=59, second=59, microsecond=0
        )
        classes = _fetch_schedule(from_dt, to_dt)

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


@tool_register(namespace="fitness/schedule")
@tool
def get_daily_programming(date: str | None = None) -> str:
    """Show ALL Arbox tracks for a date in full (WOD, Endurance, PUMP, W.LIFTING, etc.).

    Use only when Roi explicitly asks about another track or wants to compare tracks;
    WOD and Saturday Endurance are already returned by the schedule tools.

    Args:
        date: 'YYYY-MM-DD' (defaults to today, Israel time).
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


ATTENDANCE_CHECK_DELAY = timedelta(hours=2)
ATTENDANCE_MISSED_GRACE = timedelta(hours=12)
ATTENDANCE_LOOKBACK_DAYS = 60


def _sync_attendance(now: datetime | None = None) -> tuple[int, int]:
    """Deterministically resolve past scheduled Arbox classes to completed or missed.

    DB-first: returns (0, 0) without hitting Arbox if no scheduled Arbox class is
    past ATTENDANCE_CHECK_DELAY. Closes SQLite before the HTTP call and guards
    updates with ``AND status = 'scheduled'`` so concurrent user logging is never
    overwritten. Marks attended classes completed right away, waits
    ATTENDANCE_MISSED_GRACE before marking an unattended class missed, and ignores
    a suspicious empty ``[]`` response when recent completed Arbox workouts exist.
    """
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    check_cutoff = (now_utc - ATTENDANCE_CHECK_DELAY).astimezone(ISRAEL_TZ).strftime("%Y-%m-%d %H:%M:%S")
    missed_cutoff = (now_utc - ATTENDANCE_MISSED_GRACE).astimezone(ISRAEL_TZ).strftime("%Y-%m-%d %H:%M:%S")
    lower_bound = (now_utc - timedelta(days=ATTENDANCE_LOOKBACK_DAYS)).astimezone(ISRAEL_TZ).strftime("%Y-%m-%d 00:00:00")

    conn = _get_db()
    try:
        scheduled = conn.execute(
            "SELECT workout_id, scheduled_time FROM workouts "
            "WHERE status = 'scheduled' AND source = 'arbox' "
            "AND scheduled_time >= ? AND scheduled_time < ?",
            (lower_bound, check_cutoff),
        ).fetchall()
        if not scheduled:
            return 0, 0
        has_recent_completed = conn.execute(
            "SELECT 1 FROM workouts WHERE status = 'completed' AND source = 'arbox' "
            "AND scheduled_time >= ? LIMIT 1",
            (lower_bound,),
        ).fetchone() is not None
    finally:
        conn.close()

    from_dt = (now_utc - timedelta(days=ATTENDANCE_LOOKBACK_DAYS)).strftime("%Y-%m-%dT00:00:00.000Z")
    to_dt = now_utc.strftime("%Y-%m-%dT23:59:59.999Z")
    body = {
        "from": from_dt,
        "to": to_dt,
        "locations_box_id": int(os.environ.get("ARBOX_LOCATIONS_BOX_ID", "0")),
    }
    resp = _arbox_post("/api/v2/schedule/weekly", body)
    if not isinstance(resp, list):
        logger.warning("arbox attendance sync: unexpected response type %s", type(resp).__name__)
        return 0, 0
    attended_dates = {d for d in resp if isinstance(d, str)}
    if not attended_dates and has_recent_completed:
        logger.warning("arbox attendance sync: empty attendance list despite recent completed classes; skipping")
        return 0, 0

    conn = _get_db()
    try:
        completed_count = 0
        missed_count = 0
        for w in scheduled:
            workout_date = w["scheduled_time"][:10]
            if workout_date in attended_dates:
                cur = conn.execute(
                    "UPDATE workouts SET status = 'completed' WHERE workout_id = ? AND status = 'scheduled'",
                    (w["workout_id"],),
                )
                completed_count += cur.rowcount
            elif w["scheduled_time"] < missed_cutoff:
                cur = conn.execute(
                    "UPDATE workouts SET status = 'missed' WHERE workout_id = ? AND status = 'scheduled'",
                    (w["workout_id"],),
                )
                missed_count += cur.rowcount

        if completed_count or missed_count:
            conn.commit()
            logger.info("arbox attendance synced: %d completed, %d missed", completed_count, missed_count)
        return completed_count, missed_count
    finally:
        conn.close()
