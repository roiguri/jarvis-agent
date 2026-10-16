"""Memory suggestions: each morning one model call reads yesterday's
conversation together with every memory file and sends the owner suggestions
for the memory. Nothing in memory changes; the owner reads them and decides
(he can answer in the conversation, and Jarvis applies what he asks for with
its own memory tools).

The input is compaction's transcript of the owner thread — the owner's
messages, Jarvis's replies and Jarvis's own sends, tool traffic left out —
plus the memory files the agent maintains. A day without an owner message
makes no model call and sends nothing; neither does an answer of "Nothing to
suggest."
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, time, timedelta, timezone

from langchain_core.messages import HumanMessage, SystemMessage

import config
from timeutils import ISRAEL_TZ

logger = logging.getLogger(__name__)

RUN_AT = time(8, 30)            # Israel time: yesterday is complete
CATCH_UP_UNTIL = time(12, 0)    # a process started after RUN_AT runs the missed job until then
NOTHING = "Nothing to suggest."
JOB = "memory_suggestions"          # names the send in its notification metadata
TIMEOUT_S = 180
MAX_TOKENS = 4096

STATE_PATH = os.path.join(config.DATA_DIR, "memory_suggestions", "state.json")
PROMPT_PATH = os.path.join(os.path.dirname(__file__), "prompts", "memory_suggestions.md")

READ_ONLY = {"SOUL.md"}
# The heartbeat's own files: the task list, its notes, and the old daily logs.
EXCLUDED_FILES = {"HEARTBEAT.md"}
EXCLUDED_DIRS = {"heartbeat", "daily"}
NOT_SHOWN = "(not shown: HEARTBEAT.md, heartbeat/ task notes, daily/ logs — the heartbeat's own files)"
CORE_ORDER = ["SOUL.md", "USER.md", "MEMORY.md"]

_llm = None


def _model():
    global _llm
    if _llm is None:
        import agent
        from langchain_google_genai import ChatGoogleGenerativeAI

        _llm = ChatGoogleGenerativeAI(model=agent.llm.model, temperature=0, max_retries=1,
                                      timeout=TIMEOUT_S, max_tokens=MAX_TOKENS)
    return _llm


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------

def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(), ISRAEL_TZ)
    return start, start + timedelta(days=1)


def conversation(day: date, until: datetime | None = None) -> str | None:
    """The day's transcript, or None when the owner said nothing that day."""
    import compaction
    from gateway.base import OWNER_THREAD_ID

    start, end = _day_bounds(day)
    if until is not None:
        end = min(end, until)
    rows = [r for r in compaction._span_rows(OWNER_THREAD_ID, start, end)
            if r["role"] in ("owner", "jarvis", "jarvis_sent")]
    if not any(r["role"] == "owner" for r in rows):
        return None
    return compaction._transcript(rows)


def _memory_files() -> list[tuple[str, str, date]]:
    """(path relative to the memory dir, content, last-changed Israel date)."""
    found = []
    for root, dirs, files in os.walk(config.MEMORY_DIR):
        rel_root = os.path.relpath(root, config.MEMORY_DIR)
        if rel_root == ".":
            dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS and not d.startswith(".")]
        else:
            dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            rel = os.path.normpath(os.path.join(rel_root, name))
            if not name.endswith((".md", ".txt")) or rel in EXCLUDED_FILES:
                continue
            path = os.path.join(root, name)
            try:
                with open(path, encoding="utf-8") as f:
                    content = f.read()
                changed = datetime.fromtimestamp(os.path.getmtime(path), timezone.utc).astimezone(ISRAEL_TZ).date()
            except (OSError, UnicodeDecodeError):
                logger.warning("memory suggestions: skipped unreadable %s", rel)
                continue
            found.append((rel, content, changed))
    rank = {name: i for i, name in enumerate(CORE_ORDER)}
    return sorted(found, key=lambda f: (rank.get(f[0], len(rank)), f[0]))


def build_input(day: date, today: date, transcript: str) -> str:
    files = _memory_files()
    width = max((len(rel) + (len(" (read-only)") if rel in READ_ONLY else 0) for rel, _, _ in files), default=0)
    listing = []
    for rel, content, changed in files:
        label = rel + (" (read-only)" if rel in READ_ONLY else "")
        listing.append(f"  {label:<{width}}  {len(content):>6,} chars, changed {changed:%Y-%m-%d}")
    bodies = [f"--- {rel} (changed {changed:%Y-%m-%d}) ---\n{content.strip()}" for rel, content, changed in files]
    return "\n\n".join([
        f"Today: {today:%A %Y-%m-%d}, Israel time.",
        "Memory files:\n" + "\n".join(listing) + f"\n  {NOT_SHOWN}",
        "\n\n".join(bodies),
        f"Conversation on {day:%A %Y-%m-%d}:\n{transcript}",
    ])


# ---------------------------------------------------------------------------
# The call and the note
# ---------------------------------------------------------------------------

def compose(day: date, now: datetime | None = None) -> str | None:
    """The note for ``day``, or None when there is nothing to send: no owner
    message that day, or the model found nothing worth suggesting. Raises if
    the model call fails."""
    from observability import telemetry

    now = now or datetime.now(timezone.utc)
    transcript = conversation(day, until=now)
    if transcript is None:
        logger.info("memory suggestions: no conversation on %s, nothing sent", day)
        return None
    with open(PROMPT_PATH, encoding="utf-8") as f:
        system = f.read()
    user = build_input(day, now.astimezone(ISRAEL_TZ).date(), transcript)
    with telemetry.job("memory_suggestions"):
        t0 = datetime.now(timezone.utc)
        try:
            response = _model().invoke([SystemMessage(content=system), HumanMessage(content=user)])
        except Exception as e:
            telemetry.record_llm_call(None, latency_ms=_ms_since(t0), error=f"{type(e).__name__}: {e}")
            raise
        telemetry.record_llm_call(response, latency_ms=_ms_since(t0))
    text = _text(response.content).strip()
    if not text or text.rstrip(".").lower() == NOTHING.rstrip(".").lower():
        logger.info("memory suggestions: nothing to suggest for %s", day)
        return None
    return f"🧠 Memory suggestions — {day:%Y-%m-%d}\n\n{text}"


async def send(day: date) -> str:
    """Compose and deliver the note for ``day``. Returns what happened:
    "sent", "nothing" (no conversation, or nothing worth suggesting) or
    "failed" (the model call or the send failed)."""
    import asyncio

    from gateway.factory import default_outbox
    from gateway.outbox import EVENT_MEDIA

    try:
        note = await asyncio.to_thread(compose, day)
    except Exception:
        logger.exception("memory suggestions for %s failed", day)
        return "failed"
    if note is None:
        return "nothing"
    outcome = await default_outbox().notify_owner(note, event=EVENT_MEDIA, metadata={"job": JOB})
    return "sent" if outcome.ok else "failed"


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------

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
    """The morning job: yesterday's suggestions, once a day. A failed run is
    not retried; tomorrow's covers its own day."""
    today = datetime.now(timezone.utc).astimezone(ISRAEL_TZ).date()
    if _last_run() == today:
        return
    outcome = await send(today - timedelta(days=1))
    _mark_run(today)
    logger.info("memory suggestions for %s: %s", today - timedelta(days=1), outcome)


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content or [])


def _ms_since(t0: datetime) -> int:
    return int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
