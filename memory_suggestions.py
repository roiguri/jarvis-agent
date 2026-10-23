"""Memory suggestions: each morning Jarvis runs one background turn over
yesterday's conversation, every memory file and its own notes from the past
week, and sends the owner suggestions for the memory. The turn runs in the
"dream" scope with its usual tools; only its instruction (prompts/dream.md)
says to change nothing, and its final answer is the note. The owner
reads the suggestions and decides (he can answer in the conversation, and
Jarvis applies what he asks for with its memory tools).

The conversation is compaction's transcript of the owner thread — the owner's
messages, Jarvis's replies and Jarvis's own sends, tool traffic left out. A day
without an owner message runs no turn and sends nothing; neither does a turn
that finds nothing worth suggesting. The morning job (dream.py) runs it for
yesterday.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, time, timedelta, timezone

import config
from timeutils import ISRAEL_TZ

logger = logging.getLogger(__name__)

JOB = "memory_suggestions"          # names the send in its notification metadata
THREAD_ID = "dream"                 # tags the turn's records; the turn keeps no conversation
NOTHING = "Nothing to suggest."
PAST_NOTES_DAYS = 7
# TODO(#176): when the review applies changes, the note reports what changed:
# reword the title (in send) and this footer.
FOOTER = "Suggestions only — apply one only when Roi asks."

# TODO(#176): when the review applies changes, rewrite the prompt's "change
# nothing" and "suggest" wording, and the past-notes rule.
PROMPT_PATH = os.path.join(os.path.dirname(__file__), "prompts", "dream.md")

READ_ONLY = {"SOUL.md"}
# The heartbeat's own files: the task list, its notes, and the old daily logs.
EXCLUDED_FILES = {"HEARTBEAT.md"}
EXCLUDED_DIRS = {"heartbeat", "daily"}
NOT_SHOWN = "(not shown: HEARTBEAT.md, heartbeat/ task notes, daily/ logs — the heartbeat's own files)"
# Already in the turn's system prompt; listed here, not repeated.
CORE_ORDER = ["SOUL.md", "USER.md", "MEMORY.md"]


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------

def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(), ISRAEL_TZ)
    return start, start + timedelta(days=1)


def _is_note(row: dict) -> bool:
    try:
        return json.loads(row.get("meta") or "{}").get("job") == JOB
    except (ValueError, AttributeError):
        return False


def conversation(day: date, until: datetime | None = None) -> str | None:
    """The day's transcript, or None when the owner said nothing that day.
    Earlier suggestion notes are left out; they are shown on their own."""
    import compaction
    from gateway.base import OWNER_THREAD_ID

    start, end = _day_bounds(day)
    if until is not None:
        end = min(end, until)
    rows = [r for r in compaction._span_rows(OWNER_THREAD_ID, start, end)
            if r["role"] in ("owner", "jarvis", "jarvis_sent") and not _is_note(r)]
    if not any(r["role"] == "owner" for r in rows):
        return None
    return compaction._transcript(rows)


def past_notes(now: datetime) -> list[tuple[date, str]]:
    """The suggestion notes sent in the past week: (Israel date sent, text)."""
    from episodes import store

    since = (now - timedelta(days=PAST_NOTES_DAYS)).astimezone(timezone.utc).isoformat()
    rows = store.rows("SELECT ts, meta, content FROM episodes WHERE role = 'jarvis_sent' AND ts >= ? "
                      "ORDER BY ts, id", (since,))
    return [(datetime.fromisoformat(r["ts"]).astimezone(ISRAEL_TZ).date(), r["content"])
            for r in rows if _is_note(r)]


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


def build_instruction(day: date, now: datetime, transcript: str) -> str:
    """The turn's input: the review instruction, then the memory files, the
    past week's notes and the day's conversation."""
    with open(PROMPT_PATH, encoding="utf-8") as f:
        instruction = f.read().strip()
    files = _memory_files()
    width = max((len(rel) + (len(" (read-only)") if rel in READ_ONLY else 0) for rel, _, _ in files), default=0)
    listing = []
    for rel, content, changed in files:
        label = rel + (" (read-only)" if rel in READ_ONLY else "")
        where = "  (above)" if rel in CORE_ORDER else ""
        listing.append(f"  {label:<{width}}  {len(content):>6,} chars, changed {changed:%Y-%m-%d}{where}")
    bodies = [f"--- {rel} (changed {changed:%Y-%m-%d}) ---\n{content.strip()}"
              for rel, content, changed in files if rel not in CORE_ORDER]
    notes = past_notes(now)
    notes_text = "\n\n".join(f"--- sent {sent:%Y-%m-%d} ---\n{text.strip()}" for sent, text in notes) or "(none)"
    return "\n\n".join([
        instruction,
        f"Today: {now.astimezone(ISRAEL_TZ):%A %Y-%m-%d}, Israel time.",
        "Memory files:\n" + "\n".join(listing) + f"\n  {NOT_SHOWN}",
        "\n\n".join(bodies),
        f"Your notes from the past {PAST_NOTES_DAYS} days:\n{notes_text}",
        f"Conversation on {day:%A %Y-%m-%d}:\n{transcript}",
    ])


# ---------------------------------------------------------------------------
# The turn and the note
# ---------------------------------------------------------------------------

async def send(day: date) -> str:
    """Run the review for ``day`` and deliver its note. Returns what happened:
    "sent", "nothing" (no conversation, or nothing worth suggesting) or
    "failed" (the turn or the send failed)."""
    import asyncio

    import agent
    import heartbeat
    from gateway.factory import default_outbox
    from gateway.outbox import EVENT_MEDIA

    now = datetime.now(timezone.utc)
    transcript = conversation(day, until=now)
    if transcript is None:
        logger.info("memory suggestions: no conversation on %s, nothing sent", day)
        return "nothing"
    instruction = build_instruction(day, now, transcript)
    async with heartbeat.TURN_LOCK:
        outcome = await asyncio.to_thread(agent.ask_jarvis, instruction, THREAD_ID, scope="dream")
    if not outcome.finished:
        logger.error("memory suggestions for %s did not finish (%s): %s", day, outcome.kind, outcome.cause)
        return "failed"
    text = outcome.text.strip()
    if not text or text.rstrip(".").lower() == NOTHING.rstrip(".").lower():
        logger.info("memory suggestions: nothing to suggest for %s", day)
        return "nothing"
    note = f"🧠 Memory suggestions — {day:%Y-%m-%d}\n\n{text}\n\n{FOOTER}"
    sent = await default_outbox().notify_owner(note, event=EVENT_MEDIA, metadata={"job": JOB})
    return "sent" if sent.ok else "failed"
