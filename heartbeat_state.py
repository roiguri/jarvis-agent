"""Code-owned heartbeat task state: HEARTBEAT.md parsing + last-run stamps.

Two concerns the agent never touches directly:

- ``parse_tasks()`` — a lenient read of the task definitions in HEARTBEAT.md
  (name + cadence). A task whose cadence can't be parsed surfaces with
  ``cadence=None`` so callers treat it as always due — a malformed line
  degrades to "let the model look at it", never to a silently dropped task.
- ``load_state()`` / ``stamp()`` — per-task last-run timestamps in
  ``/app/jarvis_data/heartbeat/state.json``, stamped only for the tasks the
  agent reported acting on (its ``heartbeat_respond`` ack). Writes are
  atomic; unknown task names are logged and skipped, never fatal.

The agent's own narrative notes (``heartbeat/<task>.md`` in the memory dir)
are unrelated to this module and stay agent-owned.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, time as dt_time, timedelta, timezone
from zoneinfo import ZoneInfo

import config

logger = logging.getLogger(__name__)

HEARTBEAT_PATH = os.path.join(config.MEMORY_DIR, "HEARTBEAT.md")
STATE_DIR = "/app/jarvis_data/heartbeat"
STATE_PATH = os.path.join(STATE_DIR, "state.json")

_ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")

# Slack for the cadence comparison. Ticks run exactly one interval apart,
# but the gate's clock is read at job start, so consecutive reads sit a few
# ms either side of the nominal interval — without slack, an every-1h task
# stamped at the previous tick misses ~half its checks by milliseconds.
# Must stay well under the smallest supported cadence (1h).
_CADENCE_GRACE = timedelta(seconds=60)

# Task header: "- **task-name** | every 24h | due: 06:00-22:00 | state: `heartbeat/x.md`"
_HEADER_RE = re.compile(r"^-\s*\*\*(?P<name>[^*]+?)\*\*(?P<rest>.*)$")
# Cadence: "every 1h" / "every 24h" / "every 7 days" / "every 2 hours"
_CADENCE_RE = re.compile(r"every\s+(?P<n>\d+)\s*(?P<unit>hours?|days?|h|d)\b", re.IGNORECASE)
# Optional due window field inside the header's "|"-separated segments.
_DUE_FIELD_RE = re.compile(r"due:\s*(?P<spec>[^|`]+)", re.IGNORECASE)
# Window spec: "[Day[,Day...] ]HH:MM-HH:MM" or "[Day[,Day...] ]HH:MM±Nh"
# (± also accepted as "+-" or "+/-" for ASCII-only editing).
_WINDOW_RE = re.compile(
    r"^(?:(?P<days>(?:mon|tue|wed|thu|fri|sat|sun)"
    r"(?:\s*,\s*(?:mon|tue|wed|thu|fri|sat|sun))*)\s+)?"
    r"(?P<h1>\d{1,2}):(?P<m1>\d{2})"
    r"(?:\s*-\s*(?P<h2>\d{1,2}):(?P<m2>\d{2})"
    r"|\s*(?:±|\+/-|\+-)\s*(?P<rad>\d+(?:\.\d+)?)\s*h)\s*$",
    re.IGNORECASE,
)
_DAY_NUM = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


@dataclass(frozen=True)
class DueWindow:
    """A time-of-day window (Israel time), optionally limited to weekdays.

    Exactly one of (``start``+``end``) or (``center``+``radius``) is set.
    An end at or before the start wraps past midnight.
    """

    days: frozenset[int] | None  # weekday numbers, None = every day
    start: dt_time | None = None
    end: dt_time | None = None
    center: dt_time | None = None
    radius: timedelta | None = None

    def is_open(self, now: datetime) -> bool:
        now = now.astimezone(_ISRAEL_TZ)
        # A window anchored to yesterday/today/tomorrow can contain `now`
        # (ranges that wrap midnight, ± radii that cross a day boundary).
        for day_offset in (-1, 0, 1):
            day = (now + timedelta(days=day_offset)).date()
            if self.days is not None and day.weekday() not in self.days:
                continue
            if self.center is not None:
                center = datetime.combine(day, self.center, tzinfo=_ISRAEL_TZ)
                if abs(now - center) <= self.radius:
                    return True
            else:
                start = datetime.combine(day, self.start, tzinfo=_ISRAEL_TZ)
                duration = (
                    datetime.combine(day, self.end, tzinfo=_ISRAEL_TZ) - start
                ) % timedelta(days=1)
                if not duration:
                    duration = timedelta(days=1)  # end == start: full day
                if start <= now < start + duration:
                    return True
        return False


def parse_window(spec: str) -> DueWindow | None:
    """A ``DueWindow`` from a ``due:`` spec string, or None if unparseable."""
    m = _WINDOW_RE.match(spec.strip())
    if not m:
        return None
    try:
        days = None
        if m.group("days"):
            days = frozenset(
                _DAY_NUM[d.strip().lower()] for d in m.group("days").split(",")
            )
        t1 = dt_time(int(m.group("h1")), int(m.group("m1")))
        if m.group("h2") is not None:
            t2 = dt_time(int(m.group("h2")), int(m.group("m2")))
            return DueWindow(days=days, start=t1, end=t2)
        return DueWindow(
            days=days, center=t1, radius=timedelta(hours=float(m.group("rad")))
        )
    except ValueError:
        return None


@dataclass(frozen=True)
class HeartbeatTask:
    name: str
    cadence: timedelta | None  # None = unparseable → treat as always due
    raw: str  # the header line as written
    due: str | None = None  # raw due: spec, if present
    window: DueWindow | None = None  # None = no (or unparseable) window → open


_parse_cache: tuple[str, float, list[HeartbeatTask]] | None = None  # (path, mtime, tasks)


def split_blocks(text: str) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """Split HEARTBEAT.md content into (preamble lines, task blocks).

    A block is a ``- **name**`` header line plus every following line up to
    the next header (or EOF). The preamble is everything before the first
    header. Pure text operation — no validation.
    """
    preamble: list[str] = []
    blocks: list[tuple[str, list[str]]] = []
    current: list[str] | None = None
    for line in text.splitlines():
        m = _HEADER_RE.match(line.strip())
        if m:
            current = [line]
            blocks.append((m.group("name").strip(), current))
        elif current is not None:
            current.append(line)
        else:
            preamble.append(line)
    return preamble, blocks


def parse_tasks(path: str = HEARTBEAT_PATH) -> list[HeartbeatTask]:
    """Task definitions from HEARTBEAT.md, cached by file mtime.

    Missing/unreadable file → empty list. Header lines are matched leniently;
    anything that isn't a ``- **name**`` line is ignored (prose, numbered
    steps, headings).
    """
    global _parse_cache
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return []
    if _parse_cache is not None and _parse_cache[0] == path and _parse_cache[1] == mtime:
        return _parse_cache[2]

    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        logger.warning("heartbeat_state: cannot read %s", path)
        return []

    tasks = parse_tasks_text(text)
    _parse_cache = (path, mtime, tasks)
    return tasks


def parse_tasks_text(text: str) -> list[HeartbeatTask]:
    """Task definitions from HEARTBEAT.md-format text (uncached).

    Same leniency as ``parse_tasks``; used to validate candidate content
    before it is written.
    """
    tasks: list[HeartbeatTask] = []
    seen: set[str] = set()
    for line in text.splitlines():
        m = _HEADER_RE.match(line.strip())
        if not m:
            continue
        name = m.group("name").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        rest = m.group("rest")
        cm = _CADENCE_RE.search(rest)
        cadence: timedelta | None = None
        if cm:
            n = int(cm.group("n"))
            unit = cm.group("unit").lower()
            cadence = timedelta(days=n) if unit.startswith("d") else timedelta(hours=n)
        else:
            logger.warning(
                "heartbeat_state: no parseable cadence for task %r — treating as always due",
                name,
            )
        due_raw: str | None = None
        window: DueWindow | None = None
        dm = _DUE_FIELD_RE.search(rest)
        if dm:
            due_raw = dm.group("spec").strip()
            window = parse_window(due_raw)
            if window is None:
                logger.warning(
                    "heartbeat_state: unparseable due window %r for task %r — "
                    "treating as always open",
                    due_raw,
                    name,
                )
        tasks.append(
            HeartbeatTask(
                name=name, cadence=cadence, raw=line.strip(), due=due_raw, window=window
            )
        )
    return tasks


def filter_heartbeat_md(text: str, due_names: list[str]) -> str:
    """HEARTBEAT.md content with only the named task blocks kept.

    The preamble (everything before the first task header) is preserved.
    Task blocks not in ``due_names`` are collapsed into one terse note naming
    them, so the model knows they exist and are simply not due — without
    paying for their full bodies. Unknown names in ``due_names`` are ignored.
    """
    preamble, blocks = split_blocks(text)
    keep = set(due_names)
    kept = [b for name, b in blocks if name in keep]
    omitted = [name for name, _ in blocks if name not in keep]

    out: list[str] = []
    if preamble:
        out.append("\n".join(preamble).rstrip())
    for block in kept:
        out.append("\n".join(block).rstrip())
    if omitted:
        out.append(
            f"({len(omitted)} other task(s) are not due this tick and are "
            f"omitted here: {', '.join(omitted)}. Do not act on them; the "
            f"full list lives in HEARTBEAT.md.)"
        )
    return "\n\n".join(p for p in out if p)


def load_state(path: str = STATE_PATH) -> dict:
    """The last-run map: ``{"last_run": {"<task>": "<iso8601>", ...}}``.

    Missing or corrupt file → a fresh empty map (every task then looks
    never-run, i.e. due — the safe direction).
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {"last_run": {}}
    except (OSError, json.JSONDecodeError):
        logger.warning("heartbeat_state: unreadable %s — starting fresh", path)
        return {"last_run": {}}
    if not isinstance(data, dict) or not isinstance(data.get("last_run"), dict):
        logger.warning("heartbeat_state: malformed %s — starting fresh", path)
        return {"last_run": {}}
    return data


def any_due(
    now: datetime | None = None,
    *,
    heartbeat_path: str = HEARTBEAT_PATH,
    state_path: str = STATE_PATH,
) -> tuple[bool, list[str] | None]:
    """Which tasks are cadence-due at ``now`` (default: now, UTC).

    Returns ``(anything_due, due_names)``. ``due_names`` is ``None`` when the
    answer is "unknown — run everything": an empty or unreadable HEARTBEAT.md
    must degrade to running the model with the full task list, never to
    silently skipping the tick.

    A task is due when its cadence has elapsed (never stamped, stamp
    unreadable, cadence unparseable, or ``now - last_run`` reaching the
    cadence less ``_CADENCE_GRACE``) AND its ``due:`` window — if it has a
    parseable one — is open at ``now``.
    """
    now = now or datetime.now(timezone.utc)
    tasks = parse_tasks(heartbeat_path)
    if not tasks:
        logger.warning(
            "heartbeat_state: no parseable tasks in %s — treating all as due",
            heartbeat_path,
        )
        return True, None
    last_run = load_state(state_path)["last_run"]

    due: list[str] = []
    for t in tasks:
        if t.window is not None and not t.window.is_open(now):
            continue
        cadence_due = False
        if t.cadence is None:
            cadence_due = True
        else:
            raw = last_run.get(t.name)
            if not raw:
                cadence_due = True
            else:
                try:
                    prev = datetime.fromisoformat(raw)
                    if prev.tzinfo is None:
                        prev = prev.replace(tzinfo=timezone.utc)
                    cadence_due = now - prev >= t.cadence - _CADENCE_GRACE
                except ValueError:
                    logger.warning(
                        "heartbeat_state: unreadable last_run for %r — treating as due",
                        t.name,
                    )
                    cadence_due = True
        if cadence_due:
            due.append(t.name)
    return bool(due), due


def stamp(
    task_names: list[str],
    when: datetime | None = None,
    *,
    heartbeat_path: str = HEARTBEAT_PATH,
    state_path: str = STATE_PATH,
) -> list[str]:
    """Record ``when`` (default: now, UTC) as last_run for the named tasks.

    Only names present in HEARTBEAT.md are stamped; unknown names are logged
    and skipped. Nothing valid → no write. The file is replaced atomically
    (tmp + os.replace). Returns the names actually stamped.
    """
    when = when or datetime.now(timezone.utc)
    known = {t.name for t in parse_tasks(heartbeat_path)}
    valid = [n.strip() for n in task_names if n and n.strip()]
    stamped = [n for n in valid if n in known]
    for unknown in set(valid) - set(stamped):
        logger.warning(
            "heartbeat_state: ack named unknown task %r — not stamping", unknown
        )
    if not stamped:
        return []

    state = load_state(state_path)
    for name in stamped:
        state["last_run"][name] = when.isoformat()

    state_dir = os.path.dirname(state_path)
    os.makedirs(state_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=state_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, state_path)
    except OSError:
        logger.exception("heartbeat_state: failed to write %s", state_path)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return []
    return stamped
