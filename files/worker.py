"""The background worker that writes each stored file's record.

store.add() queues a job in the same write as the file. The worker takes one due job at a time,
newest file first, and asks records.make() for the record. An attempt is counted before the model call, so a crash mid-call is retried later;
failures back off, and after the last one the job stays with its error and is not
retried. A file that has no bytes any more, or can't have a record, has its job
dropped. If another arrival of the same bytes already has the current record, it
is copied without a model call.

Each job runs in a fresh context, so its model call is never charged to a turn
that happened to start it. Between jobs the loop also collects unused bytes
hourly, so a deleted file leaves the disk within the hour.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import threading
from datetime import datetime, timedelta, timezone

from files import index, records, store

logger = logging.getLogger(__name__)

JOB_KIND = store.RECORD_JOB
BACKOFF_S = (60, 600, 3600, 6 * 3600, 24 * 3600)
MAX_ATTEMPTS = len(BACKOFF_S)
POLL_S = 60
# Unused bytes (a deleted file's) are collected this often, not only at startup.
COLLECT_EVERY_S = 3600
# A breath between jobs, so a back-fill never runs model calls back to back.
PAUSE_S = 2

_wake: asyncio.Event | None = None


def requeue_outdated(kind: str) -> int:
    """Queue (again) every stored `kind` file whose record is missing or older than
    the current version, failed jobs included. Returns how many were queued."""
    recorder = records.RECORDERS.get(kind)
    if recorder is None:
        raise ValueError(f"no record is made for {kind} files")
    now = index.now_iso()
    with index.write() as con:
        return con.execute(
            "INSERT INTO jobs (file_id, kind, next_at, created)"
            " SELECT f.id, ?, ?, ? FROM files f"
            " WHERE f.kind = ? AND f.state IN ('available', 'trash')"
            " AND NOT EXISTS (SELECT 1 FROM derived d WHERE d.file_id = f.id AND d.kind = ?"
            "                 AND d.version = ?)"
            " ON CONFLICT(file_id, kind) DO UPDATE SET attempts = 0, next_at = excluded.next_at,"
            " last_error = NULL",
            (JOB_KIND, now, now, kind, recorder.record_kind, recorder.version)).rowcount


def queue_status() -> dict:
    """Jobs still to do, and the ones that gave up (with their last error)."""
    given_up = index.rows("SELECT file_id, last_error FROM jobs WHERE attempts >= ? ORDER BY id",
                          (MAX_ATTEMPTS,))
    queued = index.rows("SELECT COUNT(*) AS n FROM jobs WHERE attempts < ?", (MAX_ATTEMPTS,))
    return {"queued": queued[0]["n"] if queued else 0, "given_up": len(given_up),
            "given_up_jobs": given_up}


def next_job() -> dict | None:
    found = index.rows(
        "SELECT j.* FROM jobs j JOIN files f ON f.id = j.file_id"
        " WHERE j.attempts < ? AND j.next_at <= ? ORDER BY f.created DESC LIMIT 1",
        (MAX_ATTEMPTS, index.now_iso()))
    return found[0] if found else None


def run_one(job: dict) -> str:
    """Do one job. Returns what happened: made, copied, dropped or failed."""
    file_id = job["file_id"]
    retry_at = datetime.now(timezone.utc) + timedelta(seconds=BACKOFF_S[min(job["attempts"],
                                                                           MAX_ATTEMPTS - 1)])
    with index.write() as con:
        con.execute("UPDATE jobs SET attempts = attempts + 1, next_at = ? WHERE id = ?",
                    (retry_at.isoformat(), job["id"]))

    rec = store.get(file_id)
    if rec is None or rec.state not in ("available", "trash"):
        _done(job, "dropped", f"the file is {rec.state if rec else 'unknown'}")
        return "dropped"
    if _copy_from_same_bytes(rec):
        _done(job, "copied")
        return "copied"
    try:
        record = records.make(rec)
    except records.NoRecord as e:
        _done(job, "dropped", str(e))
        return "dropped"
    except Exception as e:
        error = (str(e) if isinstance(e, records.RecordFailed) else f"{type(e).__name__}: {e}")[:500]
        with index.write() as con:
            con.execute("UPDATE jobs SET last_error = ? WHERE id = ?", (error, job["id"]))
        last = job["attempts"] + 1 >= MAX_ATTEMPTS
        logger.warning("file record failed for %s (attempt %d%s): %s", file_id,
                       job["attempts"] + 1, ", giving up" if last else "", error)
        return "failed"
    try:
        store.put_derived(rec.id, record.kind, record.content, producer=record.producer,
                          version=record.version)
    except store.FileUnavailable as e:
        _done(job, "dropped", str(e))
        return "dropped"
    _done(job, "made", f"{record.kind}, {len(record.content):,} characters")
    return "made"


def run_pending(limit: int | None = None) -> list[str]:
    """Do due jobs until none is left (or `limit`). Returns their outcomes."""
    outcomes = []
    while limit is None or len(outcomes) < limit:
        job = next_job()
        if job is None:
            break
        outcomes.append(run_one(job))
    return outcomes


def _copy_from_same_bytes(rec: store.FileRecord) -> bool:
    recorder = records.RECORDERS.get(rec.kind)
    if recorder is None:
        return False
    found = index.rows(
        "SELECT d.* FROM derived d JOIN files f ON f.id = d.file_id"
        " WHERE f.hash = ? AND f.id != ? AND d.kind = ? AND d.version = ? LIMIT 1",
        (rec.hash, rec.id, recorder.record_kind, recorder.version))
    if not found:
        return False
    twin = found[0]
    store.put_derived(rec.id, twin["kind"], twin["content"], producer=twin["producer"],
                      version=twin["version"])
    return True


def _done(job: dict, outcome: str, detail: str = "") -> None:
    with index.write() as con:
        con.execute("DELETE FROM jobs WHERE id = ?", (job["id"],))
    logger.info("file record %s for %s%s", outcome, job["file_id"], f" ({detail})" if detail else "")


# ---------------------------------------------------------------------------
# The service's loop
# ---------------------------------------------------------------------------

def start() -> asyncio.Task:
    """Start the worker on the running loop."""
    global _wake
    _wake = asyncio.Event()
    return asyncio.get_running_loop().create_task(_run_forever(_wake))


def kick() -> None:
    """Look for work now (after a turn), rather than at the next poll."""
    if _wake is not None:
        _wake.set()


async def _run_forever(wake: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    collected_at = loop.time()  # startup has just collected
    while True:
        if loop.time() - collected_at >= COLLECT_EVERY_S:
            collected_at = loop.time()
            try:
                removed = await asyncio.to_thread(store.collect_unused)
                if removed:
                    logger.info("file store: collected %d unused files", len(removed))
            except Exception:
                logger.exception("file store: collecting unused bytes failed")
        try:
            job = await asyncio.to_thread(next_job)
            if job is not None:
                await _in_own_thread(run_one, job)
                await asyncio.sleep(PAUSE_S)
                continue
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("file record worker: a job crashed")
            await asyncio.sleep(POLL_S)
            continue
        wake.clear()
        try:
            await asyncio.wait_for(wake.wait(), timeout=POLL_S)
        except asyncio.TimeoutError:
            pass


def _in_own_thread(fn, *args) -> asyncio.Future:
    """Run `fn` in a fresh context on a daemon thread: a stop never waits for a
    model call (the job, already counted, is retried after the restart)."""
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def settle(result, error):
        if not future.done():
            if error is None:
                future.set_result(result)
            else:
                future.set_exception(error)

    def target():
        try:
            outcome = (contextvars.Context().run(fn, *args), None)
        except BaseException as e:
            outcome = (None, e)
        try:
            loop.call_soon_threadsafe(settle, *outcome)
        except RuntimeError:
            pass  # the loop closed while the job ran: the service is stopping

    threading.Thread(target=target, name="file-record", daemon=True).start()
    return future
