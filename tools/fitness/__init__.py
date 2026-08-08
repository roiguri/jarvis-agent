"""Fitness skill — Arbox classes, workout logging, running, training plans.

One module per concern, so a tool and the rows it owns stay together; `_db.py`
holds the schema and the helpers they share, `_arbox.py` the gym's API surface.
Importing this package imports each module, running its ``@tool_register``
side-effects.
"""

from tools.fitness.classes import (  # noqa: F401
    fetch_upcoming_arbox_classes,
    fetch_weekly_gym_schedule,
    get_daily_programming,
    sync_arbox_attendance,
)
from tools.fitness.logs import (  # noqa: F401
    get_today_workout_id,
    log_exercise_stats,
    log_running_session,
    log_wod_result,
    query_exercise_history,
)
from tools.fitness.plans import manage_fitness_plan  # noqa: F401
from tools.fitness.query import query_fitness_db  # noqa: F401
from tools.fitness.reports import (  # noqa: F401
    get_adherence_report,
    get_weekly_fitness_summary,
)

__all__ = [
    "fetch_upcoming_arbox_classes",
    "fetch_weekly_gym_schedule",
    "get_adherence_report",
    "get_daily_programming",
    "get_today_workout_id",
    "get_weekly_fitness_summary",
    "log_exercise_stats",
    "log_running_session",
    "log_wod_result",
    "manage_fitness_plan",
    "query_exercise_history",
    "query_fitness_db",
    "sync_arbox_attendance",
]
