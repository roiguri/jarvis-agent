"""The process's one APScheduler, and how triggers are armed on it.

Jobs live in APScheduler's in-memory store; the trigger store is the durable
record, so every pending trigger is re-armed from it on startup.
"""

import asyncio
import logging
from datetime import datetime, timezone

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

from triggers import runner, store
from triggers.model import Trigger

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def init_scheduler() -> AsyncIOScheduler:
    global _scheduler
    _scheduler = AsyncIOScheduler()
    return _scheduler


def get_scheduler() -> AsyncIOScheduler:
    if _scheduler is None:
        raise RuntimeError("Scheduler not initialized — call init_scheduler() first")
    return _scheduler


def _job_id(trigger_id: str) -> str:
    return f"trigger_{trigger_id}"


def arm(trigger: Trigger, run_date: datetime | None = None, attempt: int = 0) -> None:
    """Schedule ``trigger`` to run at its instant, or at ``run_date`` for a retry."""
    get_scheduler().add_job(
        runner.run,
        DateTrigger(run_date=run_date or trigger.when.instant),
        id=_job_id(trigger.id),
        args=[trigger, attempt],
        replace_existing=True,
    )


def disarm(trigger_id: str) -> None:
    try:
        get_scheduler().remove_job(_job_id(trigger_id))
    except JobLookupError:
        pass


def restore_pending() -> list[asyncio.Task]:
    """Re-arm every stored trigger after a restart. One whose instant passed
    while the process was down runs now, once. Returns those runs' tasks so
    the caller can hold references (an unreferenced task can be collected)."""
    now = datetime.now(timezone.utc)
    past_due = []
    for trigger in store.all_triggers():
        if trigger.when.instant > now:
            arm(trigger)
            logger.info("Restored trigger %s for %s", trigger.id, trigger.when.instant)
        else:
            past_due.append(asyncio.create_task(runner.run(trigger)))
            logger.info("Past-due trigger %s — running now", trigger.id)
    return past_due
