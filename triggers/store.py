"""Code-owned persistence for triggers: ``DATA_DIR/triggers/triggers.json``.

Writers are a user turn (creating or deleting a reminder) and the runner
(removing a trigger once it has fired), each on a worker thread in the same
process. One lock is held across every load→modify→save so each writer reads
fresh state; the atomic ``os.replace`` alone prevents a torn file, not a lost
update. One writer process per instance root is held procedurally (one
service unit per root), as for the memory writer.

Rows are kept as written and parsed on read, so a row this code can't read
is skipped with a warning but never dropped from the file by a later write.
"""

import json
import logging
import os
import tempfile
import threading
import time

import config
from triggers.model import Trigger

logger = logging.getLogger(__name__)

STORE_PATH = os.path.join(config.DATA_DIR, "triggers", "triggers.json")
_VERSION = 1

_LOCK = threading.Lock()


def _empty() -> dict:
    return {"version": _VERSION, "triggers": [], "gates": {}, "last_run": {}}


def _read() -> dict:
    try:
        with open(STORE_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = _empty()
    except json.JSONDecodeError:
        # Moved aside, never overwritten: a later write must not turn a
        # recoverable file into an empty one.
        aside = f"{STORE_PATH}.corrupt-{int(time.time())}"
        os.replace(STORE_PATH, aside)
        logger.exception("triggers store unreadable — moved to %s, starting empty", aside)
        data = _empty()
    data.setdefault("triggers", [])
    data.setdefault("gates", {})
    data.setdefault("last_run", {})
    return data


def _write(data: dict) -> None:
    dir_ = os.path.dirname(STORE_PATH)
    os.makedirs(dir_, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=dir_, delete=False, encoding="utf-8") as tmp:
        json.dump(data, tmp, indent=2)
        tmp_path = tmp.name
    os.replace(tmp_path, STORE_PATH)


def _parse(rows: list[dict]) -> list[Trigger]:
    out = []
    for row in rows:
        try:
            out.append(Trigger.from_dict(row))
        except (KeyError, TypeError, ValueError):
            logger.warning("skipping unreadable trigger row: %r", row)
    return out


def all_triggers() -> list[Trigger]:
    with _LOCK:
        return _parse(_read()["triggers"])


def get(trigger_id: str) -> Trigger | None:
    return next((t for t in all_triggers() if t.id == trigger_id), None)


def add(trigger: Trigger) -> None:
    with _LOCK:
        data = _read()
        data["triggers"].append(trigger.to_dict())
        _write(data)


def remove(trigger_id: str) -> bool:
    """Drop a trigger. Returns whether one was there."""
    with _LOCK:
        data = _read()
        kept = [r for r in data["triggers"] if r.get("id") != trigger_id]
        if len(kept) == len(data["triggers"]):
            return False
        data["triggers"] = kept
        _write(data)
        return True


def get_by_key(key: str) -> Trigger | None:
    return next((t for t in all_triggers() if t.key == key), None)


def with_key_prefix(prefix: str) -> list[Trigger]:
    return [t for t in all_triggers() if t.key and t.key.startswith(prefix)]


def gate_state(task: str):
    """The last committed state of a gated task's check, or None."""
    with _LOCK:
        return _read()["gates"].get(task)


def set_gate_state(task: str, state) -> None:
    commit_gate(task, state, [], [])


def commit_gate(task: str, state, add: list[Trigger], remove_ids: list[str]) -> bool:
    """A gate's outcome in one write: its new state plus the triggers its
    handler created and cancelled, so a crash can't leave half of it applied.
    Skips the write when nothing changed. Returns whether it wrote."""
    with _LOCK:
        data = _read()
        gone = set(remove_ids)
        rows = [r for r in data["triggers"] if r.get("id") not in gone]
        rows += [t.to_dict() for t in add]
        if rows == data["triggers"] and data["gates"].get(task) == state:
            return False
        data["triggers"] = rows
        data["gates"][task] = state
        _write(data)
        return True


def task_stamps() -> dict[str, str]:
    """Heartbeat tasks' last-run instants, ISO 8601, by task name."""
    with _LOCK:
        return dict(_read()["last_run"])


def stamp_tasks(names: list[str], when_iso: str) -> None:
    with _LOCK:
        data = _read()
        for name in names:
            data["last_run"][name] = when_iso
        _write(data)
