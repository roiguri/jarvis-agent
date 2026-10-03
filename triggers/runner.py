"""Executes a fired trigger.

``send`` delivers fixed text through the Outbox, with no model. A trigger is
removed from the store only after a successful send; a failed send is retried
a few times, and the stored trigger survives a restart either way.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from timeutils import ISRAEL_TZ
from triggers import store
from triggers.model import Trigger

logger = logging.getLogger(__name__)

# A send that fails is retried; past the cap the trigger is dropped so a
# permanently failing send can't reschedule itself forever.
_RETRY_DELAY = timedelta(minutes=5)
_MAX_RETRIES = 3
# Sent this late or later, the text says when it was meant for.
_LATE_AFTER = timedelta(seconds=60)


async def run(trigger: Trigger, attempt: int = 0) -> None:
    from gateway.factory import default_outbox
    from gateway.outbox import EVENT_REMINDER
    from triggers import scheduler

    text = trigger.action.text
    if datetime.now(timezone.utc) - trigger.when.instant > _LATE_AFTER:
        scheduled_local = trigger.when.instant.astimezone(ISRAEL_TZ).strftime("%H:%M Israel time")
        text = f"[Originally scheduled for {scheduled_local}]\n{text}"

    logger.info("Trigger: firing id=%s at=%s text=%r",
                trigger.id, trigger.when.instant.isoformat(), text[:80])
    outcome = await default_outbox().notify_owner(text, event=EVENT_REMINDER)
    if outcome.ok:
        await asyncio.to_thread(store.remove, trigger.id)
        logger.info("Trigger: id=%s sent and removed", trigger.id)
        return

    if attempt >= _MAX_RETRIES:
        logger.error("Trigger: id=%s undeliverable after %d retries (%s) — dropping",
                     trigger.id, attempt, outcome.error)
        await asyncio.to_thread(store.remove, trigger.id)
        return

    retry_at = datetime.now(timezone.utc) + _RETRY_DELAY
    scheduler.arm(trigger, run_date=retry_at, attempt=attempt + 1)
    logger.warning("Trigger: id=%s send failed (%s) — retry %d/%d at %s",
                   trigger.id, outcome.error, attempt + 1, _MAX_RETRIES,
                   retry_at.isoformat(timespec="seconds"))
