"""Memory suggestions: each morning Jarvis runs one background turn over
yesterday's conversation, every memory file, and a summary of other homes,
and sends the owner suggestions for the memory. The turn runs in the
"dream" scope with its usual tools; only its instruction (prompts/dream.md)
says to change nothing, and its final answer is the note. The owner
reads the suggestions and decides (he can answer in the conversation, and
Jarvis applies what he asks for with its memory tools).

The conversation is the owner thread's messages for that day — the owner's
messages, Jarvis's replies, conversational sends (heartbeats and reminders,
not one-way media alerts or earlier suggestion notes), and a compact one-line
trail of tool calls with their status (never tool result bodies). A day
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
PREFERENCES_FILE = "dream_preferences.md"
# TODO(#176): when the review applies changes, the note reports what changed:
# reword the title (in send) and this footer.
FOOTER = "Suggestions only — apply one only when Roi asks."

# TODO(#176): when the review applies changes, rewrite the prompt's "change
# nothing" and "suggest" wording.
PROMPT_PATH = os.path.join(os.path.dirname(__file__), "prompts", "dream.md")

READ_ONLY = {"SOUL.md"}
# Heartbeat's own files plus the owner's dreaming-preferences file (injected separately when non-empty).
EXCLUDED_FILES = {"HEARTBEAT.md", PREFERENCES_FILE}
EXCLUDED_DIRS = {"heartbeat", "daily"}
NOT_SHOWN = "(not shown: HEARTBEAT.md, heartbeat/ task notes, daily/ logs — the heartbeat's own files)"
# Already in the turn's system prompt; listed here, not repeated.
CORE_ORDER = ["SOUL.md", "USER.md", "MEMORY.md"]

TOOL_ARG_VALUE_CAP = 80
TOOL_ARGS_TOTAL_CAP = 140


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------

def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(), ISRAEL_TZ)
    return start, start + timedelta(days=1)


def _row_meta(row: dict) -> dict:
    raw = row.get("meta")
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


def _is_note(row: dict) -> bool:
    return _row_meta(row).get("job") == JOB


def _drop_sent(row: dict) -> bool:
    """Drop earlier suggestion notes and one-way broadcast notifications."""
    from gateway.outbox import EVENT_LLM_MEDIA, EVENT_MEDIA

    if _is_note(row):
        return True
    return _row_meta(row).get("event") in (EVENT_MEDIA, EVENT_LLM_MEDIA)


def _truncate(text: str, limit: int) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def _format_tool_args(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return _truncate(str(raw), TOOL_ARGS_TOTAL_CAP)
    if not isinstance(parsed, dict):
        return _truncate(str(raw), TOOL_ARGS_TOTAL_CAP)
    parts: list[str] = []
    for k, v in parsed.items():
        if isinstance(v, str):
            val = json.dumps(_truncate(v, TOOL_ARG_VALUE_CAP), ensure_ascii=False)
        else:
            val = _truncate(json.dumps(v, ensure_ascii=False, default=str), TOOL_ARG_VALUE_CAP)
        parts.append(f"{k}={val}")
    return _truncate(", ".join(parts), TOOL_ARGS_TOTAL_CAP)


def _format_tool_call(row: dict, result_status: dict[str, str]) -> str:
    import compaction

    name = row.get("tool_name") or "unknown"
    args_str = _format_tool_args(row.get("content"))
    cid = _row_meta(row).get("call_id")
    status = result_status.get(str(cid)) if cid else None
    suffix = f" -> {status}" if status else ""
    return f"[{compaction._hm(row['ts'])} tool] {name}({args_str}){suffix}"


def conversation(day: date, until: datetime | None = None) -> str | None:
    """The day's transcript, or None when the owner said nothing that day.
    One-way notifications and earlier suggestion notes are left out; tool calls
    appear as a compact one-line trail without tool result bodies."""
    import compaction
    from gateway.base import OWNER_THREAD_ID

    start, end = _day_bounds(day)
    if until is not None:
        end = min(end, until)
    raw_rows = compaction._span_rows(OWNER_THREAD_ID, start, end)

    result_status: dict[str, str] = {}
    for r in raw_rows:
        if r["role"] == "tool_result":
            meta = _row_meta(r)
            cid = meta.get("call_id")
            if cid:
                result_status[str(cid)] = "error" if meta.get("status") == "error" else "ok"

    kept = [
        r for r in raw_rows
        if r["role"] in ("owner", "jarvis", "tool_call")
        or (r["role"] == "jarvis_sent" and not _drop_sent(r))
    ]
    if not any(r["role"] == "owner" for r in kept):
        return None

    lines = [
        _format_tool_call(r, result_status) if r["role"] == "tool_call"
        else compaction._transcript([r])
        for r in kept
    ]
    return "\n".join(lines)


def _dream_preferences() -> str | None:
    """Owner-curated preferences for memory suggestions, or None if absent/empty."""
    path = os.path.join(config.MEMORY_DIR, PREFERENCES_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read().strip()
    except OSError:
        return None
    return text or None


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
    """The turn's input: the review instruction, the memory files, other homes,
    optional owner dreaming preferences, and the day's conversation."""
    import homes

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
    prefs = _dream_preferences()
    parts = [
        instruction,
        f"Today: {now.astimezone(ISRAEL_TZ):%A %Y-%m-%d}, Israel time.",
        "Memory files:\n" + "\n".join(listing) + f"\n  {NOT_SHOWN}",
        "\n\n".join(bodies),
        "Other homes (already tracked outside memory):\n" + homes.summarize(),
        (
            f"Owner preferences for memory suggestions (follow these; never suggest editing {PREFERENCES_FILE}):\n{prefs}"
            if prefs else ""
        ),
        f"Conversation on {day:%A %Y-%m-%d}:\n{transcript}",
    ]
    return "\n\n".join(p for p in parts if p)


def _is_nothing(text: str) -> bool:
    import turn_budget

    cleaned = text.strip()
    if cleaned.endswith(turn_budget.WRAPPED_UP_LINE):
        cleaned = cleaned[: -len(turn_budget.WRAPPED_UP_LINE)].strip()
    return not cleaned or cleaned.rstrip(".").lower() == NOTHING.rstrip(".").lower()


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
    if _is_nothing(text):
        logger.info("memory suggestions: nothing to suggest for %s", day)
        return "nothing"
    note = f"🧠 Memory suggestions — {day:%Y-%m-%d}\n\n{text}\n\n{FOOTER}"
    sent = await default_outbox().notify_owner(note, event=EVENT_MEDIA, metadata={"job": JOB})
    return "sent" if sent.ok else "failed"
