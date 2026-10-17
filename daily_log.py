"""The daily log: one model call, the morning after, writes a short summary of
a finished day to daily/daily_YYYY-MM-DD.md in the memory dir. The heartbeat
reads yesterday's as its "yesterday"; /logs shows any day's.

The input is the day as the episode store holds it: what the heartbeat did
(the day view's background lines) and the owner conversation — the owner's
messages, Jarvis's replies and Jarvis's own sends, tool traffic left out. A
day where nothing happened writes nothing. Writing again replaces the file.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, time, timedelta, timezone

from langchain_core.messages import HumanMessage, SystemMessage

import config
from timeutils import ISRAEL_TZ

logger = logging.getLogger(__name__)

DAILY_DIR = os.path.join(config.MEMORY_DIR, "daily")
PROMPT_PATH = os.path.join(os.path.dirname(__file__), "prompts", "daily_log.md")
TIMEOUT_S = 180
MAX_TOKENS = 16384   # the model's thinking counts against it too

_llm = None


class Truncated(Exception):
    """The model stopped at the output cap; a cut log is not written."""


def path_for(day: date) -> str:
    return os.path.join(DAILY_DIR, f"daily_{day.isoformat()}.md")


def read(day: date) -> str | None:
    try:
        with open(path_for(day), encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def _model():
    global _llm
    if _llm is None:
        import agent
        from langchain_google_genai import ChatGoogleGenerativeAI

        _llm = ChatGoogleGenerativeAI(model=agent.llm.model, temperature=0, max_retries=1,
                                      timeout=TIMEOUT_S, max_tokens=MAX_TOKENS)
    return _llm


def build_input(day: date, until: datetime | None = None) -> str | None:
    """The day as the model reads it, or None when nothing happened."""
    import compaction
    from episodes import today as episodes_today
    from gateway.base import OWNER_THREAD_ID

    start = datetime.combine(day, time(), ISRAEL_TZ)
    end = start + timedelta(days=1)
    if until is not None:
        end = min(end, until)
    background = episodes_today._background_lines(start.astimezone(timezone.utc), end.astimezone(timezone.utc))
    rows = [r for r in compaction._span_rows(OWNER_THREAD_ID, start, end)
            if r["role"] in ("owner", "jarvis", "jarvis_sent")]
    if not (background or rows):
        return None
    return "\n\n".join([
        f"Day: {day:%A %Y-%m-%d}, Israel time.",
        "In the background (heartbeat checks and scheduled wakes):\n"
        + ("\n".join(f"- {line}" for line in background) or "(nothing)"),
        "Conversation:\n" + (compaction._transcript(rows) or "(none)"),
    ])


def compose(day: date, now: datetime | None = None) -> str | None:
    """The log for ``day``, or None when nothing happened that day. Raises if
    the model call fails or stops at the output cap."""
    import agent
    from observability import telemetry

    now = now or datetime.now(timezone.utc)
    user = build_input(day, until=now)
    if user is None:
        logger.info("daily log: nothing happened on %s, nothing written", day)
        return None
    with open(PROMPT_PATH, encoding="utf-8") as f:
        system = f.read()
    with telemetry.job("daily_log"):
        t0 = datetime.now(timezone.utc)
        try:
            response = _model().invoke([SystemMessage(content=system), HumanMessage(content=user)])
        except Exception as e:
            telemetry.record_llm_call(None, latency_ms=_ms_since(t0), error=f"{type(e).__name__}: {e}")
            raise
        reason = agent._finish_reason(response)
        telemetry.record_llm_call(response, latency_ms=_ms_since(t0), finish_reason=reason)
        if reason == "MAX_TOKENS":
            raise Truncated(f"daily log for {day} stopped at the output cap")
    text = _text(response.content).strip()
    if not text:
        raise ValueError(f"daily log for {day}: empty answer")
    return f"# Daily Log - {day:%A, %B} {day.day}, {day.year}\n\n{text}\n"


def _write(day: date, now: datetime | None = None) -> str:
    log = compose(day, now)
    if log is None:
        return "nothing"
    os.makedirs(DAILY_DIR, exist_ok=True)
    tmp = path_for(day) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(log)
    os.replace(tmp, path_for(day))
    return "written"


async def write(day: date) -> str:
    """Compose and save the log for ``day``. Returns what happened: "written",
    "nothing" (nothing happened that day) or "failed"."""
    import asyncio

    try:
        return await asyncio.to_thread(_write, day)
    except Exception:
        logger.exception("daily log for %s failed", day)
        return "failed"


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content or [])


def _ms_since(t0: datetime) -> int:
    return int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
