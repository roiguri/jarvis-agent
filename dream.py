"""The morning job: for yesterday, write the daily log and send the memory
suggestions — a model call and a background turn, neither sees the other's output. Runs
once a day; a process started after RUN_AT catches up until CATCH_UP_UNTIL.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, time, timedelta, timezone

import config
from timeutils import ISRAEL_TZ

logger = logging.getLogger(__name__)

# TODO(#176): when the review applies changes, reconsider the time: an
# overnight run would land them before the first chat of the day.
RUN_AT = time(8, 30)            # Israel time: yesterday is complete
CATCH_UP_UNTIL = time(12, 0)    # a process started after RUN_AT runs the missed job until then
STATE_PATH = os.path.join(config.DATA_DIR, "dream", "state.json")


async def run(day: date) -> tuple[str, str]:
    """The daily log and the memory suggestions for ``day``: each one's outcome."""
    import daily_log
    import memory_suggestions

    log = await daily_log.write(day)
    suggestions = await memory_suggestions.send(day)
    return log, suggestions


def _last_run() -> date | None:
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return date.fromisoformat(json.load(f)["last_run"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _mark_run(today: date) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"last_run": today.isoformat()}, f)
    os.replace(tmp, STATE_PATH)


def catch_up_due(now: datetime | None = None) -> bool:
    """True when this morning's run was missed and there is still time for it
    (a process started between RUN_AT and CATCH_UP_UNTIL)."""
    local = (now or datetime.now(timezone.utc)).astimezone(ISRAEL_TZ)
    return RUN_AT <= local.time() < CATCH_UP_UNTIL and _last_run() != local.date()


async def run_scheduled() -> None:
    """Yesterday's log and suggestions, once a day. A failed run is not
    retried; tomorrow's covers its own day."""
    today = datetime.now(timezone.utc).astimezone(ISRAEL_TZ).date()
    if _last_run() == today:
        return
    yesterday = today - timedelta(days=1)
    log, suggestions = await run(yesterday)
    _mark_run(today)
    logger.info("morning job for %s: daily log %s, suggestions %s", yesterday, log, suggestions)
