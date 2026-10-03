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

import config
from triggers.model import Trigger

logger = logging.getLogger(__name__)

STORE_PATH = os.path.join(config.DATA_DIR, "triggers", "triggers.json")
# Reminders lived here before triggers existed; migrated once, then renamed.
LEGACY_PATH = os.path.join(config.DATA_DIR, "scheduling", "scheduled_events.json")
_VERSION = 1

_LOCK = threading.Lock()


def _empty() -> dict:
    return {"version": _VERSION, "triggers": []}


def _read() -> dict:
    try:
        with open(STORE_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return _migrate_legacy()
    except json.JSONDecodeError:
        logger.exception("triggers store unreadable: %s — treating as empty", STORE_PATH)
        return _empty()
    data.setdefault("triggers", [])
    return data


def _write(data: dict) -> None:
    dir_ = os.path.dirname(STORE_PATH)
    os.makedirs(dir_, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=dir_, delete=False, encoding="utf-8") as tmp:
        json.dump(data, tmp, indent=2)
        tmp_path = tmp.name
    os.replace(tmp_path, STORE_PATH)


def _migrate_legacy() -> dict:
    """First read on an instance that still has the pre-triggers reminder file:
    convert its reminders, write the store, keep the old file as a backup.
    Called with no store on disk, so it runs at most once per instance."""
    data = _empty()
    try:
        with open(LEGACY_PATH, encoding="utf-8") as f:
            legacy = json.load(f)
    except FileNotFoundError:
        return data
    except json.JSONDecodeError:
        logger.exception("legacy reminder file unreadable: %s — not migrated", LEGACY_PATH)
        return data
    for event in legacy.get("events", []):
        if event.get("type") != "reminder":
            logger.warning("legacy event %r of type %r not migrated", event.get("id"), event.get("type"))
            continue
        data["triggers"].append({
            "id": event["id"],
            "when": {"at": event["fire_at"]},
            "action": {"send": {"text": event.get("text", "(reminder)")}},
        })
    _write(data)
    os.replace(LEGACY_PATH, LEGACY_PATH + ".migrated")
    logger.info("migrated %d reminder(s) from %s", len(data["triggers"]), LEGACY_PATH)
    return data


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
