"""Reading the record back — weekly adherence and multi-week consistency."""

from datetime import datetime, timedelta

from langchain_core.tools import tool

from timeutils import israel_week_bounds
from tools.fitness._db import ISRAEL_TZ, _fmt_pace, _get_db, _target_for_week
from tools.registry import tool_register


@tool_register(namespace="fitness")
@tool
def get_weekly_fitness_summary() -> str:
    """Get a summary of this week's fitness activity: quota progress and logged lifts.

    Use for weekly check-ins, Sunday reviews, or when the user asks 'how am I doing this week?'

    Returns: sessions completed vs target, list of workouts, and recent exercise logs.
    """
    try:
        conn = _get_db()
        week_start, week_end = israel_week_bounds()

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

        now = datetime.now(ISRAEL_TZ)
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
                week_target = _target_for_week(conn, p["plan_id"], ws, target)
                done = conn.execute(
                    "SELECT COUNT(*) FROM workouts WHERE plan_id=? AND status='completed' "
                    "AND date(scheduled_time) BETWEEN ? AND ?",
                    (p["plan_id"], ws, we),
                ).fetchone()[0]
                met = isinstance(week_target, int) and week_target > 0 and done >= week_target
                if met:
                    hits += 1
                # The current week (i==0) is still in progress: count it toward
                # the streak if already met, but an unmet current week is
                # pending, not a miss, so it must not close out the streak —
                # only i>=1 (a week that has fully elapsed) can do that.
                if i == 0:
                    if met:
                        streak += 1
                elif streak_open and met:
                    streak += 1
                else:
                    streak_open = False
                tag = "✓" if met else "·"
                label = "this week" if i == 0 else f"-{i}w"
                wk_tgt_label = week_target if week_target else "?"
                lines.append(f"  {tag} {ws} ({label}): {done}/{wk_tgt_label}")
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
