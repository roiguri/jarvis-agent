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
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

HEARTBEAT_PATH = "/app/jarvis_memory/HEARTBEAT.md"
STATE_DIR = "/app/jarvis_data/heartbeat"
STATE_PATH = os.path.join(STATE_DIR, "state.json")

# Task header: "- **task-name** | every 24h | state: `heartbeat/x.md`"
_HEADER_RE = re.compile(r"^-\s*\*\*(?P<name>[^*]+?)\*\*(?P<rest>.*)$")
# Cadence: "every 1h" / "every 24h" / "every 7 days" / "every 2 hours"
_CADENCE_RE = re.compile(r"every\s+(?P<n>\d+)\s*(?P<unit>hours?|days?|h|d)\b", re.IGNORECASE)


@dataclass(frozen=True)
class HeartbeatTask:
    name: str
    cadence: timedelta | None  # None = unparseable → treat as always due
    raw: str  # the header line as written


_parse_cache: tuple[str, float, list[HeartbeatTask]] | None = None  # (path, mtime, tasks)


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
            lines = f.read().splitlines()
    except OSError:
        logger.warning("heartbeat_state: cannot read %s", path)
        return []

    tasks: list[HeartbeatTask] = []
    seen: set[str] = set()
    for line in lines:
        m = _HEADER_RE.match(line.strip())
        if not m:
            continue
        name = m.group("name").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        cm = _CADENCE_RE.search(m.group("rest"))
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
        tasks.append(HeartbeatTask(name=name, cadence=cadence, raw=line.strip()))

    _parse_cache = (path, mtime, tasks)
    return tasks


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

    A task is due when it has never been stamped, its stamp is unreadable,
    its cadence is unparseable, or ``now - last_run >= cadence``.
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
        if t.cadence is None:
            due.append(t.name)
            continue
        raw = last_run.get(t.name)
        if not raw:
            due.append(t.name)
            continue
        try:
            prev = datetime.fromisoformat(raw)
        except ValueError:
            logger.warning(
                "heartbeat_state: unreadable last_run for %r — treating as due", t.name
            )
            due.append(t.name)
            continue
        if prev.tzinfo is None:
            prev = prev.replace(tzinfo=timezone.utc)
        if now - prev >= t.cadence:
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
