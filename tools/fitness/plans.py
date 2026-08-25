"""Training plans — the weekly target and the history behind it."""

from datetime import datetime, timedelta

from langchain_core.tools import tool

from timeutils import israel_week_bounds
from tools.fitness._db import ISRAEL_TZ, _get_db, _valid_date
from tools.registry import tool_register


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
            conn.execute(
                "INSERT INTO plan_target_history (plan_id, target, effective_from) VALUES (?, ?, ?)",
                (pid, weekly_target_count, sd),
            )
            conn.commit()
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

            # Capture the pre-update target so a real change can be recorded in
            # plan_target_history — past weeks stay judged by the target that
            # was actually in force then, not silently rewritten by this edit.
            prev = conn.execute("SELECT weekly_target_count FROM plans WHERE plan_id=?", (plan_id,)).fetchone()
            target_changed = (
                weekly_target_count is not None
                and prev is not None
                and weekly_target_count != prev["weekly_target_count"]
            )

            values.append(plan_id)
            conn.execute(f"UPDATE plans SET {', '.join(fields)} WHERE plan_id = ?", values)

            if target_changed:
                effective = datetime.now(ISRAEL_TZ).strftime("%Y-%m-%d")
                # Same-day re-edit replaces rather than stacks: there is only
                # ever one target in force on a given date.
                conn.execute(
                    "DELETE FROM plan_target_history WHERE plan_id=? AND effective_from=?",
                    (plan_id, effective),
                )
                conn.execute(
                    "INSERT INTO plan_target_history (plan_id, target, effective_from) VALUES (?, ?, ?)",
                    (plan_id, weekly_target_count, effective),
                )

            conn.commit()
            conn.close()
            summary = ", ".join(f.split(" = ")[0] + "=" + str(v) for f, v in zip(fields, values[:-1]))
            return f"Updated plan {plan_id}: {summary}."

        elif action == "list":
            week_start, week_end = israel_week_bounds()
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
